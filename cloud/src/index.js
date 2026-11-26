/**
 * HCA clip renderer (ported from Shockwave Made Clear): the Cloudflare Worker in front of the container.
 *
 * Two ways a render starts:
 *   - Supabase calls POST /run (with the shared secret) the moment a clip
 *     version is queued, so it starts within seconds.
 *   - Every minute, a scheduled check asks Supabase whether anything is
 *     queued or rendering, and if so wakes the container. That's the
 *     backstop, and it also keeps the container awake while a long queue
 *     renders. When nothing is waiting it does nothing, so the container
 *     sleeps (and isn't billed).
 *
 * The container (cloud/Dockerfile) gets its secrets as environment variables
 * from this Worker's secrets, which GitHub Actions sets on every deploy.
 */
import { Container, getContainer } from "@cloudflare/containers";

export class Renderer extends Container {
  defaultPort = 8080;
  sleepAfter = "3m";
  enableInternet = true;

  constructor(ctx, env) {
    super(ctx, env);
    this.envVars = {
      SUPABASE_URL: clean(env.SUPABASE_URL),
      SUPABASE_SECRET_KEY: clean(env.SUPABASE_SECRET_KEY),
      R2_ACCOUNT_ID: clean(env.R2_ACCOUNT_ID),
      R2_ACCESS_KEY_ID: clean(env.R2_ACCESS_KEY_ID),
      R2_SECRET_ACCESS_KEY: clean(env.R2_SECRET_ACCESS_KEY),
      R2_BUCKET: clean(env.R2_BUCKET),
      WORKER_ID: "cloud",
    };
  }
}

/** Secrets pasted into GitHub often carry a trailing newline or space. */
const clean = (v) => String(v ?? "").trim();

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** Is any clip waiting for, or in the middle of, a render? */
async function pending(env) {
  const r = await fetch(`${clean(env.SUPABASE_URL)}/rest/v1/clip_versions?select=id&status=in.(queued,rendering)&limit=1`, {
    headers: { apikey: clean(env.SUPABASE_SECRET_KEY) },
  });
  if (!r.ok) throw new Error(`Supabase answered ${r.status}`);
  return (await r.json()).length > 0;
}

/** Wake the one renderer container and ask it to work through the queue. */
async function wake(env) {
  const container = getContainer(env.RENDERER, "main");
  const r = await container.fetch(new Request("http://renderer/run", { method: "POST" }));
  return r.json();
}

function sameSecret(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === "/run" && request.method === "POST") {
      if (!sameSecret(clean(request.headers.get("x-trigger-secret")), clean(env.TRIGGER_SECRET))) return json({ error: "not allowed" }, 401);
      try { return json(await wake(env), 202); }
      catch (e) { return json({ error: String(e) }, 502); }
    }
    if (pathname === "/restart" && request.method === "POST") {
      // A running container keeps the settings it started with, so after the
      // secrets change the deploy workflow calls this to make the next render
      // start fresh. A render in progress is handed back to the queue.
      if (!sameSecret(clean(request.headers.get("x-trigger-secret")), clean(env.TRIGGER_SECRET))) return json({ error: "not allowed" }, 401);
      try { await getContainer(env.RENDERER, "main").stop(); return json({ restarted: true }); }
      catch (e) { return json({ restarted: false, note: String(e) }); }
    }
    if (pathname === "/diag" && request.method === "POST") {
      // What the Worker and the container can see: yes/no per setting, never values.
      if (!sameSecret(clean(request.headers.get("x-trigger-secret")), clean(env.TRIGGER_SECRET))) return json({ error: "not allowed" }, 401);
      const names = ["SUPABASE_URL", "SUPABASE_SECRET_KEY", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "TRIGGER_SECRET"];
      const worker = Object.fromEntries(names.map((n) => [n, !!clean(env[n])]));
      let container;
      try { container = await (await getContainer(env.RENDERER, "main").fetch(new Request("http://renderer/health"))).json(); }
      catch (e) { container = { error: String(e) }; }
      return json({ worker, container });
    }
    if (pathname === "/health") return json({ ok: true });
    return json({ error: "not found" }, 404);
  },

  async scheduled(event, env, ctx) {
    try {
      if (await pending(env)) ctx.waitUntil(wake(env));
    } catch (e) {
      console.log("scheduled check failed:", String(e));
    }
  },
};
