/**
 * /api/demo-login  -  the one team sign-in for the clip studio and approvals.
 *
 *   GET                -> { enabled: true | false }
 *   POST { role: ... } -> { access_token, refresh_token } for the shared
 *                         "HCA team" account (team@hcamadeclear.com, admin)
 *
 * HCA Made Clear has one login for the whole client team: the site password
 * (Netlify edge function) gets you in, and the studio then signs you in to
 * this shared Supabase account automatically. On while DEMO_PASSWORD is set
 * in Netlify to the team account's Supabase password; the password stays on
 * the server and never reaches the page. Approvals show as "HCA team".
 * Ported from RDN's demo-login; the path is kept so clips.js is unchanged.
 */
import { SUPABASE_URL, PUBLIC_KEY } from "../lib/review.mjs";

const TEAM = "team@hcamadeclear.com";
const ACCOUNTS = { marketer: TEAM, medaffairs: TEAM, admin: TEAM };

const reply = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });

export default async (req) => {
  const password = (process.env.DEMO_PASSWORD || "").trim();
  if (req.method === "GET") return reply({ enabled: !!password });
  if (req.method !== "POST") return reply({ error: "Use POST." }, 405);
  if (!password) return reply({ error: "Team sign-in is switched off." }, 404);

  let role = "";
  try { role = String((await req.json()).role || ""); } catch (e) { /* fall through */ }
  const email = ACCOUNTS[role];
  if (!email) return reply({ error: "Unknown role." }, 400);

  const r = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { apikey: PUBLIC_KEY, "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const j = await r.json().catch(() => null);
  if (!r.ok || !j || !j.access_token) return reply({ error: "The team account didn't sign in. Check DEMO_PASSWORD in Netlify matches the team account's password in Supabase." }, 502);
  return reply({ access_token: j.access_token, refresh_token: j.refresh_token });
};

export const config = { path: "/api/demo-login" };
