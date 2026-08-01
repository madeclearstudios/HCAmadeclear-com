// Password gate for the whole site.
// Set the SITE_PASSWORD environment variable in Netlify:
// Site configuration → Environment variables → Add → SITE_PASSWORD
import type { Context } from "https://edge.netlify.com";

const COOKIE_NAME = "site_auth";
const COOKIE_MAX_AGE = 60 * 60 * 24 * 30; // 30 days

async function sha256(text: string): Promise<string> {
  const data = new TextEncoder().encode(text);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export default async (request: Request, context: Context) => {
  const password = Deno.env.get("SITE_PASSWORD");

  // If no password is configured, don't lock everyone out — let traffic through.
  if (!password) return context.next();

  const expectedToken = await sha256(password);
  const cookies = request.headers.get("cookie") ?? "";
  const match = cookies.match(new RegExp(`${COOKIE_NAME}=([^;]+)`));

  // Already authenticated → continue to the site.
  if (match && match[1] === expectedToken) {
    return context.next();
  }

  const url = new URL(request.url);

  // Handle the login form submission.
  if (request.method === "POST" && url.pathname === "/__login") {
    const form = await request.formData();
    const attempt = String(form.get("password") ?? "");

    if (attempt === password) {
      const redirectTo = String(form.get("redirect") ?? "/");
      return new Response(null, {
        status: 302,
        headers: {
          Location: redirectTo.startsWith("/") ? redirectTo : "/",
          "Set-Cookie": `${COOKIE_NAME}=${expectedToken}; Path=/; Max-Age=${COOKIE_MAX_AGE}; HttpOnly; Secure; SameSite=Lax`,
        },
      });
    }

    return new Response(loginPage(url.pathname, true), {
      status: 401,
      headers: { "Content-Type": "text/html; charset=utf-8" },
    });
  }

  // Anything else while unauthenticated → show the login page.
  return new Response(loginPage(url.pathname + url.search, false), {
    status: 401,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
};

function loginPage(redirectTo: string, showError: boolean): string {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Sign in — HCA Healthcare UK × Made Clear</title>
<style>
  :root{
    --hca-navy:#17253F; --hca-denim:#05477F; --hca-white:#FFFFFF;
    --hca-cerulean-25:#BDDDEE; --hca-teal:#83C8C4;
    --hca-karbon:#2E2D2C; --hca-slate:#575756;
    --hca-chrome-ultralight:#E9E9E3;
    --font-primary:-apple-system,"Helvetica Neue",Helvetica,Arial,sans-serif;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{
    font-family:var(--font-primary);
    background:linear-gradient(160deg,var(--hca-navy) 0%,var(--hca-denim) 100%);
    min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;
  }
  .card{
    background:var(--hca-white);width:100%;max-width:420px;
    padding:48px 40px;border-radius:0 0 32px 0; /* curve: bottom-right only */
  }
  .brand{color:var(--hca-navy);font-weight:700;font-size:1.05rem;letter-spacing:-0.01em;margin-bottom:32px}
  h1{color:var(--hca-navy);font-size:1.6rem;letter-spacing:-0.02em;margin-bottom:8px}
  p{color:var(--hca-slate);font-size:0.95rem;line-height:1.5;margin-bottom:28px}
  label{display:block;color:var(--hca-karbon);font-weight:600;font-size:0.85rem;margin-bottom:8px}
  input[type=password]{
    width:100%;padding:13px 14px;font-size:1rem;font-family:inherit;
    border:1.5px solid var(--hca-chrome-ultralight);border-radius:6px;
    color:var(--hca-karbon);outline:none;
  }
  input[type=password]:focus{border-color:var(--hca-denim);box-shadow:0 0 0 3px var(--hca-cerulean-25)}
  button{
    width:100%;margin-top:20px;padding:14px;font-size:1rem;font-weight:600;font-family:inherit;
    color:var(--hca-white);background:var(--hca-denim);border:none;border-radius:6px;cursor:pointer;
  }
  button:hover{background:var(--hca-navy)}
  .error{
    background:#FBEAE5;color:#8a2f1d;font-size:0.9rem;
    padding:12px 14px;border-radius:6px;margin-bottom:20px;
  }
</style>
</head>
<body>
  <main class="card">
    <div class="brand">HCA Healthcare UK &times; Made Clear</div>
    <h1>This site is private</h1>
    <p>Enter the password to continue. If you don't have it, ask your project contact.</p>
    ${showError ? `<div class="error">That password isn't right. Please try again.</div>` : ``}
    <form method="POST" action="/__login">
      <input type="hidden" name="redirect" value="${redirectTo.replace(/"/g, "&quot;")}">
      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="current-password" autofocus required>
      <button type="submit">Continue</button>
    </form>
  </main>
</body>
</html>`;
}

export const config = { path: "/*" };
