# HCAmadeclear-com

Ask anything about our facilities. Every scene of every film, tagged and searchable. Ask a question the way a patient would.

## What this is

A searchable video knowledge base for HCA Healthcare UK's telesales and training team. HCA has 13 promotional films (one per hospital/facility). This tool runs each film through an AI vision pipeline that detects scene cuts, samples a frame per scene, and generates a description and tags for what's shown. That produces a searchable index across all films, so a rep can type a natural-language question and get back matching scene cards — thumbnail, timecode, description, tags — linking straight into the film at that timestamp.

Co-branded "HCA Made Clear": HCA's brand identity leads, with Made Clear credited alongside.

## Status

Early scaffold. One film (The Wellington) has placeholder scene structure in `data/films.json`; the rest are marked `awaiting_tagging`. Search is currently a simple client-side keyword filter over the JSON file — this will be replaced by a Supabase-backed database with full-text/embedding search.

## Stack (planned)

- **GitHub** — source code and (for now) film/scene data
- **Supabase** — films/scenes/tags tables, replacing the JSON file, plus natural-language search
- **Netlify** — build + hosting, deployed from this repo, with HCAmadeclear.com pointed at it via DNS

## Structure

- `index.html`, `styles.css`, `script.js` — front end
- `data/films.json` — placeholder film/scene data (temporary, pre-Supabase)
- `fonts/` — add licensed Mark for HCA `.otf` files here (not committed yet)
