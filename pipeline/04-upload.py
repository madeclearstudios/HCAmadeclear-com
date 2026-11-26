#!/usr/bin/env python3
"""
03 - Upload to Cloudflare Stream, locked down on arrival.

    export CF_ACCOUNT_ID=...          # dashboard, right-hand side
    export CF_API_TOKEN=...           # custom token, Stream:Edit only
    export ALLOWED_ORIGIN=hcamadeclear.com
    python3 upload-v4.py "/Volumes/.../MEDTRONIC RDN" ~/rdn-stream-ids.csv

Two paths, because Cloudflare treats them differently:
  - under 190 MB: the simple upload, one request
  - 190 MB and over: the resumable (tus) protocol, in 100 MiB chunks

WHY THE SYMLINKS
Your filenames contain commas, curly quotes and invisible characters. curl's
form syntax reads a comma as a separator between multiple files, so
"Clip 1, Debbie Cohen....mp4" fails to open. Rather than fight the quoting,
each film is symlinked to a plain name for the duration of its upload and the
real title is set afterwards through the API as JSON, where punctuation
cannot cause trouble.

Every film arrives with signed URLs required and playback pinned to your
domain. Resumable at the library level: anything already in the CSV is
skipped, so stop and restart whenever you like.
"""
import base64
import csv
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ACCOUNT = os.environ.get("CF_ACCOUNT_ID", "").strip()
TOKEN   = os.environ.get("CF_API_TOKEN", "").strip()
ORIGIN  = os.environ.get("ALLOWED_ORIGIN", "").strip()
for name, val in (("CF_ACCOUNT_ID", ACCOUNT), ("CF_API_TOKEN", TOKEN), ("ALLOWED_ORIGIN", ORIGIN)):
    if not val:
        sys.exit(f"Set {name} first. See the comment at the top of this file.")

SRC = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").expanduser()
CSV_PATH = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "stream-ids.csv").expanduser()
API = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/stream"

SIMPLE_MAX = 190 * 1024 * 1024
CHUNK = 100 * 1024 * 1024
EXTS = {".mp4", ".mov", ".m4v"}

WORK = pathlib.Path(tempfile.mkdtemp(prefix="rdnup-"))


def b64(s):
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def curl(args, body=None, timeout=1800):
    """Run curl. Returns (status, headers, body_text)."""
    with tempfile.NamedTemporaryFile(suffix=".hdr", delete=False) as hf:
        hdr_path = hf.name
    try:
        proc = subprocess.run(
            ["curl", "-sS", "--http1.1", "-H", "Expect:", "-D", hdr_path, *args],
            input=body, capture_output=True, timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"curl: {proc.stderr.decode('utf-8', 'replace')[:400]}")

        status, headers = 0, {}
        with open(hdr_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.upper().startswith("HTTP/"):
                    parts = line.split()
                    if len(parts) > 1 and parts[1].isdigit():
                        status, headers = int(parts[1]), {}
                elif ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
        return status, headers, proc.stdout.decode("utf-8", "replace")
    finally:
        try:
            os.unlink(hdr_path)
        except OSError:
            pass


def set_title(uid, title):
    """Set the video's name afterwards, as JSON, where punctuation is safe."""
    payload = json.dumps({"meta": {"name": title}}).encode("utf-8")
    curl([
        "-X", "POST", f"{API}/{uid}",
        "-H", f"Authorization: Bearer {TOKEN}",
        "-H", "Content-Type: application/json",
        "--data-binary", "@-",
    ], body=payload, timeout=120)


def safe_link(path, ext):
    """A plain-named symlink curl can definitely open."""
    link = WORK / f"upload{ext}"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(path.resolve())
    return link


def upload_simple(path):
    link = safe_link(path, path.suffix.lower())
    status, _, body = curl([
        "-X", "POST", API,
        "-H", f"Authorization: Bearer {TOKEN}",
        "-F", f"file=@{link}",
        "-F", "requireSignedURLs=true",
        "-F", f"allowedOrigins[]={ORIGIN}",
    ])
    if status not in (200, 201):
        raise RuntimeError(f"HTTP {status}: {body[:400]}")
    try:
        uid = json.loads(body)["result"]["uid"]
    except Exception:                                  # noqa: BLE001
        raise RuntimeError(f"no uid in response: {body[:400]}")
    return uid


def upload_resumable(path, size):
    meta = f"name {b64(path.name)},requiresignedurls,allowedorigins {b64(ORIGIN)}"
    status, headers, body = curl([
        "-X", "POST", API,
        "-H", f"Authorization: Bearer {TOKEN}",
        "-H", "Tus-Resumable: 1.0.0",
        "-H", f"Upload-Length: {size}",
        "-H", f"Upload-Metadata: {meta}",
    ])
    if status not in (200, 201) or not headers.get("location"):
        raise RuntimeError(f"create, HTTP {status}: {body[:400]}")
    url = headers["location"]
    uid = headers.get("stream-media-id", "")

    chunk_file = WORK / "chunk.bin"
    with_auth = False
    offset = 0
    started = time.time()
    with path.open("rb") as fh:
        while offset < size:
            fh.seek(offset)
            data = fh.read(CHUNK)
            chunk_file.write_bytes(data)               # a real file, so curl sets Content-Length

            args = [
                "-X", "PATCH", url,
                "-H", "Tus-Resumable: 1.0.0",
                "-H", f"Upload-Offset: {offset}",
                "-H", "Content-Type: application/offset+octet-stream",
                "--data-binary", f"@{chunk_file}",
            ]
            if with_auth:
                args += ["-H", f"Authorization: Bearer {TOKEN}"]

            status, headers, body = curl(args)
            if status in (400, 401, 403) and not with_auth:
                with_auth = True
                continue
            if status not in (200, 204):
                raise RuntimeError(f"chunk at {offset}, HTTP {status}: {body[:400]}")

            offset = int(headers.get("upload-offset", offset + len(data)))
            pct = offset * 100 // size
            mb = offset / 1048576
            rate = mb / max(time.time() - started, 0.1)
            print(f"      {pct:3d}%   {mb:6.0f} MB   {rate:4.1f} MB/s", end="\r", flush=True)
    chunk_file.unlink(missing_ok=True)
    print(" " * 48, end="\r")
    return uid


def main():
    done = set()
    if CSV_PATH.exists():
        with CSV_PATH.open(newline="", encoding="utf-8") as fh:
            for row in csv.reader(fh):
                if row and row[0] != "filename":
                    done.add(row[0])
    else:
        with CSV_PATH.open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(["filename", "uid"])

    films = sorted(
        p for p in SRC.rglob("*")
        if p.is_file() and p.suffix.lower() in EXTS and not p.name.startswith("._")
    )
    if not films:
        sys.exit(f"No films found under {SRC}")

    todo = [p for p in films if p.name not in done]
    gb = sum(p.stat().st_size for p in todo) / 1073741824
    big = sum(1 for p in todo if p.stat().st_size >= SIMPLE_MAX)
    print(f"{len(films)} films, {len(done)} already uploaded, {len(todo)} to go ({gb:.1f} GB)")
    print(f"{len(todo) - big} by simple upload, {big} resumable")
    print()

    ok = fail = 0
    try:
        for i, path in enumerate(todo, 1):
            size = path.stat().st_size
            route = "resumable" if size >= SIMPLE_MAX else "simple"
            print(f"[{i}/{len(todo)}] {path.name}  ({size / 1048576:.0f} MB, {route})")
            try:
                uid = upload_resumable(path, size) if size >= SIMPLE_MAX else upload_simple(path)
                if route == "simple":
                    set_title(uid, path.stem)
            except Exception as e:                     # noqa: BLE001
                print(f"      FAILED: {e}")
                fail += 1
                if fail >= 3 and ok == 0:
                    print("\nThree failures and nothing through. Stopping rather than "
                          "working through the rest producing the same error.")
                    break
                continue

            with CSV_PATH.open("a", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow([path.name, uid])
            ok += 1
            print(f"      done   {uid}")
    finally:
        shutil.rmtree(WORK, ignore_errors=True)

    print()
    print(f"Uploaded: {ok}")
    print(f"Failed:   {fail}")
    print(f"Map:      {CSV_PATH}")
    if fail:
        print("\nRe-run the same command to retry. Successes are skipped.")


if __name__ == "__main__":
    main()
