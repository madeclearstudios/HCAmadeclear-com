/**
 * AI Triage of a clip version against the legal context library (a background
 * function: Netlify answers 202 at once, this runs for up to 15 minutes, and
 * the review page watches the ai_triage row for the result).
 *
 *   POST /.netlify/functions/triage-background
 *   Authorization: Bearer <the reviewer's Supabase sign-in token>
 *   { triage_id, removed: [{ from, to, text }] }   // words cut out between stretches
 *
 * The AI may use only the passages retrieved from legal_docs (the documents
 * listed in the Legal context box) plus the clip itself: no web search, no
 * outside sources. Every suggested revision cites passages by number, and
 * each quoted line is checked against that passage before it is shown.
 * The suggestion is advisory; the Medical Affairs reviewer decides.
 *
 * Needs ANTHROPIC_API_KEY and SUPABASE_SECRET_KEY in Netlify, plus the
 * Cloudflare keys Ask already uses (for the embeddings).
 */
import Anthropic from "@anthropic-ai/sdk";
import { jsonSchemaOutputFormat } from "@anthropic-ai/sdk/helpers/json-schema";
import { db, signedInReviewer, embed, vec } from "../lib/review.mjs";

const MODEL = "claude-opus-5-5";
const PER_QUERY = 14, MAX_PASSAGES = 40;

const SYSTEM = `You help HCA Healthcare UK's marketing team check short social media clips before someone on the team approves them. The clips are cut from HCA UK's own hospital films and B-roll: facilities, equipment, clinical teams and patients across HCA UK hospitals and outpatient centres. They are aimed at the public and prospective patients.

Base every judgement only on the numbered excerpts provided from the team's reference library (brand guidelines, advertising rules, consent and usage notes), and on the clip itself. Do not use outside knowledge of regulations or company policy, and do not assume facts the excerpts don't state. If the excerpts don't cover something you would need to check, say so under limitations instead of filling the gap.

Consider, as far as the excerpts allow: whether any identifiable patient, or any screen, chart, wristband or paperwork with readable patient information, appears in the clip; whether the shot descriptions suggest anything that breaks HCA's photography rules (for example stethoscopes worn round the neck, white coats outside lab settings, staff not bare below the elbow); whether any claims in words, straps or the end card are supported and not overstated (for example "best", "first", "only", outcome or waiting-time claims); whether facility names and people's names and titles are accurate; whether the end card and straps follow HCA's tone of voice (clear, purposeful, human); and whether the edit changes the meaning of anything said.

Verdicts: "approve" when you find no issue supported by the excerpts; "revise" when there are fixable issues; "block" when the clip should not be used at all according to the excerpts (for example a patient without consent on file). Every suggested revision must cite at least one excerpt by its number with a short verbatim quote from that excerpt. Write plainly, for a busy marketer. Your output is advice; the team makes the decision.`;

const SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["verdict", "summary", "checks", "revisions", "limitations"],
  properties: {
    verdict: { type: "string", enum: ["approve", "revise", "block"] },
    summary: { type: "string", description: "Two or three sentences: the recommendation and the main reason." },
    checks: {
      type: "array",
      items: {
        type: "object", additionalProperties: false, required: ["area", "ok", "finding"],
        properties: { area: { type: "string" }, ok: { type: "boolean" }, finding: { type: "string" } },
      },
    },
    revisions: {
      type: "array",
      items: {
        type: "object", additionalProperties: false, required: ["problem", "why", "suggested_change", "citations"],
        properties: {
          problem: { type: "string", description: "What in the clip is the issue, quoting the clip where useful." },
          why: { type: "string" },
          suggested_change: { type: "string" },
          citations: {
            type: "array",
            items: {
              type: "object", additionalProperties: false, required: ["excerpt", "quote"],
              properties: { excerpt: { type: "integer" }, quote: { type: "string", description: "A short verbatim quote from that excerpt." } },
            },
          },
        },
      },
    },
    limitations: { type: "string", description: "What the excerpts don't cover, or anything the reviewer should check by hand." },
  },
};

const norm = (s) => String(s || "").toLowerCase().replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/[^a-z0-9%.'"]+/g, " ").trim();

export default async (req) => {
  let triageId = null;
  try {
    const who = await signedInReviewer(req);
    if (!who) return;
    const body = await req.json();
    triageId = String(body.triage_id || "");
    const removed = Array.isArray(body.removed) ? body.removed.slice(0, 20).map((r) => ({ from: +r.from || 0, to: +r.to || 0, text: String(r.text || "").slice(0, 4000) })) : [];
    const [triage] = await db("GET", `ai_triage?select=id,status,version_id&id=eq.${encodeURIComponent(triageId)}`);
    if (!triage || triage.status !== "running") return;
    const [v] = await db("GET", `clip_versions?select=*,clips(title,film_title)&id=eq.${triage.version_id}`);
    if (!v || !v.words) throw new Error("This version hasn't rendered yet, so there are no words to check.");

    const docs = await db("GET", "legal_docs?select=id,title,issuer,doc_date,kind,status,pages&active=eq.true&order=created_at.asc");
    const indexed = docs.filter((d) => d.status === "indexed");
    if (!indexed.length) throw new Error("No legal context documents are indexed yet.");
    const byId = new Map(indexed.map((d) => [d.id, d]));

    // Three angles on the library: the clip's own words, the rules on promotional claims, and the product's approved use.
    const queries = [
      v.words,
      "Rules for healthcare advertising and social media: claims must be truthful, not misleading and substantiated; comparative and superlative claims; patient testimonials and consent.",
      "HCA Healthcare UK brand guidelines: tone of voice, logo use, colour, photography direction, clinical accuracy in imagery, facility names; patient consent, talent waivers and image usage restrictions.",
    ];
    const vectors = await embed(queries);
    const seen = new Map();
    for (const q of vectors) {
      const rows = await db("POST", "rpc/match_legal_passages", { query: vec(q), match_count: PER_QUERY });
      for (const r of rows) if (!seen.has(r.id) && byId.has(r.doc_id)) seen.set(r.id, r);
    }
    const passages = [...seen.values()].sort((a, b) => b.similarity - a.similarity).slice(0, MAX_PASSAGES);
    const numbered = passages.map((p, i) => ({ n: i + 1, ...p, doc: byId.get(p.doc_id) }));
    const excerpts = numbered.map((p) =>
      `<excerpt number="${p.n}" document="${p.doc.title}" pages="${p.page_from === p.page_to ? p.page_from : `${p.page_from}-${p.page_to}`}">\n${p.text}\n</excerpt>`).join("\n\n");

    const cuts = (v.cuts && v.cuts.length) ? v.cuts : [{ in: v.in_s, out: v.out_s }];
    const straps = (v.straps && v.straps.length) ? v.straps : (v.speaker ? [{ name: v.speaker, inst: v.role_line }] : []);
    const clip = [
      `Clip title (internal only, not shown in the video): ${v.clips?.title || ""}`,
      `Source film: ${v.clips?.film_title || ""}`,
      `Format: ${v.format}. Stretches kept from the film: ${cuts.length}.`,
      `Every word spoken in the clip, in order:\n${v.words}`,
      removed.length ? `Words cut out between the kept stretches (not in the clip):\n${removed.map((r, i) => `Cut ${i + 1}: ${r.text}`).join("\n")}` : "No words were cut out between stretches.",
      `Name straps on screen: ${straps.length ? straps.map((s) => `${s.name}${s.inst ? `, ${s.inst}` : ""}`).join("; ") : "none"}`,
      `End card: ${v.end_head || "Watch the full film"}${v.end_sub ? ` / ${v.end_sub}` : ""}`,
    ].join("\n\n");

    const client = new Anthropic();
    const response = await client.beta.messages.create({
      model: MODEL,
      max_tokens: 16000,
      betas: ["server-side-fallback-2026-07-01"],
      fallbacks: "default",
      output_config: { effort: "high", format: jsonSchemaOutputFormat(SCHEMA) },
      system: SYSTEM,
      messages: [{
        role: "user",
        content: `Excerpts from the legal context library (${numbered.length} passages retrieved from ${indexed.length} documents):\n\n${excerpts}\n\n---\n\nThe clip to triage:\n\n${clip}`,
      }],
    });
    if (response.stop_reason === "refusal") throw new Error("The AI declined to assess this clip. Review it by hand.");
    const text = response.content.filter((b) => b.type === "text").map((b) => b.text).join("");
    const out = JSON.parse(text);

    // Attach document and page to each citation, and check each quote really is in that passage.
    const byN = new Map(numbered.map((p) => [p.n, p]));
    for (const rev of out.revisions || []) {
      rev.citations = (rev.citations || []).map((c) => {
        const p = byN.get(c.excerpt);
        return p ? { ...c, document: p.doc.title, doc_id: p.doc_id, pages: p.page_from === p.page_to ? `${p.page_from}` : `${p.page_from}-${p.page_to}`,
                     verified: norm(p.text).includes(norm(c.quote)) && norm(c.quote).length > 8 }
                 : { ...c, document: null, verified: false };
      });
    }
    out.passages = numbered.map((p) => ({ n: p.n, document: p.doc.title, doc_id: p.doc_id, pages: p.page_from === p.page_to ? `${p.page_from}` : `${p.page_from}-${p.page_to}` }));
    out.usage = response.usage;
    await db("PATCH", `ai_triage?id=eq.${triageId}`, {
      status: "done", verdict: out.verdict, result: out, model: response.model, finished_at: new Date().toISOString(),
      docs_used: indexed.map((d) => ({ id: d.id, title: d.title, issuer: d.issuer, kind: d.kind })),
    }, "return=minimal");
  } catch (e) {
    console.error("triage failed:", e);
    const msg = !(e instanceof Anthropic.APIError) ? String(e.message || e)
      : [401, 403].includes(e.status) ? `The AI service rejected the API key (${e.status}). Check ANTHROPIC_API_KEY in Netlify, then redeploy.`
      : `The AI service returned an error (${e.status}). Try again in a minute.`;
    if (triageId) await db("PATCH", `ai_triage?id=eq.${triageId}`, { status: "failed", error: msg.slice(0, 500), finished_at: new Date().toISOString() }, "return=minimal").catch(() => {});
  }
};
