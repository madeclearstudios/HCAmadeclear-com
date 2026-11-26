/**
 * Index a legal context document for the AI triage (a background function:
 * Netlify answers 202 at once and lets this run for up to 15 minutes).
 *
 *   POST /.netlify/functions/legal-index-background   { doc_id }
 *   Authorization: Bearer <the reviewer's Supabase sign-in token>
 *
 * Splits the document's pages (legal_pages) into overlapping passages of
 * about 900 characters, each remembering the pages it came from, embeds them,
 * and stores them in legal_passages. The document's status goes
 * uploaded -> indexing -> indexed (or failed, with the reason).
 */
import { db, signedInReviewer, embed, vec } from "../lib/review.mjs";

const SIZE = 900, OVERLAP = 150;

/** Passages that never split mid-word and carry their page range. */
function passages(pages) {
  const out = [];
  let buf = "", from = null, to = null;
  const flush = () => { if (buf.trim().length > 40) out.push({ page_from: from, page_to: to, text: buf.trim() }); };
  for (const p of pages) {
    const words = p.text.replace(/\s+/g, " ").split(" ");
    for (const w of words) {
      if (from === null) from = p.page;
      to = p.page;
      buf += (buf ? " " : "") + w;
      if (buf.length >= SIZE) {
        flush();
        const tail = buf.slice(-OVERLAP);
        buf = tail.slice(tail.indexOf(" ") + 1);
        from = p.page;
      }
    }
  }
  flush();
  return out;
}

export default async (req) => {
  let docId = null;
  try {
    const who = await signedInReviewer(req);
    if (!who) return;
    docId = String((await req.json()).doc_id || "");
    const [doc] = await db("GET", `legal_docs?select=id,status&id=eq.${encodeURIComponent(docId)}`);
    if (!doc || doc.status === "indexing" || doc.status === "indexed") return;
    await db("PATCH", `legal_docs?id=eq.${docId}`, { status: "indexing", error: null }, "return=minimal");

    const pages = [];
    for (let from = 0; ; from += 1000) {   // PostgREST pages results; read them all
      const batch = await db("GET", `legal_pages?select=page,text&doc_id=eq.${docId}&order=page.asc&offset=${from}&limit=1000`);
      pages.push(...batch);
      if (batch.length < 1000) break;
    }
    const parts = passages(pages);
    if (!parts.length) throw new Error("No readable text in this document (is it a scanned image?).");
    const vectors = await embed(parts.map((p) => p.text));
    await db("DELETE", `legal_passages?doc_id=eq.${docId}`, undefined, "return=minimal");
    const rows = parts.map((p, i) => ({ doc_id: docId, ...p, embedding: vec(vectors[i]) }));
    for (let i = 0; i < rows.length; i += 200) await db("POST", "legal_passages", rows.slice(i, i + 200), "return=minimal");
    await db("PATCH", `legal_docs?id=eq.${docId}`, { status: "indexed", error: null }, "return=minimal");
  } catch (e) {
    console.error("legal index failed:", e);
    if (docId) await db("PATCH", `legal_docs?id=eq.${docId}`, { status: "failed", error: String(e.message || e).slice(0, 500) }, "return=minimal").catch(() => {});
  }
};
