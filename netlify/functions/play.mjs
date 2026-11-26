/**
 * /api/play?id=<stream uid>  -  a short-lived pass to watch one film.
 *
 * Every film on Cloudflare Stream was uploaded with "require signed URLs",
 * so a plain player link shows nothing. The page asks this function for a
 * token each time a film is opened, and the token goes where the video ID
 * would normally sit in the player address. Tokens expire after a few hours,
 * and playback is pinned to hcamadeclear.com as well, so a copied link is
 * useless both elsewhere and later.
 *
 * Two ways to sign, set in Netlify under Environment variables:
 *
 *   Preferred - a Stream signing key. Signs locally, no API call per play.
 *     STREAM_KEY_ID     the key "id"
 *     STREAM_KEY_PEM    the key "pem" exactly as Cloudflare returns it (base64)
 *   Create one with pipeline/08-create-signing-key.sh. Once only.
 *
 *   Fallback - ask Cloudflare for each token. Fine for a pilot.
 *     CF_ACCOUNT_ID     already set for /api/ask
 *     CF_STREAM_TOKEN   an API token with Stream: Edit
 */
import { createSign } from "node:crypto";

const HOURS = 4;
const UID = /^[a-f0-9]{32}$/;

const b64url = (buf) =>
  Buffer.from(buf).toString("base64").replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");

function signLocally(uid, keyId, pemB64) {
  const pem = Buffer.from(pemB64, "base64").toString("utf8");
  const header = b64url(JSON.stringify({ alg: "RS256", kid: keyId }));
  const payload = b64url(JSON.stringify({
    sub: uid,
    kid: keyId,
    exp: Math.floor(Date.now() / 1000) + HOURS * 3600,
  }));
  const signer = createSign("RSA-SHA256");
  signer.update(`${header}.${payload}`);
  return `${header}.${payload}.${b64url(signer.sign(pem))}`;
}

async function signViaApi(uid, account, apiToken) {
  const res = await fetch(
    `https://api.cloudflare.com/client/v4/accounts/${account}/stream/${uid}/token`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${apiToken}`, "Content-Type": "application/json" },
      body: JSON.stringify({ exp: Math.floor(Date.now() / 1000) + HOURS * 3600 }),
    }
  );
  const json = await res.json().catch(() => null);
  if (!res.ok || !json?.success) throw new Error(`token request failed, HTTP ${res.status}`);
  return json.result.token;
}

export default async (req) => {
  const reply = (body, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });

  const params = new URL(req.url).searchParams;
  const { STREAM_KEY_ID, STREAM_KEY_PEM, CF_ACCOUNT_ID, CF_STREAM_TOKEN } = process.env;

  // ?ids=a,b,c  -  many passes in one request, for the homepage's wall of
  // stills. Local signing only (no API call per film), at most 150 at a time.
  if (params.has("ids")) {
    const ids = [...new Set((params.get("ids") || "").split(",").map((s) => s.trim()))].filter((s) => UID.test(s)).slice(0, 150);
    if (!ids.length) return reply({ error: "Unknown films." }, 400);
    if (!(STREAM_KEY_ID && STREAM_KEY_PEM)) return reply({ error: "Batch passes need the signing key." }, 501);
    try {
      const tokens = Object.fromEntries(ids.map((id) => [id, signLocally(id, STREAM_KEY_ID, STREAM_KEY_PEM)]));
      return reply({ tokens, expiresInHours: HOURS });
    } catch (err) {
      return reply({ error: "Could not sign those films.", detail: String(err).slice(0, 200) }, 502);
    }
  }

  const uid = params.get("id") || "";
  if (!UID.test(uid)) return reply({ error: "Unknown film." }, 400);

  try {
    let token;
    if (STREAM_KEY_ID && STREAM_KEY_PEM) token = signLocally(uid, STREAM_KEY_ID, STREAM_KEY_PEM);
    else if (CF_ACCOUNT_ID && CF_STREAM_TOKEN) token = await signViaApi(uid, CF_ACCOUNT_ID, CF_STREAM_TOKEN);
    else return reply({ error: "Playback is not configured on the server." }, 500);
    return reply({ token, expiresInHours: HOURS });
  } catch (err) {
    return reply({ error: "Could not open that film.", detail: String(err).slice(0, 200) }, 502);
  }
};

export const config = { path: "/api/play" };
