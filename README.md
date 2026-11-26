# HCAmadeclear-com

hcamadeclear.com, for HCA Healthcare UK, built by Made Clear Studios.

- **Homepage** (`site/index.html`): the telesales training tool. 13 finished films (YouTube), 257 tagged scenes, ask a question the way a patient would.
- **Footage library and social clips** (`site/marketing.html`): every Why HCA 2026 Selects film, split into shots, each shot described and tagged (facility, service line, kit, people, setting, shot type) and flagged if a patient or patient data is in frame. The team searches, ticks shots, picks 9:16, 1:1 or 16:9, and renders a clip with HCA bands and end card.
- **Approvals** (`site/review.html`): every rendered clip gets a watermarked preview and a review link. The clean file downloads only once that exact version (by SHA-256) is approved. Optional AI check against the brand and usage rules the team uploads.
- **Fine edits** (`site/fine-edits.html`): anything the studio can't do goes to Made Clear with a Premiere XML.

Ported from RDN Made Clear (itself from Shockwave Made Clear) on 26 Nov 2026.

## One login

The whole site sits behind the Netlify password page (`SITE_PASSWORD`). Past that, the studio signs everyone in to one shared Supabase account, `team@hcamadeclear.com`, which has full access (search, clip, approve, download). Approvals show as "HCA team".

## Where things live

| What | Where |
| --- | --- |
| Site, functions | Netlify, publishes `site/` |
| Clips, versions, approvals, team login | Supabase project `hca-made-clear` (rlpidxfxazhqhvryiolh), migrations in `supabase/migrations` |
| Selects masters (1080p) | Dropbox: MCS PROJECTS 2026/HCA HEALTHCARE/WHY HCA 2026/EXPORTS/SELECTS, and R2 bucket `hca-masters` for the cloud renderer |
| Playback in the studio | Cloudflare Stream (hello@ account) |
| Renderer | Cloudflare Worker + container `hca-clip-renderer`, deployed by `.github/workflows/deploy-renderer.yml` |

## Pipeline (run from the repo folder on the Mac)

```bash
cd ~/Documents/GitHub/HCAmadeclear-com
SRC="/Users/mikeclear/Made Clear Studios Dropbox/MCS PROJECTS 2026/HCA HEALTHCARE/WHY HCA 2026/EXPORTS/SELECTS"
```

1. **Shots and tags** (resumable; skips Dropbox "(1)" duplicates):
   `export ANTHROPIC_API_KEY=...` then
   `caffeinate -i python3 pipeline/30-tag-shots.py "$SRC" data/shots`
   (`--cuts-only` finds shots without describing them; `--film Harborne` runs one film.)
2. **Upload to Stream** (once per new film):
   `export CF_ACCOUNT_ID=44f8bdda2dafeb8cbd86d910abcd04df CF_API_TOKEN=<pipeline token> ALLOWED_ORIGIN=hcamadeclear.com`
   `caffeinate -i python3 pipeline/04-upload.py "$SRC" data/stream-ids.csv`
3. **Masters to R2** for the cloud renderer: `sh pipeline/cloudflare-token-setup.sh` (once), then `python3 pipeline/20-upload-masters-r2.py "$SRC"`
4. **Build the library** (index, clip data, thumbnails):
   `python3 pipeline/31-build-library.py --src "$SRC" --customer <Stream customer code>`
5. Commit and push. Netlify redeploys the site; GitHub Actions redeploys the renderer.

## One-off setup

- **Supabase** (hca-made-clear): Authentication > Users > Add user: `team@hcamadeclear.com`, a strong password, auto-confirm. Authentication > URL configuration: Site URL `https://hcamadeclear.com`.
- **Netlify** environment variables: `SITE_PASSWORD` (exists), `DEMO_PASSWORD` (the team account's password), `SUPABASE_SECRET_KEY` (sb_secret_ for hca-made-clear), `CF_ACCOUNT_ID`, `CF_API_TOKEN` (Workers AI read, for the brand-rules check), `STREAM_KEY_ID`, `STREAM_KEY_PEM`, `CF_STREAM_TOKEN` (`sh pipeline/08-create-signing-key.sh`), `ANTHROPIC_API_KEY`.
- **GitHub** repo secrets for the renderer: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `SUPABASE_SECRET_KEY`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `TRIGGER_SECRET`. After the first deploy, in Supabase SQL: `select vault.create_secret('https://hca-clip-renderer.<sub>.workers.dev', 'renderer_url'); select vault.create_secret('<TRIGGER_SECRET>', 'renderer_trigger_secret');`
- **Brand**: the renderer and pages use system fonts for Mark for HCA and draw no logo. Put HCA's reversed lockup at `brand/hca-logo-white.png` (and Mark for HCA .otf files in `brand/fonts`) when HCA's Studio supplies them.
