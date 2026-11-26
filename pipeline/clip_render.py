#!/usr/bin/env python3
"""
Render one social clip from a film, with ffmpeg. Ported from Shockwave Made Clear.

  python3 pipeline/clip_render.py --film labs-why-hca-2026-selects-gaps-closed --start 30 --end 40
  python3 pipeline/clip_render.py ... --cut 30-48.5 --cut 61-70     # several kept stretches
  python3 pipeline/clip_render.py ... --format 1:1          # 9:16 (default), 1:1, 16:9
  python3 pipeline/clip_render.py ... --source-layout centre # fixed centre crop, no face tracking

What it makes, in data/clip-test/ unless --out says otherwise:
  <name>.mp4   H.264 / AAC, 25 fps, loudness -14 LUFS, ready to post
  <name>.json  what went into it (film, timecodes, exact words, captions,
               file hash): the record Med Affairs approves against

How: the RDN films are interviews, congress talks and documentary, shot
16:9. For 9:16 and 1:1 the picture is cropped around the speaker: frames are
sampled four times a second, faces found with OpenCV, the film split into
shots where the picture changes, and each shot gets one fixed crop centred on
its main face (shots without a face are cropped in the middle). One crop per
shot, never a moving one, so it looks reframed rather than tracked. 16:9 keeps
the whole frame. A clip can be several kept stretches of one film: each is
read from the master on its own and they're joined with a very short audio
fade so the joins don't click. Captions appear word by word, the word being
spoken on a yellow highlight. Everything drawn on top (captions, name straps,
end card) is made here with Pillow as a series of transparent frames, because
Homebrew's ffmpeg has no text filters.

Words and their timings come from site/clipdata/<film>.json (pipeline 19);
titles and chapters from site/index.json. Masters are looked for on the
WONDR drive (in the film's own subfolder), then any folder in $CLIP_MASTERS,
then Cloudflare R2.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
CLIPDATA = ROOT / "site" / "clipdata"
# In the cloud the masters are in Cloudflare R2 (pipeline 20 uploads them);
# set these and find_master hands ffmpeg a short-lived signed link instead of
# a path. ffmpeg reads only the byte ranges it needs from that link.
R2 = {k: os.environ.get(k, "") for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")}
MASTER_DIRS = [Path(p) for p in os.environ.get("CLIP_MASTERS", "").split(":") if p] + [
    Path("/Users/mikeclear/Made Clear Studios Dropbox/MCS PROJECTS 2026/HCA HEALTHCARE/WHY HCA 2026/EXPORTS/SELECTS"),
    Path.home() / "mnt" / "EXPORTS" / "SELECTS",
]
FONTS = ROOT / "brand" / "fonts"
LOGO_PNG = ROOT / "brand" / "hca-logo-white.png"  # reversed HCA UK lockup from HCA's Studio; no logo is drawn until it's there
COMPLIANCE = json.loads((ROOT / "site" / "assets" / "compliance.json").read_text())

# HCA Healthcare UK brand (Brand Guidelines 2024): Navy leads, white base,
# Teal as the one highlight. The names YELLOW/BLACK are kept from the RDN
# renderer so the drawing code is unchanged: YELLOW is the highlight (Teal),
# BLACK the dark band (Navy). Fonts are still Borna/Clearface until HCA's
# Studio supplies Mark for HCA and Georgia Pro files for brand/fonts.
YELLOW, WHITE, BLACK = (131, 200, 196), (255, 255, 255), (23, 37, 63)
FPS, END_CARD = 25, 2.5
PAD_IN, PAD_OUT = 0.15, 0.30      # breathing room around the first and last word of each cut
END_HEAD = "hcahealthcare.co.uk"   # HCA CTA rule: never "www."
SRC_W, SRC_H = 1920, 1080          # every HCA Selects export is 1920x1080

# Output layouts. Boxes are x, y, w, h on the output canvas. "picture" is
# where the (cropped) film goes; "bands" are the flat colour blocks around it.
FORMATS = {
    "9:16": {"size": (1080, 1920), "picture": (0, 200, 1080, 1240),
             "bands": [((0, 0, 1080, 200), BLACK), ((0, 1440, 1080, 300), WHITE), ((0, 1740, 1080, 180), BLACK)],
             "logo": (64, 64, 300), "title": (64, 1782, 952), "caption": (64, 1462, 952, 256),
             "name": (0, 1392), "cap_px": 66},
    "1:1":  {"size": (1080, 1080), "picture": (0, 110, 1080, 680),
             "bands": [((0, 0, 1080, 110), BLACK), ((0, 790, 1080, 290), WHITE)],
             "logo": (48, 32, 240), "title": None, "caption": (48, 812, 984, 246),
             "name": (0, 750), "cap_px": 54},
    "16:9": {"size": (1920, 1080), "picture": (0, 0, 1920, 1080), "bands": [],
             "logo": None, "title": None, "caption": (120, 910, 1680, 130),
             "name": (0, 790), "cap_px": 56},
}
# B-roll (a clip made only of Selects shots, so no captions): the picture
# takes the caption band too. Same bands top and bottom, nothing else changes.
FORMATS["9:16 broll"] = {**FORMATS["9:16"], "picture": (0, 200, 1080, 1540),
                         "bands": [((0, 0, 1080, 200), BLACK), ((0, 1740, 1080, 180), BLACK)], "name": (0, 1660)}
FORMATS["1:1 broll"] = {**FORMATS["1:1"], "picture": (0, 110, 1080, 970),
                        "bands": [((0, 0, 1080, 110), BLACK)], "name": (0, 950)}
FORMATS["16:9 broll"] = dict(FORMATS["16:9"])
SOURCE_LAYOUTS = ("interview", "centre")


def font(name, px):
    return ImageFont.truetype(str(FONTS / name), px)


def display_title(title):
    """'RDN@ESC_ Prof. Parati & Prof. Pathak - Version 3' -> 'RDN@ESC Prof. Parati & Prof. Pathak'."""
    t = re.sub(r"\s*[-–]\s*(?:final\s+)?(?:version|vers\.?|rough cut|assembly|selects)\b.*$", "", title, flags=re.I)
    t = re.sub(r",?\s*Why HCA\s*\d{4}\b", "", t, flags=re.I)
    t = re.sub(r"\b(?:white|blue|no)\s+branding\b", "", t, flags=re.I)
    t = t.replace("_", " ")
    return re.sub(r"\s{2,}", " ", t).strip(" -,") or title


# ---------------------------------------------------------------- inputs
def r2_client():
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=f"https://{R2['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                        aws_access_key_id=R2["R2_ACCESS_KEY_ID"], aws_secret_access_key=R2["R2_SECRET_ACCESS_KEY"],
                        region_name="auto", config=Config(signature_version="s3v4", retries={"max_attempts": 3}))


def local_master(film):
    """The master on this machine (in its own subfolder, or loose), or None."""
    for d in MASTER_DIRS:
        for p in (d / (film.get("folder") or "") / film["file"], d / film["file"]):
            if p.exists():
                return p
    return None


def find_master(film):
    """A local path if the master is on this machine, else a signed R2 link.
    R2 keys are the bare filename (every RDN filename is unique)."""
    filename = film["file"]
    p = local_master(film)
    if p:
        return p
    if all(R2.values()):
        c = r2_client()
        try:
            c.head_object(Bucket=R2["R2_BUCKET"], Key=filename)
        except Exception as e:
            sys.exit(f"✗ master not in R2: {filename} ({e})")
        return c.generate_presigned_url("get_object", Params={"Bucket": R2["R2_BUCKET"], "Key": filename}, ExpiresIn=3600)
    unset = [k for k, v in R2.items() if not v]
    if len(unset) < len(R2):   # R2 half set up: say which settings are missing (names only)
        sys.exit(f"✗ master not found: {filename}. R2 isn't fully set up; missing {', '.join(unset)}")
    sys.exit(f"✗ master not found: {filename}\n  looked in: " + "\n             ".join(map(str, MASTER_DIRS)))


def check_compliance(film_id, cuts, text):
    for c in cuts:
        for r in COMPLIANCE.get("excludedRanges", []):
            if r["film"] == film_id and c["in"] < r["to"] and c["out"] > r["from"]:
                sys.exit(f"✗ blocked: overlaps an excluded range ({r['reason']})")
    for topic in COMPLIANCE.get("blockedTopics", []):
        for pat in topic["patterns"]:
            if re.search(pat, text, flags=re.I):
                sys.exit(f"✗ blocked topic in the words: {topic['label']}")


def load(film_id):
    idx = json.loads((ROOT / "site" / "index.json").read_text())
    film = next((f for f in idx["films"] if f["id"] == film_id), None)
    if not film: sys.exit(f"✗ no film {film_id} in site/index.json")
    src = CLIPDATA / f"{film_id}.json"
    if not src.exists(): sys.exit(f"✗ no {src.name} in site/clipdata. Run pipeline/19-clip-sentences.py")
    # A fourth element marks a visual "word": a whole Selects shot standing in
    # for a spoken line (pipeline 31). It cuts like a word but is never a caption.
    words = [{"w": w[0], "s": w[1], "e": w[2], "v": len(w) > 3 and bool(w[3])}
             for sent in json.loads(src.read_text())["sentences"] for w in sent["w"]]
    return film, words


def resolve_cuts(words, requested):
    """Each requested stretch keeps the words whose middle falls inside it; the
    cut then runs from just before its first word to just after its last."""
    cuts = []
    for lo, hi in sorted(requested):
        ws = [w for w in words if lo - 0.1 <= (w["s"] + w["e"]) / 2 <= hi + 0.1]
        if not ws: continue
        pin, pout = (0.0, 0.0) if ws[0].get("v") else (PAD_IN, PAD_OUT)   # shots cut on their own edges
        cin = max(ws[0]["s"] - pin, cuts[-1]["out"] if cuts else 0)
        cuts.append({"in": round(cin, 3), "out": round(ws[-1]["e"] + (0.0 if ws[-1].get("v") else pout), 3), "words": ws})
    if not cuts: sys.exit("✗ no words in the requested stretch")
    off = 0.0
    for c in cuts:
        c["offset"] = round(off, 3); c["len"] = round(c["out"] - c["in"], 3); off += c["len"]
        c["words"] = [{**w, "s": c["offset"] + w["s"] - c["in"], "e": c["offset"] + w["e"] - c["in"]} for w in c["words"]]
    return cuts, round(off, 3)


def cards_from(words, max_words=4, max_chars=26):
    """Short caption cards broken at punctuation; each word keeps its timing."""
    cards, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or len(" ".join(x["w"] for x in cur)) >= max_chars or w["w"][-1:] in ".,?!;:":
            cards.append(cur); cur = []
    if cur: cards.append(cur)
    return cards


# ---------------------------------------------------------------- reframing
SAMPLE_FPS, SAMPLE_W = 4, 480
SHOT_CHANGE = 28.0      # mean grey-level difference between samples that counts as a new shot


def reframe(master, cut, crop_w, layout):
    """Shots in one cut, each with the left edge of its crop: [(start, x), ...]
    in seconds from the cut's start. Centre crop when there's no clear face."""
    centre = (SRC_W - crop_w) // 2
    if layout == "centre":
        return [(0.0, centre)]
    import cv2, numpy as np
    sh = round(SAMPLE_W * SRC_H / SRC_W) // 2 * 2
    remote = str(master).startswith("http")
    net = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"] if remote else []
    raw = subprocess.run(["ffmpeg", "-v", "error", *net, "-ss", f"{cut['in']:.3f}", "-t", f"{cut['len']:.3f}", "-i", str(master),
                          "-vf", f"fps={SAMPLE_FPS},scale={SAMPLE_W}:{sh}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True).stdout
    n = len(raw) // (SAMPLE_W * sh)
    if not n:
        return [(0.0, centre)]
    frames = np.frombuffer(raw[:n * SAMPLE_W * sh], np.uint8).reshape(n, sh, SAMPLE_W)
    cascades = cv2.data.haarcascades
    front = cv2.CascadeClassifier(cascades + "haarcascade_frontalface_default.xml")
    side = cv2.CascadeClassifier(cascades + "haarcascade_profileface.xml")

    def faces(g):
        """Every face as (x, y, w, h) in sample pixels, profiles both ways."""
        found = []
        for det, flip in ((front, False), (side, False), (side, True)):
            img = cv2.flip(g, 1) if flip else g
            for (x, y, w, h) in det.detectMultiScale(img, scaleFactor=1.1, minNeighbors=6, minSize=(sh // 9, sh // 9)):
                found.append((SAMPLE_W - x - w if flip else x, y, w, h))
        return found

    def speaker_x(s):
        """Centre x (source pixels) of the face to frame in one shot, or None.
        Faces are grouped by where they sit; with two or more people in shot,
        the one whose mouth moves most is taken to be speaking."""
        groups = []      # [centre x, [boxes], [frame indexes]]
        for i in s:
            for (x, y, w, h) in faces(frames[i]):
                cx = x + w / 2
                g = next((g for g in groups if abs(g[0] - cx) < SAMPLE_W * 0.12), None)
                if g is None:
                    groups.append([cx, [(x, y, w, h)], [i]])
                elif i not in g[2]:
                    g[1].append((x, y, w, h)); g[2].append(i)
        groups = [g for g in groups if len(g[2]) >= max(1, len(s) * 0.3)]
        if not groups:
            return None

        def mouth_motion(g):
            x, y, w, h = (int(sorted(v)[len(v) // 2]) for v in zip(*g[1]))
            box = (slice(min(sh - 1, y + h * 2 // 3), min(sh, y + h + h // 6)), slice(x + w // 5, x + w * 4 // 5))
            diffs = [float(np.mean(cv2.absdiff(frames[i][box], frames[i - 1][box]))) for i in s[1:] if i - 1 in s]
            return sum(diffs) / len(diffs) if diffs else 0.0

        if len(groups) > 1:
            best = max(groups, key=mouth_motion)
        else:
            best = groups[0]
        return sorted(x + w / 2 for (x, _, w, _) in best[1])[len(best[1]) // 2] * SRC_W / SAMPLE_W

    shots, cur = [], [0]
    for i in range(1, n):
        if float(np.mean(cv2.absdiff(frames[i], frames[i - 1]))) > SHOT_CHANGE:
            shots.append(cur); cur = []
        cur.append(i)
    shots.append(cur)
    out = []
    for s in shots:
        x = speaker_x(s)
        left = centre if x is None else int(min(max(x - crop_w / 2, 0), SRC_W - crop_w))
        start = 0.0 if not out else s[0] / SAMPLE_FPS
        if out and abs(out[-1][1] - left) < 24:   # the same framing as the shot before: one crop
            continue
        out.append((round(start, 2), left))
    return out


def crop_expr(shots):
    """ffmpeg crop x as a function of t: one fixed value per shot."""
    expr = str(shots[-1][1])
    for (t, _), (_, x) in zip(reversed(shots[1:]), reversed(shots[:-1])):
        expr = f"if(lt(t\\,{t})\\,{x}\\,{expr})"
    return expr


# ---------------------------------------------------------------- drawing
def logo(width):
    im = Image.open(LOGO_PNG).convert("RGBA")
    return im.resize((width, round(width * im.height / im.width)), Image.LANCZOS)


def wrap(d, text, f, width):
    lines, cur = [], ""
    for word in text.split():
        t = f"{cur} {word}".strip()
        if cur and d.textlength(t, font=f) > width:
            lines.append(cur); cur = word
        else:
            cur = t
    return lines + ([cur] if cur else [])


def background(fmt, title):
    L = FORMATS[fmt]
    im = Image.new("RGBA", L["size"], BLACK + (255,))
    d = ImageDraw.Draw(im)
    for (x, y, w, h), colour in L["bands"]:
        d.rectangle([(x, y), (x + w - 1, y + h - 1)], fill=colour)
    if L["logo"] and LOGO_PNG.exists():
        x, y, w = L["logo"]
        im.alpha_composite(logo(w), (x, y))
    if L["title"] and title:
        x, y, w = L["title"]
        f = font("Borna-Medium.otf", 40)
        for n, line in enumerate(wrap(d, title, f, w)[:2]):
            d.text((x, y + n * 50), line, font=f, fill=WHITE)
    return im


def draw_caption(im, fmt, card, upto):
    """Borna SemiBold, black on white, left aligned. Words appear as they are
    spoken (laid out for the whole card first, so nothing moves); the word
    being spoken sits on a yellow highlight. In 16:9 the captions sit on a
    white box over the picture."""
    L = FORMATS[fmt]
    x, y, w, h = L["caption"]
    f = font("Borna-SemiBold.otf", L["cap_px"])
    d = ImageDraw.Draw(im)
    space = d.textlength(" ", font=f)
    lines, line, lw = [], [], 0
    for i, wd in enumerate(card):
        tw = d.textlength(wd["w"], font=f)
        if line and lw + space + tw > w:
            lines.append((line, lw)); line, lw = [], 0
        lw = lw + (space if line else 0) + tw
        line.append((i, wd["w"], tw))
    if line: lines.append((line, lw))
    lh = round(L["cap_px"] * 1.25)
    top = y + (h - lh * len(lines)) // 2
    if fmt == "16:9":
        widest = max(l[1] for l in lines)
        pad = 22
        d.rectangle([(x - pad, top - pad * 0.6), (x + widest + pad, top + lh * len(lines) + pad * 0.4)], fill=WHITE)
    for n, (ln, _) in enumerate(lines):
        cx = x
        for i, text, tw in ln:
            if i == upto:
                hp = round(L["cap_px"] * 0.12)
                d.rectangle([(cx - hp, top + n * lh - hp // 2), (cx + tw + hp, top + n * lh + round(L["cap_px"] * 1.12))], fill=YELLOW)
            if i <= upto:
                d.text((cx, top + n * lh), text, font=f, fill=BLACK)
            cx += tw + space


def draw_name(im, fmt, name, inst):
    """Name strap flush to the left edge: name black on yellow, institution white on black."""
    if not name: return
    x, y = FORMATS[fmt]["name"]
    d = ImageDraw.Draw(im)
    big = fmt == "16:9"
    f1 = font("Borna-Bold.otf", 38 if not big else 40)
    f2 = font("Borna-Regular.otf", 26 if not big else 28)
    pad = 64 if fmt == "9:16" else 48
    w1 = d.textlength(name, font=f1)
    d.rectangle([(x, y - 60), (x + pad + w1 + 28, y)], fill=YELLOW)
    d.text((x + pad, y - 30), name, font=f1, fill=BLACK, anchor="lm")
    if inst:
        w2 = d.textlength(inst, font=f2)
        d.rectangle([(x, y), (x + pad + w2 + 28, y + 44)], fill=BLACK)
        d.text((x + pad, y + 22), inst, font=f2, fill=WHITE, anchor="lm")


def end_card(fmt, head, sub):
    """Flat navy, white logo (when supplied) and headline, the facility below, a teal rule."""
    W, H = FORMATS[fmt]["size"]
    im = Image.new("RGBA", (W, H), BLACK + (255,))
    d = ImageDraw.Draw(im)
    m = round(min(W, H) * 0.075)
    if LOGO_PNG.exists():
        im.alpha_composite(logo(round(min(W, H) * 0.42)), (m, m))
    hf = font("Borna-Bold.otf", round(min(W, H) * 0.09))
    sf = font("Clearface-Regular.otf", round(min(W, H) * 0.04))
    lines = wrap(d, head, hf, W - 2 * m)
    sublines = wrap(d, sub, sf, W - 2 * m) if sub else []
    hl, sl = round(hf.size * 1.1), round(sf.size * 1.3)
    y = H - m - len(sublines) * sl - round(sf.size * 0.8) - len(lines) * hl
    d.rectangle([(m, y - round(hf.size * 0.5)), (m + round(min(W, H) * 0.12), y - round(hf.size * 0.5) + 10)], fill=YELLOW)
    for line in lines:
        d.text((m, y), line, font=hf, fill=WHITE); y += hl
    y += round(sf.size * 0.8)
    for line in sublines:
        d.text((m, y), line, font=sf, fill=WHITE); y += sl
    return im


def overlay_frames(fmt, cards, dur, straps, head, sub, workdir):
    """One transparent PNG per change (a word appears, a card changes, a strap
    comes or goes), listed with durations in an ffconcat file. Times are clip
    time: the cuts already joined."""
    W, H = FORMATS[fmt]["size"]
    flat = [(ci, wi, w) for ci, c in enumerate(cards) for wi, w in enumerate(c)]
    marks = {0.0, dur, dur + END_CARD}
    for st in straps: marks.update({st["at"], st["at"] + st["dur"]})
    for _, _, w in flat: marks.add(round(max(w["s"], 0), 2))
    for c in cards: marks.add(round(c[-1]["e"] + 0.25, 2))
    marks = sorted(t for t in marks if 0 <= t <= dur + END_CARD)
    lines, last = [], None
    endc = end_card(fmt, head, sub)
    for i, t in enumerate(marks[:-1]):
        nxt = marks[i + 1]
        if t >= dur:
            frame = endc
        else:
            frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            on = [st for st in straps if st["at"] <= t < min(st["at"] + st["dur"], dur)]
            if on: draw_name(frame, fmt, on[-1]["name"], on[-1].get("inst"))
            live = [x for x in flat if x[2]["s"] <= t + 1e-3]
            if live:
                ci, wi, w = live[-1]
                card_end = cards[ci][-1]["e"] + 0.25
                nxt_card = next((c[0]["s"] for c in cards[ci + 1:]), None)
                if t < card_end or (nxt_card is not None and t < nxt_card):
                    # once the last word has finished, its highlight goes
                    draw_caption(frame, fmt, cards[ci], wi if t < w["e"] else len(cards[ci]))
        p = workdir / f"o{i:04d}.png"
        frame.save(p, compress_level=1)
        lines.append(f"file '{p}'\nduration {nxt - t:.3f}")
        last = p
    lines.append(f"file '{last}'")  # concat needs the last frame twice
    (workdir / "overlay.ffconcat").write_text("ffconcat version 1.0\n" + "\n".join(lines) + "\n")
    return workdir / "overlay.ffconcat", len(marks) - 1


# ---------------------------------------------------------------- render
def render(a):
    """a: film, format, source_layout, and either cuts [[in, out], ...] or
    start/end; straps [{name, inst, at, dur}] or speaker/role; end_head,
    end_sub; name, out, quiet."""
    film, words = load(a.film)
    requested = [tuple(map(float, c)) for c in (getattr(a, "cuts", None) or [])] or [(a.start, a.end)]
    cuts, dur = resolve_cuts(words, requested)
    text = " ".join(w["w"] for c in cuts for w in c["words"] if not w.get("v"))
    check_compliance(film["id"], cuts, text)
    cards = [card for c in cuts for card in cards_from([w for w in c["words"] if not w.get("v")])]   # a card never spans a cut
    chapter = max((c for c in film.get("chapters", []) if c["t"] <= cuts[0]["in"]), key=lambda c: c["t"], default={})
    straps = [dict(st, at=max(0.0, float(st.get("at", 0))), dur=max(0.5, float(st.get("dur", 4))))
              for st in (getattr(a, "straps", None) or []) if (st.get("name") or "").strip()]
    if getattr(a, "straps", None) is None:   # the CLI and older versions: one strap from speaker/role
        name = getattr(a, "speaker", None) or chapter.get("speaker") or ""
        if name:
            straps = [{"name": name, "inst": getattr(a, "role", None) or "", "at": 0.3, "dur": 4.7}]
    title = display_title(film["title"])
    head = (getattr(a, "end_head", None) or END_HEAD).strip()
    fac = film.get("facility") or ""
    default_sub = fac if fac and fac != "Several HCA sites" else "HCA Healthcare UK"
    sub = (getattr(a, "end_sub", None) if getattr(a, "end_sub", None) is not None else default_sub).strip()
    master = find_master(film)
    layout = getattr(a, "source_layout", None) or "interview"
    fmt = a.format if cards else f"{a.format} broll"
    L = FORMATS[fmt]
    W, H = L["size"]
    px, py, pw, ph = L["picture"]
    crop_w = min(SRC_W, round(SRC_H * pw / ph) // 2 * 2)

    name = a.name or f"{film['id'][:60]}-{int(cuts[0]['in'])}-{int(cuts[-1]['out'])}-{a.format.replace(':', 'x')}"
    out_dir = Path(a.out).expanduser() if a.out else ROOT / "data" / "clip-test"
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4 = out_dir / f"{name}.mp4"
    print(f"Film     {film['title']}\nMaster   {film['file'] if str(master).startswith('http') else master}\nCuts     " + ", ".join(f"{c['in']:.2f}-{c['out']:.2f}" for c in cuts)
          + f" = {dur:.2f}s ({sum(len(c['words']) for c in cuts)} words, {len(cards)} cards)"
          f"\nFormat   {a.format} {W}x{H}, {layout if crop_w < SRC_W else 'full frame'}\nWords    {text[:160]}…")

    framing = [reframe(master, c, crop_w, layout) if crop_w < SRC_W else [(0.0, 0)] for c in cuts]
    if crop_w < SRC_W:
        print("Framing  " + " | ".join(", ".join(f"{t:.1f}s→x{x}" for t, x in f) for f in framing))

    work = Path(tempfile.mkdtemp(prefix="clip-"))
    try:
        bg = work / "bg.png"; background(fmt, title).save(bg)
        concat, n = overlay_frames(fmt, cards, dur, straps, head, sub, work)
        print(f"Overlay  {n} frames drawn")

        total = dur + END_CARD
        K = len(cuts)
        inputs, chain = [], []
        remote = str(master).startswith("http")
        for c in cuts:   # each cut is its own accurately seeked input
            net = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"] if remote else []
            inputs += [*net, "-ss", f"{c['in']:.3f}", "-t", f"{c['len']:.3f}", "-i", str(master)]
        # Some Selects exports have no sound at all: give those cuts silence.
        has_audio = bool(subprocess.run(["ffprobe", "-v", "error", *(["-reconnect", "1"] if remote else []), "-select_streams", "a",
                                         "-show_entries", "stream=index", "-of", "csv=p=0", str(master)],
                                        capture_output=True, text=True).stdout.strip())
        if not has_audio:
            for c in cuts:
                inputs += ["-f", "lavfi", "-t", f"{c['len']:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        for k, c in enumerate(cuts):
            fade = min(0.06, c["len"] / 4)
            asrc = f"{k}:a" if has_audio else f"{K + k}:a"
            chain.append(f"[{k}:v]setpts=PTS-STARTPTS,fps={FPS},crop={crop_w}:{SRC_H}:x='{crop_expr(framing[k])}':y=0,"
                         f"scale={pw}:{ph}:flags=lanczos,setsar=1[cv{k}]")
            chain.append(f"[{asrc}]asetpts=PTS-STARTPTS,afade=t=in:st=0:d={fade:.3f},afade=t=out:st={c['len'] - fade:.3f}:d={fade:.3f}[ca{k}]")
        chain.append("".join(f"[cv{k}][ca{k}]" for k in range(K)) + f"concat=n={K}:v=1:a=1[vjoin][ajoin]")
        bgi, ovi = (K, K + 1) if has_audio else (2 * K, 2 * K + 1)
        chain.append(f"[{bgi}:v][vjoin]overlay={px}:{py}:eof_action=pass[pic]")   # the end card plays over the background
        chain.append(f"[pic][{ovi}:v]overlay=0:0:format=auto,format=yuv420p[v]")
        chain.append(f"[ajoin]afade=t=in:st=0:d=0.12,afade=t=out:st={max(0, dur - 0.35):.2f}:d=0.35,"
                     f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,apad=whole_dur={total:.3f}[a]")
        cmd = ["ffmpeg", "-v", "error", *([] if getattr(a, "quiet", False) else ["-stats"]), "-y", *inputs,
               "-loop", "1", "-framerate", str(FPS), "-t", f"{total:.3f}", "-i", str(bg),
               "-f", "concat", "-safe", "0", "-i", str(concat),
               "-filter_complex", ";".join(chain), "-map", "[v]", "-map", "[a]",
               "-r", str(FPS), "-t", f"{total:.3f}",
               "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(mp4)]
        run = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
        if run.stderr and not getattr(a, "quiet", False): sys.stderr.write(run.stderr)
        if run.returncode != 0:   # keep ffmpeg's own last words, without any signed links
            tail = " | ".join(l.strip() for l in run.stderr.strip().splitlines()[-6:])
            raise RuntimeError(f"ffmpeg failed ({run.returncode}): " + re.sub(r"https?://\S+", "<film link>", tail)[:600])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    sha = hashlib.sha256(mp4.read_bytes()).hexdigest()
    record = {
        "film": film["id"], "filmTitle": film["title"], "streamId": film.get("streamId"), "master": film["file"],
        "in": cuts[0]["in"], "out": cuts[-1]["out"], "duration": round(total, 2),
        "cuts": [{"in": c["in"], "out": c["out"], "at": c["offset"], "text": " ".join(w["w"] for w in c["words"])} for c in cuts],
        "format": a.format, "size": [W, H], "chapter": chapter.get("title"),
        "framing": framing if crop_w < SRC_W else None,
        "straps": straps, "endCard": {"head": head, "sub": sub},
        "speaker": straps[0]["name"] if straps else None, "role": straps[0].get("inst") if straps else None,
        "words": text, "captions": [{"text": " ".join(w["w"] for w in c), "start": round(c[0]["s"], 2),
                                     "end": round(c[-1]["e"], 2)} for c in cards],
        "file": mp4.name, "sha256": sha, "bytes": mp4.stat().st_size,
    }
    mp4.with_suffix(".json").write_text(json.dumps(record, indent=2))
    print(f"\n✓ {mp4}\n  {record['bytes'] / 1e6:.1f} MB · {total:.1f}s · {K} cut{'s' if K > 1 else ''} · sha256 {sha[:16]}…")
    return record


PREVIEW_MARK = "For review – not approved"


def preview(src, dst, quiet=True):
    """A half-size copy of a finished clip, stamped "For review – not approved"
    across the frame, for the marketing team to watch before approval. The
    clean file stays locked until Medical Affairs approves it."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(src)], capture_output=True, text=True, check=True)
    W, H = (int(v) // 2 // 2 * 2 for v in probe.stdout.strip().split(",")[:2])
    mark = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))   # drawn large, rotated, then cropped to the frame
    d = ImageDraw.Draw(mark)
    f = font("Borna-Bold.otf", max(22, W // 18))
    step_x, step_y = int(d.textlength(PREVIEW_MARK, font=f)) + W // 4, max(120, H // 3)
    for row, y in enumerate(range(0, H * 2, step_y)):
        for x in range(-(row % 2) * step_x // 2, W * 2, step_x):
            d.text((x, y), PREVIEW_MARK, font=f, fill=(255, 255, 255, 92), stroke_width=2, stroke_fill=(0, 0, 0, 70))
    mark = mark.rotate(28, resample=Image.BICUBIC).crop((W // 2, H // 2, W // 2 + W, H // 2 + H))
    badge = font("Borna-Bold.otf", max(18, W // 30))
    bd = ImageDraw.Draw(mark)
    bw = int(bd.textlength(PREVIEW_MARK, font=badge))
    bd.rectangle([(0, H - badge.size * 2), (bw + badge.size * 2, H)], fill=(*BLACK, 230))
    bd.text((badge.size, H - badge.size), PREVIEW_MARK, font=badge, fill=YELLOW, anchor="lm")
    with tempfile.TemporaryDirectory(prefix="preview-") as td:
        png = Path(td) / "mark.png"
        mark.save(png)
        run = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-i", str(png),
                              "-filter_complex", f"[0:v]scale={W}:{H}[v];[v][1:v]overlay=0:0",
                              "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
                              "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dst)],
                             stderr=subprocess.PIPE, text=True)
    if run.returncode != 0:
        raise RuntimeError("preview failed: " + " | ".join(run.stderr.strip().splitlines()[-4:])[:400])
    if not quiet: print(f"✓ preview {dst} ({Path(dst).stat().st_size / 1e6:.1f} MB)")
    return Path(dst)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--film", default="labs-why-hca-2026-selects-gaps-closed")
    ap.add_argument("--start", type=float, default=30.0)
    ap.add_argument("--end", type=float, default=70.0)
    ap.add_argument("--cut", action="append", metavar="IN-OUT", help="a kept stretch in film seconds; repeat for several")
    ap.add_argument("--format", default="9:16", choices=FORMATS)
    ap.add_argument("--source-layout", default="interview", choices=SOURCE_LAYOUTS)
    ap.add_argument("--speaker", help="name strap; defaults to the chapter's speaker")
    ap.add_argument("--role", help="second line of the strap (institution)")
    ap.add_argument("--end-head", help=f"end card headline (default: {END_HEAD})")
    ap.add_argument("--end-sub", help="end card second line (default: the film title)")
    ap.add_argument("--name", help="output file name without extension")
    ap.add_argument("--out", help="output folder (default data/clip-test)")
    a = ap.parse_args()
    a.cuts = [tuple(map(float, c.split("-"))) for c in a.cut] if a.cut else None
    a.straps = None
    render(a)


if __name__ == "__main__":
    main()
