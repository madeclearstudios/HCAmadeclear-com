#!/usr/bin/env python3
"""
The Mac render worker: picks up queued clips from Supabase, renders them from
the master files with clip_render.py, uploads the MP4 and its record, and
marks the version "awaiting review".

  python3 pipeline/clip_worker.py          # keep watching (Ctrl-C to stop)
  python3 pipeline/clip_worker.py --once   # do at most one job, then stop
  python3 pipeline/clip_worker.py --drain  # do every queued job, then stop (the cloud container)
  python3 pipeline/clip_worker.py --previews  # make watermarked previews for clips rendered before they existed

Needs the Supabase URL and secret key: from the environment (the cloud
container), or ~/.hca-worker.env, which pipeline/clip-worker-setup.sh
writes on the Mac (outside the repo, readable only by you).
The secret key bypasses every access rule, so it lives on this Mac only:
never in the site, the repo or Netlify.

One job at a time. A job is claimed by switching it from "queued" to
"rendering" only if it is still queued, so two workers can't take the same
one. If this Mac stops mid-render, the job goes back to the queue on the next
start (anything left "rendering" for over 15 minutes). If the master drive
isn't connected, jobs stay queued until it is.
"""
import argparse, json, os, re, signal, sys, tempfile, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clip_render  # noqa: E402

ENV_FILE = Path.home() / ".hca-worker.env"
BUCKET = "clips"
POLL = 5          # seconds between looks at the queue
STALE = 15        # minutes before a "rendering" job counts as abandoned


def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            k, _, v = line.strip().partition("=")
            if k and not k.startswith("#") and k not in os.environ:
                os.environ[k] = v.strip().strip('"')
    url, key = os.environ.get("SUPABASE_URL", "").rstrip("/"), os.environ.get("SUPABASE_SECRET_KEY", "")
    if not (url and key):
        sys.exit(f"✗ no Supabase URL or secret key. Run: sh pipeline/clip-worker-setup.sh")
    return url, key


class Supabase:
    def __init__(self, url, key):
        self.url = url
        self.headers = {"apikey": key}
        if key.startswith("eyJ"):  # legacy service_role JWT also wants a bearer header
            self.headers["Authorization"] = f"Bearer {key}"

    def _call(self, method, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {**self.headers, **({"Content-Type": "application/json"} if raw is None else {}), **(headers or {})}
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                text = r.read()
                return json.loads(text) if text else None
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path.split('?')[0]} → HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")

    def rest(self, method, table, query="", body=None, prefer="return=representation"):
        return self._call(method, f"/rest/v1/{table}?{query}", body, {"Prefer": prefer})

    def upload(self, path, file, content_type):
        return self.upload_to(BUCKET, path, file, content_type)

    def upload_to(self, bucket, path, file, content_type):
        return self._call("POST", f"/storage/v1/object/{bucket}/{urllib.parse.quote(path)}",
                          raw=Path(file).read_bytes(),
                          headers={"Content-Type": content_type, "x-upsert": "true", "Cache-Control": "3600"})

    def download(self, path, dest):
        req = urllib.request.Request(f"{self.url}/storage/v1/object/{BUCKET}/{urllib.parse.quote(path)}", headers=self.headers)
        with urllib.request.urlopen(req, timeout=300) as r:
            Path(dest).write_bytes(r.read())
        return Path(dest)


def now():
    return datetime.now(timezone.utc).isoformat()


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


WORKER_ID = os.environ.get("WORKER_ID", "studio-mac")   # "cloud" in the container


def beat(sb, state, detail=None):
    """Tell the studio this worker is alive (it shows online/offline from this)."""
    try:
        sb.rest("POST", "worker_status", "on_conflict=id",
                {"id": WORKER_ID, "state": state, "detail": detail, "last_seen": now()},
                prefer="resolution=merge-duplicates,return=minimal")
    except Exception as e:
        log(f"couldn't report status: {e}")


def requeue_stale(sb):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STALE)).isoformat()
    rows = sb.rest("PATCH", "clip_versions",
                   f"status=eq.rendering&claimed_at=lt.{urllib.parse.quote(cutoff)}", {"status": "queued"})
    for r in rows or []:
        log(f"put back in the queue: clip {r['clip_id']} v{r['version']} (left rendering)")


def next_job(sb):
    rows = sb.rest("GET", "clip_versions",
                   "select=*,clips(film_id,film_title,title)&status=eq.queued&order=created_at.asc&limit=1",
                   prefer="")
    return rows[0] if rows else None


def claim(sb, job):
    rows = sb.rest("PATCH", "clip_versions", f"id=eq.{job['id']}&status=eq.queued",
                   {"status": "rendering", "error": None, "claimed_at": now()})
    return bool(rows)


def master_ready(film_id):
    idx = json.loads((clip_render.ROOT / "site" / "index.json").read_text())
    film = next((f for f in idx["films"] if f["id"] == film_id), None)
    if not film:
        return False, f"no film {film_id} in site/index.json"
    try:
        clip_render.find_master(film)
        return True, ""
    except SystemExit as e:
        return False, str(e.code).splitlines()[0]


def run_job(sb, job, current):
    clip = job["clips"]
    label = f"“{clip['title']}” v{job['version']} {job['format']}"
    log(f"rendering {label}")
    current["id"] = job["id"]
    beat(sb, "rendering", job["id"])
    cuts = [(float(c["in"]), float(c["out"])) for c in (job.get("cuts") or [])] or None
    with tempfile.TemporaryDirectory(prefix="clipjob-") as td:
        args = SimpleNamespace(film=clip["film_id"], start=float(job["in_s"]), end=float(job["out_s"]), cuts=cuts,
                               format=job["format"], source_layout="interview",
                               straps=job.get("straps"),   # None on older versions: falls back to speaker/role
                               speaker=job.get("speaker"), role=job.get("role_line"),
                               end_head=job.get("end_head"), end_sub=job.get("end_sub"),
                               name=f"v{job['version']}-{job['format'].replace(':', 'x')}", out=td, quiet=True)
        try:
            record = clip_render.render(args)
        except SystemExit as e:  # clip_render stops with a message on a blocked topic, missing words, etc.
            reason = str(e.code).replace("✗ ", "").strip()
            sb.rest("PATCH", "clip_versions", f"id=eq.{job['id']}", {"status": "failed", "error": reason[:500]})
            log(f"failed {label}: {reason}")
            current["id"] = None; beat(sb, "idle")
            return
        except Exception as e:
            msg = re.sub(r"https?://\S+", "<film link>", f"render error: {e}")   # never store a signed link
            sb.rest("PATCH", "clip_versions", f"id=eq.{job['id']}", {"status": "failed", "error": msg[:500]})
            log(f"failed {label}: {e}")
            current["id"] = None; beat(sb, "idle")
            return

        base = f"{job['clip_id']}/{args.name}"
        mp4 = Path(td) / record["file"]
        sb.upload(f"{base}.mp4", mp4, "video/mp4")
        record["version"] = job["version"]
        record["clip"] = job["clip_id"]
        side = Path(td) / "record.json"
        side.write_text(json.dumps(record, indent=2))
        sb.upload(f"{base}.json", side, "application/json")
        preview_path = make_preview(sb, mp4, base)

    sb.rest("PATCH", "clip_versions", f"id=eq.{job['id']}", {
        "status": "awaiting_review", "storage_path": f"{base}.mp4", "preview_path": preview_path, "sha256": record["sha256"],
        "bytes": record["bytes"], "duration": record["duration"], "words": record["words"],
        "captions": record["captions"], "speaker": record["speaker"], "role_line": record["role"],
        "rendered_at": now(), "error": None})
    beat(sb, "idle")
    current["id"] = None
    log(f"ready for review: {label} ({record['bytes'] / 1e6:.1f} MB, {record['duration']}s)")


def make_preview(sb, mp4, base):
    """The watermarked copy the marketing team can watch before approval. A
    failure here never blocks the clip: it just goes to review without one."""
    try:
        out = clip_render.preview(mp4, Path(mp4).with_name("preview.mp4"))
        sb.upload(f"{base}-preview.mp4", out, "video/mp4")
        return f"{base}-preview.mp4"
    except Exception as e:
        log(f"no preview made: {e}")
        return None


def backfill_previews(sb):
    """Make previews for rendered versions that don't have one yet (clips made before previews existed)."""
    rows = sb.rest("GET", "clip_versions", "select=id,storage_path&storage_path=not.is.null&preview_path=is.null", prefer="")
    log(f"{len(rows)} version(s) need a preview")
    for r in rows:
        with tempfile.TemporaryDirectory(prefix="backfill-") as td:
            src = sb.download(r["storage_path"], Path(td) / "clip.mp4")
            path = make_preview(sb, src, r["storage_path"].removesuffix(".mp4"))
            if path:
                sb.rest("PATCH", "clip_versions", f"id=eq.{r['id']}", {"preview_path": path}, prefer="return=minimal")
                log(f"preview made: {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="do at most one job, then stop")
    ap.add_argument("--drain", action="store_true", help="do every queued job, then stop")
    ap.add_argument("--previews", action="store_true", help="make missing previews for clips already rendered, then stop")
    a = ap.parse_args()
    sb = Supabase(*load_env())
    if a.previews:
        return backfill_previews(sb)
    current = {"id": None}

    def stop(*_):
        if current["id"]:  # mid-render: hand the job back rather than leave it stuck
            try: sb.rest("PATCH", "clip_versions", f"id=eq.{current['id']}&status=eq.rendering", {"status": "queued"})
            except Exception: pass
            log("stopped mid-render; job returned to the queue")
        else:
            log("stopped")
        beat(sb, "stopped")
        sys.exit(0)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    log(f"watching {sb.url} for queued clips (every {POLL}s)")
    requeue_stale(sb)
    waiting_note = None
    while True:
        try:
            job = next_job(sb)
            if job:
                ok, why = master_ready(job["clips"]["film_id"])
                if not ok:
                    beat(sb, "waiting", why)
                    if why != waiting_note:
                        log(f"waiting: {why}")
                        waiting_note = why
                    if a.once or a.drain: return
                    time.sleep(30); continue
                waiting_note = None
                if claim(sb, job):
                    run_job(sb, job, current)
                if a.once: return
                continue
            if a.drain:
                beat(sb, "sleeping"); log("queue empty"); return
            beat(sb, "idle")
            if a.once:
                log("queue empty"); return
        except RuntimeError as e:
            log(f"Supabase error: {e}")
        except urllib.error.URLError as e:
            log(f"network: {e.reason}")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
