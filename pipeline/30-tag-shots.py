#!/usr/bin/env python3
"""
30 - Split each Selects film into shots and describe every shot visually.

    export ANTHROPIC_API_KEY=...
    python3 pipeline/30-tag-shots.py "/path/to/Selects folder" data/shots

One JSON per film lands in data/shots/<film-id>.json. The script is resumable:
shots already described are skipped, so stop and restart whenever you like.
Use --film "Wilmslow" to run just one film while checking the output.

WHAT A SHOT IS
ffmpeg's scene detector finds the cuts. Anything shorter than 1.2 s is folded
into its neighbour, and anything longer than 12 s is split, so a long gimbal
walk down a corridor still gets separate descriptions for the reception, the
corridor and the ward it ends in.

WHAT GETS WRITTEN PER SHOT
Three frames (25 / 50 / 75 % through the shot) go to Claude as one strip.
Back comes a plain-English description plus tags in HCA's terms: service
line, kit, people, setting and shot type. Facility is NOT guessed from pixels:
it comes from the film's filename (see FACILITIES), because a corridor in
the Wellington looks like a corridor in the Lister.

Two safety flags are written for the marketing team, never hidden:
  patient_visible        a patient (not staff, not an actor-looking model) is in frame
  screen_data_visible    a monitor, chart or wristband with readable patient detail
These feed the "check waiver / do not use" view; nothing is auto-deleted.
"""
import argparse
import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

MODEL = os.environ.get("DESCRIBE_MODEL", "claude-opus-5-5")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

SCENE_THRESHOLD = 0.30
MIN_SHOT = 1.2
MAX_SHOT = 12.0
FRAME_WIDTH = 512
VIDEO_EXT = {".mp4", ".mov", ".mxf", ".m4v"}

# Filename fragment -> facility name as HCA writes it. First match wins.
FACILITIES = [
    ("wellington", "The Wellington Hospital"),
    ("princess grace", "The Princess Grace Hospital"),
    ("portland", "The Portland Hospital"),
    ("lister", "The Lister Hospital"),
    ("london bridge", "London Bridge Hospital"),
    ("harley street clinic", "The Harley Street Clinic"),
    ("harley street", "The Harley Street Clinic"),
    ("harborne", "The Harborne Hospital"),
    ("wilmslow", "The Wilmslow Hospital"),
    ("christie", "The Christie Private Care"),
    ("leaders in oncology", "Leaders in Oncology Care"),
    ("loc", "Leaders in Oncology Care"),
    ("shard", "HCA UK at The Shard"),
    ("canary wharf", "HCA UK Canary Wharf"),
    ("chiswick", "Chiswick Outpatient Centre"),
    ("golders green", "Golders Green Outpatient Centre"),
    ("elstree", "Elstree Outpatient Centre"),
    ("brentwood", "Brentwood Outpatient Centre"),
    ("university college", "University College Hospital"),
    ("iseh", "HCA UK at the Institute of Sport, Exercise and Health"),
    ("manchester", "HCA UK Manchester"),
    ("birmingham", "HCA UK Birmingham"),
]

SERVICE_LINES = [
    "cardiology", "oncology", "haematology", "orthopaedics", "spine", "neurology",
    "neurosurgery", "urology", "gynaecology", "fertility", "maternity", "paediatrics",
    "respiratory", "gastroenterology", "imaging and diagnostics", "critical care",
    "general surgery", "robotic surgery", "physiotherapy and rehabilitation",
    "sports medicine", "breast care", "plastic surgery", "ENT", "ophthalmology",
    "dermatology", "renal", "liver", "endocrinology", "pharmacy", "pathology",
    "radiotherapy", "international patients", "general hospital",
]

KIT_HINTS = [
    "MRI scanner", "CT scanner", "PET-CT", "X-ray", "ultrasound", "mammography",
    "cath lab", "da Vinci surgical robot", "Ion robotic bronchoscopy", "Mako robot",
    "linear accelerator", "endoscope", "operating theatre lights", "anaesthetic machine",
    "hydrotherapy pool", "gym / rehab equipment", "infusion chair", "hospital bed",
    "patient monitor", "lab analyser", "microscope", "pharmacy robot",
]

PROMPT = f"""You are tagging footage for HCA Healthcare UK's marketing asset library.
The image is ONE shot shown as three frames left to right (start, middle, end).
Facility for this film: {{facility}} (if "Several HCA sites", name the hospital in the description only when signage shows it). Film: {{film}}.

Return ONLY a JSON object with these keys:
"description": one or two plain sentences a marketer would search for. Say what we see and what is happening. No adjectives like stunning or state-of-the-art.
"service_lines": list, chosen from {json.dumps(SERVICE_LINES)} (empty if none is evident)
"kit": list of equipment visible, using these names where they fit: {json.dumps(KIT_HINTS)}; otherwise a short plain name
"people": list of objects {{{{"role": "consultant|surgeon|nurse|radiographer|physiotherapist|receptionist|porter|patient|visitor|staff (unclear)", "count": n}}}}
"setting": one of "exterior", "reception", "corridor", "ward", "patient room", "consulting room", "operating theatre", "imaging suite", "lab", "gym / rehab", "pool", "cafe / lounge", "office", "other"
"shot": object {{{{"size": "wide|medium|close-up|detail", "movement": "static|pan|tilt|push|gimbal|drone|handheld"}}}}
"text_on_screen": any legible signage or text, else ""
"patient_visible": true if someone who appears to be a patient is identifiable in frame
"screen_data_visible": true if a monitor, chart, wristband or paperwork shows readable patient information
"social_ready": true if this would work as a 3 to 10 second social cutaway (in focus, steady, nothing awkward)
"""


def die(msg):
    sys.exit(msg)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80]


def facility_for(name):
    low = name.lower()
    for frag, full in FACILITIES:
        if re.search(r"\b" + re.escape(frag) + r"\b", low):
            return full
    return "Several HCA sites"


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def detect_cuts(path):
    """Scene-change times in seconds. Runs on a 320px downscale so 4K is quick."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-vf", f"scale=320:-2,select='gt(scene,{SCENE_THRESHOLD})',showinfo",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]


def make_shots(cuts, duration):
    edges = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    shots = []
    for a, b in zip(edges, edges[1:]):
        if shots and (b - a) < MIN_SHOT:
            shots[-1][1] = b                      # fold a flash frame into the shot before
            continue
        shots.append([a, b])
    if len(shots) > 1 and shots[0][1] - shots[0][0] < MIN_SHOT:
        shots[1][0] = shots[0][0]
        shots.pop(0)
    out = []
    for a, b in shots:                            # split long takes
        n = max(1, int((b - a) // MAX_SHOT) + (1 if (b - a) % MAX_SHOT > MIN_SHOT else 0))
        step = (b - a) / n
        for i in range(n):
            out.append((round(a + i * step, 2), round(a + (i + 1) * step, 2)))
    return out


def frame_strip(path, start, end, tmp):
    """Three frames side by side as one JPEG, returned as base64."""
    span = end - start
    times = [start + span * f for f in (0.25, 0.5, 0.75)]
    files = []
    for i, t in enumerate(times):
        f = pathlib.Path(tmp) / f"f{i}.jpg"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}",
             "-i", str(path), "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-2",
             "-q:v", "4", str(f)], check=True)
        files.append(f)
    strip = pathlib.Path(tmp) / "strip.jpg"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(files[0]), "-i", str(files[1]), "-i", str(files[2]),
         "-filter_complex", "hstack=inputs=3", "-q:v", "4", str(strip)], check=True)
    return base64.b64encode(strip.read_bytes()).decode()


def describe(b64, facility, film):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 700,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": PROMPT.format(facility=facility, film=film)},
        ]}],
    }).encode()
    for attempt in range(5):
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body, method="POST",
            headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
            text = "".join(c.get("text", "") for c in data.get("content", []))
            m = re.search(r"\{.*\}", text, re.S)
            return json.loads(m.group(0))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                die("Anthropic rejected the API key (401). Check ANTHROPIC_API_KEY.")
            if e.code in (429, 500, 502, 503, 529):
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, AttributeError):
            time.sleep(5 * (attempt + 1))
    return {"description": "", "error": "describe failed after 5 tries"}


def run_film(path, out_dir):
    film_id = slug(path.stem)
    out = out_dir / f"{film_id}.json"
    rec = json.loads(out.read_text()) if out.exists() else None
    if not rec:
        duration = probe_duration(path)
        print(f"  finding cuts in {path.name} ({duration/60:.1f} min)…", flush=True)
        rec = {
            "id": film_id, "title": path.stem, "file": path.name,
            "facility": facility_for(path.stem + " " + path.parent.name),
            "duration": round(duration, 2),
            "shots": [{"t": a, "end": b} for a, b in make_shots(detect_cuts(path), duration)],
        }
        out.write_text(json.dumps(rec, indent=1))
    todo = [s for s in rec["shots"] if not s.get("description")]
    print(f"  {len(rec['shots'])} shots, {len(todo)} to describe", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        for i, s in enumerate(todo, 1):
            s.update(describe(frame_strip(path, s["t"], s["end"], tmp), rec["facility"], rec["title"]))
            if i % 10 == 0 or i == len(todo):
                out.write_text(json.dumps(rec, indent=1))
                print(f"    {i}/{len(todo)}", flush=True)
    out.write_text(json.dumps(rec, indent=1))
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="folder of Selects films (searched recursively)")
    ap.add_argument("out", help="where per-film JSON goes, e.g. data/shots")
    ap.add_argument("--film", help="only films whose name contains this")
    ap.add_argument("--cuts-only", action="store_true", help="find shots, skip descriptions")
    a = ap.parse_args()
    if not API_KEY and not a.cuts_only:
        die("Set ANTHROPIC_API_KEY first (or use --cuts-only).")
    src, out_dir = pathlib.Path(a.src), pathlib.Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    films = sorted(p for p in src.rglob("*") if p.suffix.lower() in VIDEO_EXT
                   and not p.name.startswith("."))
    # Dropbox duplicates ("Film (1).mp4" next to "Film.mp4") are skipped.
    names = {p.stem for p in films}
    films = [p for p in films
             if not (re.search(r" \(\d+\)$", p.stem) and re.sub(r" \(\d+\)$", "", p.stem) in names)]
    if a.film:
        films = [p for p in films if a.film.lower() in p.name.lower()]
    if not films:
        die(f"No films found under {src}")
    unknown = [p.name for p in films if facility_for(p.stem + " " + p.parent.name) == "Several HCA sites"]
    if unknown:
        print("No single facility in the name, tagged 'Several HCA sites' (add to FACILITIES if wrong):\n  " + "\n  ".join(unknown))
    for n, p in enumerate(films, 1):
        print(f"[{n}/{len(films)}] {p.name}", flush=True)
        if a.cuts_only:
            film_id = slug(p.stem)
            out = out_dir / f"{film_id}.json"
            if not out.exists():
                d = probe_duration(p)
                out.write_text(json.dumps({
                    "id": film_id, "title": p.stem, "file": p.name,
                    "facility": facility_for(p.stem + " " + p.parent.name),
                    "duration": round(d, 2),
                    "shots": [{"t": x, "end": y} for x, y in make_shots(detect_cuts(p), d)]},
                    indent=1))
            continue
        run_film(p, out_dir)


if __name__ == "__main__":
    main()
