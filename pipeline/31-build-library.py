#!/usr/bin/env python3
"""
31 - Build the Selects library for the site and the clip studio.

    python3 pipeline/31-build-library.py [--streams data/stream-ids.csv]

Reads data/shots/*.json (from 30-tag-shots.py) and writes:

  site/index.json            every Selects film with its shots, descriptions
                             and tags, plus its Cloudflare Stream id once
                             04-upload.py has run (data/stream-ids.csv)
  site/clipdata/<film>.json  what the clip studio picks from. In RDN these
                             are spoken sentences; here each shot is one
                             "sentence" whose text is its description, so a
                             marketer picks shots instead of lines. Shot
                             entries carry "kind": "shot" and their single
                             "word" is flagged visual, so the renderer never
                             burns a description in as a caption.

  site/thumbs/<film>/<nnn>.jpg  one small frame from the middle of each shot,
                             with --src pointing at the Selects folder (skips
                             thumbnails that already exist)

Re-run after 30 (new films or descriptions) and after 04 (new Stream ids).
"""
import argparse
import csv
import json
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parent.parent


def tag_list(s):
    tags = []
    tags += s.get("service_lines") or []
    tags += s.get("kit") or []
    if s.get("setting"):
        tags.append(s["setting"])
    for p in s.get("people") or []:
        if isinstance(p, dict) and p.get("role"):
            tags.append(p["role"])
    shot = s.get("shot") or {}
    if isinstance(shot, dict):
        tags += [v for v in (shot.get("size"), shot.get("movement")) if v]
    seen, out = set(), []
    for t in tags:
        k = str(t).strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def make_thumbs(rec, src):
    master = next(iter(src.rglob(rec["file"])), None)
    if not master:
        print(f"  no master for thumbnails: {rec['file']}")
        return
    out = REPO / "site" / "thumbs" / rec["id"]
    out.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(rec["shots"], 1):
        jpg = out / f"{i:03d}.jpg"
        if jpg.exists():
            continue
        t = (s["t"] + s["end"]) / 2
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(master), "-frames:v", "1",
                        "-vf", "scale=320:-2", "-q:v", "6", str(jpg)], check=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=str(REPO / "data" / "shots"))
    ap.add_argument("--streams", default=str(REPO / "data" / "stream-ids.csv"))
    ap.add_argument("--customer", default="", help="Cloudflare Stream customer code")
    ap.add_argument("--src", help="Selects folder, to make shot thumbnails")
    a = ap.parse_args()

    streams = {}
    sp = pathlib.Path(a.streams)
    if sp.exists():
        for row in csv.DictReader(sp.open()):
            streams[row.get("file") or row.get("filename")] = row.get("uid") or row.get("stream_id")

    films = []
    clipdir = REPO / "site" / "clipdata"
    clipdir.mkdir(parents=True, exist_ok=True)
    for f in sorted(pathlib.Path(a.shots).glob("*.json")):
        rec = json.loads(f.read_text())
        if a.src:
            make_thumbs(rec, pathlib.Path(a.src))
        shots = []
        for i, s in enumerate(rec["shots"], 1):
            shots.append({
                "n": i, "t": s["t"], "end": s["end"],
                "desc": s.get("description") or "",
                "tags": tag_list(s),
                "text": s.get("text_on_screen") or "",
                "patient": bool(s.get("patient_visible")),
                "screenData": bool(s.get("screen_data_visible")),
                "social": s.get("social_ready"),
            })
        films.append({
            "id": rec["id"], "title": rec["title"], "file": rec["file"],
            "facility": rec.get("facility") or "", "duration": rec["duration"],
            "streamId": streams.get(rec["file"]), "kind": "selects",
            "chapters": [], "shots": shots,
        })
        sentences = [{
            "kind": "shot", "t": s["t"], "end": s["end"],
            "text": s["desc"] or f"Shot {s['n']}",
            "tags": s["tags"], "patient": s["patient"], "screenData": s["screenData"],
            "w": [[f"[shot {s['n']}]", s["t"], s["end"], 1]],
        } for s in shots]
        (clipdir / f"{rec['id']}.json").write_text(
            json.dumps({"film": rec["id"], "sentences": sentences}, separators=(",", ":")))

    idx = {"collection": "HCA Why HCA 2026 Selects", "stream": {"customerCode": a.customer}, "films": films}
    (REPO / "site" / "index.json").write_text(json.dumps(idx, separators=(",", ":")))
    described = sum(1 for f in films for s in f["shots"] if s["desc"])
    total = sum(len(f["shots"]) for f in films)
    print(f"{len(films)} films, {total} shots ({described} described), "
          f"{sum(1 for f in films if f['streamId'])} with Stream ids")


if __name__ == "__main__":
    main()
