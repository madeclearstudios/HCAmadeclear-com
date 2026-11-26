/**
 * Delete a clip the marketing team no longer wants: its versions, reviews,
 * AI triage results, fine-edit requests and every rendered file.
 *
 *   POST /.netlify/functions/delete-clip   { clip_id }
 *   Authorization: Bearer <the marketer's Supabase sign-in token>
 *
 * Marketing and Made Clear only. A clip with an approved version is kept, so
 * there's always a record of what Medical Affairs signed off; so is one that's
 * rendering right now (the renderer would write to it after it had gone).
 * Database rows go by cascade from the clip; files are removed with the
 * secret key, since marketers can't see the clean files to remove them.
 */
import { db, signedIn, storage } from "../lib/review.mjs";

const reply = (status, body) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export default async (req) => {
  if (req.method !== "POST") return reply(405, { error: "POST only" });
  try {
    const who = await signedIn(req, ["marketer", "admin"]);
    if (!who) return reply(401, { error: "Sign in as Marketing to delete clips." });
    const clipId = String((await req.json().catch(() => ({}))).clip_id || "");
    if (!/^[0-9a-f-]{36}$/.test(clipId)) return reply(400, { error: "Which clip?" });

    const [clip] = await db("GET", `clips?select=id,title,clip_versions(status)&id=eq.${clipId}`);
    if (!clip) return reply(404, { error: "That clip has already gone." });
    const statuses = clip.clip_versions.map((v) => v.status);
    if (statuses.includes("approved")) return reply(409, { error: "Approved clips are kept as the record of what Medical Affairs signed off." });
    if (statuses.includes("rendering")) return reply(409, { error: "This clip is rendering. Try again in a minute." });

    // Every file under the clip's folder: renders, previews and their records.
    const files = await storage("POST", "object/list/clips", { prefix: `${clipId}/`, limit: 1000 });
    const paths = (files || []).filter((f) => f.name).map((f) => `${clipId}/${f.name}`);
    if (paths.length) await storage("DELETE", "object/clips", { prefixes: paths });

    await db("DELETE", `clips?id=eq.${clipId}`, undefined, "return=minimal");
    console.log(`clip deleted by ${who.email}: ${clip.title} (${clipId}), ${paths.length} files`);
    return reply(200, { deleted: true, files: paths.length });
  } catch (e) {
    console.error("delete-clip failed:", e);
    return reply(500, { error: "The clip couldn't be deleted. Try again in a minute." });
  }
};

export const config = { path: "/api/delete-clip" };
