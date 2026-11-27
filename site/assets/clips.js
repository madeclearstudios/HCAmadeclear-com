/* ------------------------------------------------------------------
   Shared by marketing.html and review.html: Supabase sign-in, the role
   gate, the library index and small helpers.

   Real accounts (unlike the demo sign-in in session.js). Sign-in is an email
   link; who may do what is decided in the database (supabase/migrations),
   not here. These pages only show or hide controls to match. The key below
   is the public "publishable" key, safe in a web page: on its own it can do
   nothing the database rules don't allow. The functions keep their own copy
   in netlify/lib/review.mjs.
-------------------------------------------------------------------*/
(function () {
  const SUPABASE_URL = "https://rlpidxfxazhqhvryiolh.supabase.co";
  const SUPABASE_KEY = "sb_publishable_CuiSkBPUWAD0ft-jow7jbQ_nnmNbpeB";
  const sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, flowType: "implicit" },
  });

  const $ = id => document.getElementById(id);
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const tc = s => { s = Math.max(0, Math.round(Number(s) || 0)); const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60; return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(x).padStart(2, "0")}`; };
  const when = iso => iso ? new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const cleanTitle = t => String(t || "").replace(/\s+/g, " ").trim();
  /** The film's name without the edit history, as on the rendered clip (display_title in
      pipeline/clip_render.py): "RDN@ESC_ Prof. Parati & Prof. Pathak - Version 3" -> "RDN@ESC Prof. Parati & Prof. Pathak". */
  const displayTitle = t => {
    const s = cleanTitle(t)
      .replace(/\s*[-–]\s*(?:final\s+)?(?:version|vers\.?|rough cut|assembly)\b.*$/i, "")
      .replace(/\b(?:white|blue|no)\s+branding\b/gi, "")
      .replace(/_/g, " ")
      .replace(/\s{2,}/g, " ").replace(/^[ ,-]+|[ ,-]+$/g, "");
    return s || cleanTitle(t);
  };

  const STATUS = {
    queued:            { text: "Queued",            cls: "grey" },
    rendering:         { text: "Rendering",         cls: "grey" },
    awaiting_review:   { text: "Awaiting review",   cls: "" },
    changes_requested: { text: "Changes requested", cls: "bad" },
    approved:          { text: "Approved",          cls: "good" },
    failed:            { text: "Couldn't render",   cls: "bad" },
  };
  const lozenge = status => { const s = STATUS[status] || { text: status, cls: "grey" }; return `<span class="lozenge ${s.cls}">${esc(s.text)}</span>`; };
  const ROLE_NAME = { marketer: "Marketing", medaffairs: "Approvals", admin: "HCA team" };

  /* ---------------- library ---------------- */
  let INDEX = null, RULES = { blockedTopics: [], excludedRanges: [] }, MASTER_FPS = {};
  async function loadLibrary() {
    if (INDEX) return INDEX;
    const [idx, rules, fps] = await Promise.all([
      fetch("./index.json").then(r => r.ok ? r.json() : Promise.reject(new Error(`library HTTP ${r.status}`))),
      fetch("./assets/compliance.json", { cache: "no-store" }).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch("./assets/masters.json").then(r => r.ok ? r.json() : null).catch(() => null),
    ]);
    if (rules) RULES = rules;
    if (fps) MASTER_FPS = fps;
    INDEX = idx;
    INDEX.byId = new Map(idx.films.map(f => [f.id, f]));
    return INDEX;
  }
  const film = id => INDEX && INDEX.byId.get(id);
  const blockedTopic = text => {
    for (const t of RULES.blockedTopics || []) for (const p of t.patterns || []) {
      try { if (new RegExp(p, "i").test(text || "")) return t.label; } catch (e) {}
    }
    return null;
  };
  /** Why a stretch of film can't be clipped, or null. The render worker checks again. */
  function compliance(filmId, from, to, text) {
    const r = (RULES.excludedRanges || []).find(r => r.film === filmId && from < r.to && to > r.from);
    if (r) return `This stretch overlaps material that can't be used (${r.reason}).`;
    const topic = blockedTopic(text);
    if (topic) return `The words touch a topic outside approved use (${topic}).`;
    return null;
  }
  /** Sentences of a film, each with an end time (the next sentence's start). */
  function sentences(f) {
    if (f._sent) return f._sent;
    if (!f.segments && f.shots) {   // HCA Selects: search the shot descriptions and tags
      f._sent = f.shots.map((s, i) => ({ i, t: s.t, end: s.end, text: s.desc || `Shot ${s.n}`, tags: s.tags || [], kind: "shot" }));
      return f._sent;
    }
    const segs = f.segments || [];
    f._sent = segs.map((s, i) => ({ i, t: s.t, end: i + 1 < segs.length ? segs[i + 1].t : Number(f.duration) || s.t + 6, text: s.text }));
    return f._sent;
  }
  /** A film's chapters to browse. A film without any is one "Whole film" entry from the start. */
  function chapters(f) {
    return (f.chapters && f.chapters.length) ? f.chapters : [{ t: 0, title: "Whole film" }];
  }
  function chapterAt(f, t) {
    return (f.chapters || []).filter(c => c.t <= t).sort((a, b) => b.t - a.t)[0] || null;
  }
  function slidesIn(f, from, to) {
    return (f.slides || []).filter(s => s.t < to && (s.end ?? s.t + 30) > from);
  }

  /* ---------------- Stream preview (signed by /api/play) ---------------- */
  async function streamUrl(f, t) {
    const r = await fetch(`/api/play?id=${encodeURIComponent(f.streamId)}`, { cache: "no-store" });
    const j = await r.json().catch(() => null);
    if (!r.ok || !j || !j.token) throw new Error((j && j.error) || `the player isn't available here (HTTP ${r.status})`);
    return `https://customer-${INDEX.stream.customerCode}.cloudflarestream.com/${j.token}/iframe?startTime=${Math.max(0, Math.floor(t))}s&preload=auto`;
  }

  /* ---------------- sign-in gate ----------------
     Renders into #gate. Resolves with { user, profile } once someone with
     one of the allowed roles is signed in; otherwise shows the form or the
     "not set up" note and never resolves. */
  /* Demo sign-in: while DEMO_PASSWORD is set in Netlify, /api/demo-login says
     it's on, and typing anything in the email box signs you in to a shared
     demo account (Marketing on /marketing, Medical Affairs on /review). The
     password stays in that function; this page only receives a session. */
  const DEMO_ROLES = ["marketer", "medaffairs"];   // Made Clear (admin) always signs in by email link
  let ownAuthAt = 0;   // when this tab last signed in or out itself (auth events soon after are ours, not another tab's)
  // Sign out of this browser only. The demo accounts are shared, so a global sign-out would end
  // everyone else's demo session too; and if the server call fails, still forget the session here.
  const signOut = async () => {
    ownAuthAt = Date.now();
    const { error } = await sb.auth.signOut({ scope: "local" });
    if (error) { try { Object.keys(localStorage).filter(k => /^sb-.*-auth-token/.test(k)).forEach(k => localStorage.removeItem(k)); } catch (e) {} }
  };
  let demoOn = null;
  async function demoEnabled() {
    if (demoOn !== null) return demoOn;
    try { demoOn = !!(await (await fetch("/api/demo-login", { cache: "no-store" })).json()).enabled; }
    catch (e) { demoOn = false; }
    return demoOn;
  }
  async function demoSignIn(role) {
    ownAuthAt = Date.now();
    const r = await fetch("/api/demo-login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.access_token) throw new Error(j.error || `HTTP ${r.status}`);
    const { data, error } = await sb.auth.setSession({ access_token: j.access_token, refresh_token: j.refresh_token });
    if (error) throw error;
    return data.session;
  }

  function gate({ roles, title, intro, otherPage, switchTo }) {
    const box = $("gate");
    const demoRole = roles[0];
    return new Promise(resolve => {
      const showDemoForm = (msg = "") => {
        box.hidden = false;
        box.innerHTML = `
          <form class="card gate-card" id="gForm" novalidate>
            <span class="eyebrow">${esc(title)}</span>
            <h1>Sign in</h1>
            <p>${esc(intro)}</p>
            <div class="field" id="gField"><label for="gEmail">Email</label>
              <input class="input" id="gEmail" type="text" autocomplete="off" required>
              <span class="err">Type anything to sign in.</span></div>
            <button class="btn btn-dark" type="submit" id="gGo">Sign in as ${esc(ROLE_NAME[demoRole] || demoRole)}</button>
            <p class="gate-note">Demo version: any email signs you in as ${esc(ROLE_NAME[demoRole] || demoRole)}.</p>
            <p class="gate-msg" id="gMsg" aria-live="polite">${esc(msg)}</p>
          </form>`;
        $("gEmail").focus();
        $("gForm").onsubmit = async e => {
          e.preventDefault();
          const typed = $("gEmail").value.trim();
          $("gField").classList.toggle("invalid", !typed);
          if (!typed) return;
          $("gGo").disabled = true; $("gMsg").textContent = "Signing in…";
          try { check(await demoSignIn(demoRole)); }
          catch (err) { $("gGo").disabled = false; $("gMsg").textContent = `That didn't work: ${err.message}`; }
        };
      };
      const showForm = async (msg = "") => {
        // HCA: one team login. Past the site password, sign straight in to the shared team account.
        if (await demoEnabled()) {
          try { return check(await demoSignIn(demoRole)); }
          catch (err) { return showDemoForm(`Couldn't sign in automatically: ${err.message}`); }
        }
        box.hidden = false;
        box.innerHTML = `
          <form class="card gate-card" id="gForm" novalidate>
            <span class="eyebrow">${esc(title)}</span>
            <h1>Sign in</h1>
            <p>${esc(intro)}</p>
            <div class="field" id="gField"><label for="gEmail">Work email</label>
              <input class="input" id="gEmail" type="email" autocomplete="email" required>
              <span class="err">Enter your work email.</span></div>
            <button class="btn btn-dark" type="submit" id="gGo">Email me a sign-in link</button>
            <p class="gate-msg" id="gMsg" aria-live="polite">${esc(msg)}</p>
          </form>`;
        $("gForm").onsubmit = async e => {
          e.preventDefault();
          const email = $("gEmail").value.trim().toLowerCase();
          $("gField").classList.toggle("invalid", !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email));
          if ($("gField").classList.contains("invalid")) return;
          $("gGo").disabled = true; $("gMsg").textContent = "Sending…";
          const { error } = await sb.auth.signInWithOtp({ email, options: { emailRedirectTo: location.href.split("#")[0] } });
          $("gGo").disabled = false;
          $("gMsg").textContent = error
            ? `That didn't send: ${error.message}`
            : `Check ${email} for a link from Supabase. It opens this page signed in. You can close this tab.`;
        };
      };
      // Sign-in is shared by every tab of the site. When another tab changes it to a role this
      // page isn't for, say so here rather than signing out (which would undo that tab's switch).
      const otherTab = async role => {
        box.hidden = false; $("app") && ($("app").hidden = true);
        const canDemo = DEMO_ROLES.includes(demoRole) && await demoEnabled();
        box.innerHTML = `<div class="card gate-card"><span class="eyebrow">${esc(title)}</span><h1>Signed in elsewhere</h1>
          <p>${role ? `Another tab switched to ${esc(ROLE_NAME[role] || role)}.` : "Another tab signed out."} This page needs ${esc(ROLE_NAME[demoRole] || demoRole)}.</p>
          ${canDemo ? `<button class="btn btn-dark" type="button" id="gHere">Sign in as ${esc(ROLE_NAME[demoRole] || demoRole)} here</button>` : `<button class="btn btn-dark" type="button" id="gHere">Sign in again</button>`}</div>`;
        $("gHere").onclick = async () => {
          if (!canDemo) return location.reload();
          $("gHere").disabled = true;
          try { await demoSignIn(demoRole); location.reload(); } catch (e) { location.reload(); }
        };
      };
      let shownRole = null;
      const check = async (session, fromOtherTab = false) => {
        if (!session) return fromOtherTab ? otherTab(null) : showForm();
        const { data: profile, error } = await sb.from("profiles").select("*").eq("id", session.user.id).maybeSingle();
        if (error || !profile || !profile.role) {
          box.hidden = false;
          box.innerHTML = `<div class="card gate-card"><span class="eyebrow">${esc(title)}</span><h1>Not set up yet</h1>
            <p>${esc(session.user.email)} is signed in but hasn't been given access. Ask Made Clear to add you.</p>
            <button class="btn btn-light" type="button" id="gOut">Sign out</button></div>`;
          $("gOut").onclick = () => signOut().then(() => location.reload());
          return;
        }
        if (fromOtherTab) {
          if (!roles.includes(profile.role)) return otherTab(profile.role);
          if (shownRole && shownRole !== profile.role) return location.reload();   // still allowed here, but a different role
          if (shownRole) return;
        }
        if (!roles.includes(profile.role) && DEMO_ROLES.includes(demoRole) && await demoEnabled()) {   // demo: just sign in again for this page
          await signOut();
          return showDemoForm(`You were signed in as ${ROLE_NAME[profile.role] || profile.role}. Sign in again for this page.`);
        }
        if (!roles.includes(profile.role)) {
          box.hidden = false;
          box.innerHTML = `<div class="card gate-card"><span class="eyebrow">${esc(title)}</span><h1>This page isn't for your role</h1>
            <p>You're signed in as ${esc(ROLE_NAME[profile.role] || profile.role)}.</p>
            ${otherPage ? `<a class="btn btn-dark" href="${otherPage.href}">${esc(otherPage.text)}</a>` : ""}
            <button class="btn btn-light" type="button" id="gOut">Sign out</button></div>`;
          $("gOut").onclick = () => signOut().then(() => location.reload());
          return;
        }
        box.hidden = true; box.innerHTML = "";
        shownRole = profile.role;
        // Already in the switch target's role (a marketer viewing reviews): offer this page's own role instead.
        const target = switchTo && switchTo.role === profile.role && DEMO_ROLES.includes(demoRole)
          ? { href: location.pathname + location.search, text: `Switch to ${ROLE_NAME[demoRole] || demoRole}`, role: demoRole }
          : switchTo;
        userPill(profile, (await demoEnabled()) ? target : null);
        resolve({ user: session.user, profile });
      };
      // Arriving from the demo switch button: sign straight in as this page's role.
      const url = new URL(location.href);
      const fromSwitch = url.searchParams.get("signin") === "demo";
      if (fromSwitch) { url.searchParams.delete("signin"); history.replaceState(null, "", url.pathname + url.search + url.hash); }
      let done = false;
      sb.auth.getSession().then(async ({ data }) => {
        let session = data.session;
        // From the switch button, sign in fresh as this page's role even if an old session lingers.
        if (fromSwitch && DEMO_ROLES.includes(demoRole) && await demoEnabled()) {
          try { session = await demoSignIn(demoRole); } catch (e) { /* falls back to whatever session there is, or the sign-in box */ }
        }
        done = true; check(session);
      });
      sb.auth.onAuthStateChange((ev, session) => {
        if (!done || !["SIGNED_IN", "SIGNED_OUT"].includes(ev)) return;
        if (Date.now() - ownAuthAt < 5000) { if (ev === "SIGNED_IN") check(session); return; }
        check(session, true);
      });
    });
  }

  /** Signed-in role, sign out, and in demo mode a one-click switch to the other role's page. */
  function userPill(profile, switchTo) {
    document.querySelectorAll("[data-clip-user]").forEach(slot => {
      const sw = switchTo ? `<button class="btn btn-light btn-sm switch" type="button">${esc(switchTo.text)}</button>` : "";
      slot.innerHTML = `<span class="who-role">${esc(ROLE_NAME[profile.role] || "")}</span>${sw}
        <button class="btn btn-light btn-sm out" type="button" title="Signed in as ${esc(profile.email)}">Sign out</button>`;
      slot.querySelector(".out").onclick = () => signOut().then(() => location.reload());
      const s = slot.querySelector(".switch");
      // Sign out here and let the other page sign in as its own role when it
      // loads (signing in here would trip this page's wrong-role check).
      if (s) s.onclick = async () => {
        s.disabled = true;
        await signOut();
        location.href = switchTo.href + (switchTo.href.includes("?") ? "&" : "?") + "signin=demo";
      };
    });
  }

  /* ---------------- Premiere Pro XML for a fine edit ----------------
     Final Cut 7 / Premiere XML (xmeml v4): one 1920x1080 sequence at the
     master's own frame rate, with each kept stretch cut from the master file
     in its folder on the WONDR drive,
     video and both audio channels linked, using the renderer's own padding
     so the timeline matches the rendered clip frame for frame. Name straps,
     joins and the requested format are sequence markers. Premiere asks to
     relink if the drive is mounted somewhere else.

     Frame rates come from assets/masters.json (film id -> fps), made from
     data/masters.tsv; rebuild it from the repo root after re-surveying:
       python3 -c "import csv,json; print(json.dumps({r['film_id']: float(r['fps']) for r in csv.DictReader(open('data/masters.tsv'), delimiter='\t')}, indent=1))" > site/assets/masters.json
     23.976 and 29.97 are NTSC rates: xmeml writes them as timebase 24 or 30
     with ntsc TRUE, and counts frames at the true rate. */
  const MASTER_FOLDER = "/Users/mikeclear/Made Clear Studios Dropbox/MCS PROJECTS 2026/HCA HEALTHCARE/WHY HCA 2026/EXPORTS/SELECTS/";
  const PAD_IN = 0.15, PAD_OUT = 0.30;
  const frameRate = filmId => {
    const fps = Number(MASTER_FPS[filmId]) || 25;
    const base = Math.round(fps), ntsc = Math.abs(fps - base) > 0.001;
    return { fps: ntsc ? base * 1000 / 1001 : base, base, ntsc };
  };
  const xe = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" }[c]));
  const pathurl = p => "file://localhost" + p.split("/").map(encodeURIComponent).join("/");

  function premiereXML({ clip, version, film, note }) {
    const { fps: FPS, base, ntsc } = frameRate(film.id);
    const raw = (version.cuts && version.cuts.length) ? version.cuts : [{ in: +version.in_s, out: +version.out_s }];
    let t = 0, prevOut = 0;
    const cuts = raw.map(c => {
      const a = Math.max(+c.in - PAD_IN, prevOut), b = +c.out + PAD_OUT;
      prevOut = b;
      const srcIn = Math.round(a * FPS), srcOut = Math.round(b * FPS), len = srcOut - srcIn;
      const cut = { srcIn, srcOut, start: t, end: t + len };
      t += len;
      return cut;
    });
    const total = t;
    const rate = `<rate><timebase>${base}</timebase><ntsc>${ntsc ? "TRUE" : "FALSE"}</ntsc></rate>`;
    const masterFrames = Math.round((Number(film.duration) || 0) * FPS);
    const master = MASTER_FOLDER + (film.folder ? `${film.folder}/` : "") + film.file;
    const fileDef = `<file id="file-1"><name>${xe(film.file)}</name><pathurl>${xe(pathurl(master))}</pathurl>${rate}<duration>${masterFrames}</duration>
          <timecode>${rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>
          <media><video><samplecharacteristics>${rate}<width>1920</width><height>1080</height><pixelaspectratio>square</pixelaspectratio></samplecharacteristics></video>
          <audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics><channelcount>2</channelcount></audio></media></file>`;
    const ids = k => ({ v: `clipitem-v${k + 1}`, a1: `clipitem-a${k + 1}-1`, a2: `clipitem-a${k + 1}-2` });
    const links = k => { const i = ids(k); return [["video", i.v, 1], ["audio", i.a1, 1], ["audio", i.a2, 2]]
      .map(([m, id, tr]) => `<link><linkclipref>${id}</linkclipref><mediatype>${m}</mediatype><trackindex>${tr}</trackindex><clipindex>${k + 1}</clipindex>${m === "audio" ? `<groupindex>1</groupindex>` : ""}</link>`).join(""); };
    const item = (id, k, c, extra) => `<clipitem id="${id}"><name>${xe(film.file)}</name><enabled>TRUE</enabled><duration>${masterFrames}</duration>${rate}
          <start>${c.start}</start><end>${c.end}</end><in>${c.srcIn}</in><out>${c.srcOut}</out>
          ${k === 0 && id.startsWith("clipitem-v") ? fileDef : `<file id="file-1"/>`}${extra}${links(k)}</clipitem>`;
    const video = cuts.map((c, k) => item(ids(k).v, k, c, "")).join("\n        ");
    const audio = ch => cuts.map((c, k) => item(ch === 1 ? ids(k).a1 : ids(k).a2, k, c,
      `<sourcetrack><mediatype>audio</mediatype><trackindex>${ch}</trackindex></sourcetrack>`)).join("\n        ");
    const marker = (name, frame, comment = "") => `<marker><name>${xe(name)}</name><comment>${xe(comment)}</comment><in>${frame}</in><out>-1</out></marker>`;
    const markers = [
      marker(`Fine edit: ${clip.title}`, 0, `${version.format} · version ${version.version} · ${note || ""}`),
      ...cuts.slice(1).map((c, k) => marker(`Join ${k + 1}`, c.start, "Sentences cut out here")),
      ...(version.straps || []).map(s => marker(`Strap: ${s.name}`, Math.round((+s.at || 0) * FPS), `${s.inst || ""} · ${s.dur || ""} s`)),
      marker(`End card: ${version.end_head || "Watch the full film"}`, total, version.end_sub || ""),
    ].join("\n    ");
    return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <sequence id="sequence-1">
    <name>${xe(`${clip.title} v${version.version}`)}</name>
    <duration>${total}</duration>
    ${rate}
    <timecode>${rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>
    <media>
      <video>
        <format><samplecharacteristics>${rate}<width>1920</width><height>1080</height><pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance></samplecharacteristics></format>
        <track>
        ${video}
        </track>
      </video>
      <audio>
        <format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format>
        <track>
        ${audio(1)}
        </track>
        <track>
        ${audio(2)}
        </track>
      </audio>
    </media>
    ${markers}
  </sequence>
</xmeml>
`;
  }

  /** A short-lived link to a rendered file. The database decides whether this person may have it. */
  async function fileUrl(path, { download = null } = {}) {
    const { data, error } = await sb.storage.from("clips").createSignedUrl(path, 3600, download ? { download } : undefined);
    if (error) throw error;
    return data.signedUrl;
  }

  window.Clips = { sb, $, el, esc, tc, when, cleanTitle, displayTitle, lozenge, STATUS, ROLE_NAME,
    loadLibrary, film, compliance, sentences, chapters, chapterAt, slidesIn, streamUrl, gate, fileUrl, premiereXML,
    get index() { return INDEX; } };
})();
