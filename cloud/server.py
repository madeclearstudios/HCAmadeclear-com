#!/usr/bin/env python3
"""
The cloud renderer's front door, inside the Cloudflare container.

The Worker (cloud/src/index.js) wakes this container and POSTs /run when a
clip is queued. That starts pipeline/clip_worker.py --drain in the
background: it renders every queued clip, uploads each one, marks it awaiting
review and exits, reporting "sleeping". A /run while a drain is already going
just returns; the Worker's once-a-minute check keeps the container awake
until the queue is empty, then Cloudflare puts it to sleep.

  POST /run     start a drain if none is running   -> 202 {"running": true}
  GET  /health  is a drain running, which settings are set (yes/no), and a
                fingerprint of the renderer code                     -> 200
"""
import hashlib, json, os, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
lock = threading.Lock()
proc = None


def running():
    return proc is not None and proc.poll() is None


def start_drain():
    global proc
    with lock:
        if running():
            return False
        proc = subprocess.Popen([sys.executable, str(ROOT / "pipeline" / "clip_worker.py"), "--drain"], cwd=str(ROOT))
        return True


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path.split("?")[0] != "/run":
            return self.reply(404, {"error": "not found"})
        started = start_drain()
        self.reply(202, {"running": True, "started": started})

    def do_GET(self):
        if self.path.split("?")[0] != "/health":
            return self.reply(404, {"error": "not found"})
        # Which settings this container started with (yes/no only, never values),
        # and a fingerprint of the renderer code, to tell which image is running.
        names = ["SUPABASE_URL", "SUPABASE_SECRET_KEY", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "WORKER_ID"]
        code = hashlib.sha256((ROOT / "pipeline" / "clip_render.py").read_bytes()).hexdigest()[:12]
        self.reply(200, {"running": running(), "env": {n: bool(os.environ.get(n, "").strip()) for n in names}, "renderer": code})

    def log_message(self, fmt, *args):
        sys.stderr.write("[server] " + fmt % args + "\n")


if __name__ == "__main__":
    print("clip renderer listening on :8080", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
