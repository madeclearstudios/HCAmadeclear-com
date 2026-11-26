#!/usr/bin/env python3
"""
20 - Copy the master films to Cloudflare R2, for the cloud clip renderer.

    python3 pipeline/20-upload-masters-r2.py            # upload anything missing
    python3 pipeline/20-upload-masters-r2.py --check    # list what's there, upload nothing

Uploads every film in site/index.json from the master folder (the WONDR drive,
in each film's own subfolder, or $CLIP_MASTERS) to the R2 bucket, under its
own filename (all 125 are unique). Big files go up
in 16 MB parts, three at a time. Resumable: a file already in the bucket at
the same size is skipped, so re-run it after any interruption.

Credentials come from ~/.hca-worker.env (sh pipeline/cloudflare-token-setup.sh):
R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and optionally R2_BUCKET
(default hca-masters).
"""
import argparse, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = Path.home() / ".hca-worker.env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        k, _, v = line.strip().partition("=")
        if k and not k.startswith("#") and k not in os.environ:
            os.environ[k] = v.strip().strip('"')
os.environ.setdefault("R2_BUCKET", "hca-masters")

sys.path.insert(0, str(ROOT / "pipeline"))
import clip_render  # noqa: E402  (shares the R2 client and the master folders)
from boto3.s3.transfer import TransferConfig  # noqa: E402


def human(n):
    return f"{n / 1e9:.2f} GB"


class Progress:
    def __init__(self, name, size):
        self.name, self.size, self.done, self.t0, self.last = name, size, 0, time.time(), 0

    def __call__(self, n):
        self.done += n
        now = time.time()
        if now - self.last > 2 or self.done >= self.size:
            rate = self.done / max(now - self.t0, 0.1)
            left = (self.size - self.done) / max(rate, 1)
            print(f"\r  {self.name[:44]:44} {self.done / self.size:6.1%}  {rate / 1e6:6.1f} MB/s  {left / 60:5.1f} min left ", end="", flush=True)
            self.last = now


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    clip_render.R2.update({k: os.environ.get(k, "") for k in clip_render.R2})
    missing = [k for k, v in clip_render.R2.items() if not v]
    if missing: sys.exit(f"✗ missing {', '.join(missing)}. Run: sh pipeline/cloudflare-token-setup.sh")
    bucket = clip_render.R2["R2_BUCKET"]
    s3 = clip_render.r2_client()
    have = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for o in page.get("Contents", []):
            have[o["Key"]] = o["Size"]

    films = json.loads((ROOT / "site" / "index.json").read_text())["films"]
    todo, total = [], 0
    for f in films:
        local = clip_render.local_master(f)
        size = local.stat().st_size if local else None
        state = "in R2" if f["file"] in have and (size is None or have[f["file"]] == size) else ("to upload" if local else "NOT FOUND locally")
        print(f"  {f['file'][:52]:52} {human(size or have.get(f['file'], 0)):>9}  {state}")
        if state == "to upload": todo.append((f["file"], local, size)); total += size
    if a.check or not todo:
        print(f"\n{len(have)} files in {bucket}; {len(todo)} to upload ({human(total)})."); return

    print(f"\nUploading {len(todo)} files, {human(total)} to {bucket}…")
    # 16 MB parts, three at a time: on a home connection, bigger parts in
    # parallel kept dropping (a reset loses the whole part).
    cfg = TransferConfig(multipart_threshold=16 * 1024 ** 2, multipart_chunksize=16 * 1024 ** 2, max_concurrency=3, use_threads=True)
    for name, path, size in todo:
        s3.upload_file(str(path), bucket, name, Config=cfg, Callback=Progress(name, size),
                       ExtraArgs={"ContentType": "video/mp4"})
        head = s3.head_object(Bucket=bucket, Key=name)
        ok = head["ContentLength"] == size
        print(f"\n  {'✓' if ok else '✗ size mismatch'} {name}")
        if not ok: sys.exit("Stopped: re-run to try that file again.")
    print("\nAll masters are in R2.")


if __name__ == "__main__":
    main()
