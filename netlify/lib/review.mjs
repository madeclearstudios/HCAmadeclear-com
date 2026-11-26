/**
 * Shared by the legal-context indexing and AI triage functions.
 *
 * Supabase is called with the secret key (SUPABASE_SECRET_KEY, Netlify env),
 * which bypasses the database rules, so every function first checks the
 * caller's own sign-in token and role with signedInReviewer(). Embeddings use
 * the same Cloudflare Workers AI model as Ask (CF_ACCOUNT_ID, CF_API_TOKEN).
 */
export const SUPABASE_URL = "https://rlpidxfxazhqhvryiolh.supabase.co";
// The same public key the pages use (site/assets/clips.js). Filled in once the Supabase project is up.
export const PUBLIC_KEY = "sb_publishable_CuiSkBPUWAD0ft-jow7jbQ_nnmNbpeB";
const EMBED_MODEL = "@cf/baai/bge-small-en-v1.5";   // 384 dimensions, matches legal_passages.embedding

const secret = () => {
  const k = (process.env.SUPABASE_SECRET_KEY || "").trim();
  if (!k) throw new Error("SUPABASE_SECRET_KEY isn't set in Netlify.");
  return k;
};

/** Supabase REST with the secret key. */
export async function db(method, path, body, prefer = "return=representation") {
  const k = secret();
  const headers = { apikey: k, "Content-Type": "application/json", Prefer: prefer };
  if (k.startsWith("eyJ")) headers.Authorization = `Bearer ${k}`;
  const r = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await r.text();
  if (!r.ok) throw new Error(`${method} ${path.split("?")[0]}: HTTP ${r.status} ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : null;
}

/** The caller's profile, if their sign-in token is valid and their role is one of `roles`. */
export async function signedIn(req, roles) {
  const token = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (!token) return null;
  const r = await fetch(`${SUPABASE_URL}/auth/v1/user`, { headers: { apikey: PUBLIC_KEY, Authorization: `Bearer ${token}` } });
  if (!r.ok) return null;
  const user = await r.json();
  const [profile] = await db("GET", `profiles?select=id,email,name,role&id=eq.${user.id}`);
  return profile && roles.includes(profile.role) ? profile : null;
}

/** The caller's profile, if they're Medical Affairs or Made Clear. */
export const signedInReviewer = (req) => signedIn(req, ["medaffairs", "admin"]);

/** Supabase Storage with the secret key (list or remove a clip's files). */
export async function storage(method, path, body) {
  const k = secret();
  const headers = { apikey: k, "Content-Type": "application/json" };
  if (k.startsWith("eyJ")) headers.Authorization = `Bearer ${k}`;
  const r = await fetch(`${SUPABASE_URL}/storage/v1/${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await r.text();
  if (!r.ok) throw new Error(`storage ${method} ${path}: HTTP ${r.status} ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : null;
}

/** Embeddings for a list of texts, in batches. */
export async function embed(texts) {
  const account = (process.env.CF_ACCOUNT_ID || "").trim(), token = (process.env.CF_API_TOKEN || "").trim();
  if (!account || !token) throw new Error("CF_ACCOUNT_ID or CF_API_TOKEN isn't set in Netlify.");
  const out = [];
  for (let i = 0; i < texts.length; i += 50) {
    const r = await fetch(`https://api.cloudflare.com/client/v4/accounts/${account}/ai/run/${EMBED_MODEL}`, {
      method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ text: texts.slice(i, i + 50) }),
    });
    const j = await r.json().catch(() => null);
    if (!r.ok || !j?.success) throw new Error(`embedding failed: HTTP ${r.status}`);
    out.push(...j.result.data);
  }
  return out;
}

export const vec = (v) => `[${v.map((x) => +x.toFixed(6)).join(",")}]`;
