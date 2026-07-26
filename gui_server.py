#!/usr/bin/env python3
"""
gui_server.py  --  the panel behind the TikTok Analyzer Desktop app.

Serves a small local page that collects everything a new brand needs, writes the
config and the brief, runs the pipeline as a background job with live progress,
and hands off to the dashboard when it finishes. Standard library only, so there
is nothing to install.

Not exposed to the network: it binds 127.0.0.1 and there is no auth, because the
process holds the Apify and Gemini keys from .env and can spend money. Keep it
local.

    python3 gui_server.py            # then open http://127.0.0.1:8765
    python3 gui_server.py --port N
    python3 gui_server.py --no-open  # do not launch a browser
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import derive as derive_mod
from runstore import archive_previous_run, restore_run
from compare_runs import compare as compare_signals, load_signal

ROOT = Path(__file__).parent
CONFIG_DIR = ROOT / "config"
WEB_DIR = ROOT / "web"
TEMPLATE_BRIEF = CONFIG_DIR / "TEMPLATE-brief.md"

AMBIGUOUS_TAGS = {
    "genesis", "patagonia", "apple", "orange", "shell", "gap", "target",
    "amazon", "puma", "jaguar", "polo", "columbia", "arc", "north", "face",
    "canada", "moon", "sport", "vans", "coach", "guess",
}

# ── Job state, one run at a time ─────────────────────────────────────────────

JOB: Dict[str, Any] = {
    "running": False,
    "step": "",
    "steps_done": 0,
    "steps_total": 0,
    "log": [],
    "error": "",
    "finished": False,
    "config": "",
}
JOB_LOCK = threading.Lock()
VERIFIED_HANDLES: set = set()


def log(line: str) -> None:
    # The system Python prints an OpenSSL warning on every subprocess start;
    # four steps times two lines buried the warnings that actually matter.
    if "NotOpenSSLWarning" in line or line.strip() == "warnings.warn(" or "urllib3/__init__" in line:
        return
    with JOB_LOCK:
        JOB["log"].append(line.rstrip())
        del JOB["log"][:-400]          # keep the tail bounded


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def norm_handle(h: str) -> str:
    h = (h or "").strip()
    return h if h.startswith("@") else ("@" + h if h else "")


def brief_is_template(text: str) -> bool:
    tells = ["- ...", "| ... |", "Three or four sentences", "Six or so bullets"]
    return sum(1 for t in tells if t in text) >= 2


# ── The pipeline job ─────────────────────────────────────────────────────────

def run_pipeline(config_path: Path, pool: int, htag: int, timeline_pool: int = 0, notes: Optional[List[str]] = None) -> None:
    env = dict(os.environ)
    env["SIGNAL_BRAND_CONFIG"] = str(config_path)
    env["PYTHONUNBUFFERED"] = "1"

    steps = [
        ("Scraping TikTok", [sys.executable, "-u", "ingest.py",
                             "--pool-size", str(pool), "--hashtag-pool-size", str(htag)]),
        ("Reading the videos", [sys.executable, "-u", "analyze.py"]),
        ("Building the signal", [sys.executable, "-u", "build_signal.py"]),
        ("Building the table", [sys.executable, "-u", "build_videos_json.py"]),
    ]
    if timeline_pool:
        steps.append((
            "Building the timeline",
            [sys.executable, "-u", "timeline.py", "--pool", str(timeline_pool)],
        ))

    with JOB_LOCK:
        JOB.update(finished=False, error="", steps_done=0,
                   steps_total=len(steps), log=[], config=str(config_path))
        JOB["_stop_requested"] = False

    try:
        # The previous run's analysis and signal are overwritten by this one.
        # The first real use of the panel destroyed a paid analysis this way.
        for note in notes or []:
            log(note)
        saved = archive_previous_run()
        if saved:
            log(f"── Previous run archived to {saved.relative_to(ROOT)}")

        for i, (label, cmd) in enumerate(steps):
            with JOB_LOCK:
                JOB["step"] = label
                JOB["steps_done"] = i
            log(f"── {label}")
            proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)
            with JOB_LOCK:
                JOB["_proc"] = proc
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.strip():
                    log(line)
            code = proc.wait()
            with JOB_LOCK:
                JOB["_proc"] = None
                stopped = JOB.get("_stop_requested", False)
            if stopped:
                raise RuntimeError("Stopped by request. The config is intact; rerun when ready.")
            if code != 0:
                raise RuntimeError(f"{label} failed (exit {code})")

        sync_web()
        with JOB_LOCK:
            JOB.update(steps_done=len(steps), step="Done", finished=True)
        log("── Done")
    except Exception as exc:                                  # noqa: BLE001
        with JOB_LOCK:
            JOB["error"] = str(exc)
            JOB["finished"] = True
        log(f"ERROR: {exc}")
    finally:
        with JOB_LOCK:
            JOB["running"] = False


def sync_web() -> None:
    signal = ROOT / "signal.json"
    if not signal.is_file():
        return
    shutil.copy(signal, WEB_DIR / "signal.json")
    tl = ROOT / "timeline.json"
    if tl.is_file():
        shutil.copy(tl, WEB_DIR / "timeline.json")
    ids = set()
    try:
        sig = json.loads(signal.read_text(encoding="utf-8"))
        ids |= {v["id"] for v in sig.get("segnale", {}).get("top_videos", [])}
        table = json.loads((WEB_DIR / "videos.json").read_text(encoding="utf-8"))
        ids |= {v["id"] for v in table.get("videos", [])}
    except (OSError, ValueError, KeyError):
        pass
    dest = WEB_DIR / "assets" / "thumbs"
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for vid in ids:
        src = ROOT / "data" / "raw" / "thumbs" / f"{vid}.jpg"
        if src.is_file():
            shutil.copy(src, dest / f"{vid}.jpg")
            n += 1
    log(f"{n} thumbnails copied into the dashboard")


# ── HTTP ─────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:      # keep the console quiet
        pass

    # -- helpers --
    def send_json(self, obj: Any, code: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str, code: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".css": "text/css", ".js": "text/javascript",
        }.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # -- routes --
    def do_GET(self) -> None:                        # noqa: N802
        url = urlparse(self.path)
        route = url.path

        if route in ("/", "/index.html"):
            self.send_html(PAGE.replace("__TEMPLATE_BRIEF__",
                                        json.dumps(TEMPLATE_BRIEF.read_text(encoding="utf-8"))))
            return

        if route == "/status":
            with JOB_LOCK:
                self.send_json({k: v for k, v in JOB.items()})
            return

        if route == "/runs":
            runs = []
            arch = ROOT / "archive"
            if arch.is_dir():
                for d in sorted(arch.iterdir(), reverse=True):
                    sig = d / "signal.json"
                    if not sig.is_file():
                        continue
                    try:
                        meta = json.loads(sig.read_text(encoding="utf-8")).get("meta", {})
                    except (OSError, ValueError):
                        meta = {}
                    runs.append({
                        "dir": d.name,
                        "subject": meta.get("subject", d.name),
                        "n_videos": meta.get("n_videos"),
                        "generated_at": meta.get("generated_at", ""),
                    })
            self.send_json(runs[:20])
            return

        if route == "/compare":
            q = parse_qs(url.query)
            ref_a = (q.get("a") or [""])[0]
            ref_b = (q.get("b") or [""])[0]
            try:
                diff = compare_signals(load_signal(ref_a), load_signal(ref_b))
                self.send_json({"ok": True, "diff": diff})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)})
            except (OSError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": f"Could not read the runs: {exc}"})
            return

        if route == "/configs":
            items = []
            for p in sorted(CONFIG_DIR.glob("*.json")):
                if p.name == "TEMPLATE.json":
                    continue
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                    items.append({"file": p.name, "name": raw.get("subject", {}).get("name", p.stem)})
                except ValueError:
                    continue
            self.send_json(items)
            return

        # the finished dashboard, served from web/
        if route == "/dashboard" or route == "/dashboard/":
            self.send_file(WEB_DIR / "index.html")
            return
        if route.startswith("/dashboard/"):
            rel = route[len("/dashboard/"):]
            target = (WEB_DIR / rel).resolve()
            if WEB_DIR.resolve() in target.parents or target == WEB_DIR.resolve():
                self.send_file(target)
            else:
                self.send_error(403)
            return
        # the dashboard fetches ./signal.json and ./videos.json relative to /dashboard
        if route in ("/signal.json", "/videos.json", "/timeline.json"):
            self.send_file(WEB_DIR / route.lstrip("/"))
            return

        self.send_error(404)

    def do_POST(self) -> None:                       # noqa: N802
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"

        if url.path == "/derive":
            try:
                query = (json.loads(raw).get("query") or "").strip()
            except ValueError:
                self.send_json({"ok": False, "error": "malformed request"}, 400); return
            try:
                proposal = derive_mod.derive_brand(query)
                # A searched handle beats a recalled one when the search is
                # confident; it stays silent otherwise, so the guess survives.
                found = derive_mod.search_handle(proposal["name"])
                if found:
                    proposal["handle"] = found["handle"]
                    proposal["handle_confidence"] = "verified by search"
                for c in proposal.get("competitors", []):
                    hit = derive_mod.search_handle(c["name"])
                    if hit:
                        c["handle"] = hit["handle"]
                self.send_json({"ok": True, "proposal": proposal})
            except derive_mod.DeriveError as exc:
                self.send_json({"ok": False, "error": str(exc)})
            except Exception as exc:                       # noqa: BLE001
                self.send_json({"ok": False, "error": f"Derivation failed: {exc}"})
            return

        if url.path == "/verify":
            try:
                accounts = [(a[0], a[1]) for a in json.loads(raw).get("accounts") or []]
            except (ValueError, IndexError, TypeError):
                self.send_json({"ok": False, "error": "malformed request"}, 400); return
            try:
                results = derive_mod.verify_accounts(accounts)
                try:
                    payload = json.loads(raw)
                    tag_probe = derive_mod.probe_hashtags(payload.get("hashtags") or [])
                except (derive_mod.DeriveError, ValueError):
                    tag_probe = {}
                for r in results.values():
                    if r.get("ok"):
                        VERIFIED_HANDLES.add(str(r.get("handle", "")).lstrip("@").lower())
                self.send_json({"ok": True, "results": results, "hashtags": tag_probe})
            except derive_mod.DeriveError as exc:
                self.send_json({"ok": False, "error": str(exc)})
            return

        if url.path == "/restore":
            with JOB_LOCK:
                if JOB["running"]:
                    self.send_json({"ok": False, "error": "A run is in progress; stop it before restoring."})
                    return
            try:
                run_dir = str(json.loads(raw).get("dir", ""))
            except ValueError:
                self.send_json({"ok": False, "error": "malformed request"}, 400)
                return
            try:
                message = restore_run(run_dir)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)})
                return
            except OSError as exc:
                self.send_json({"ok": False, "error": f"Restore failed: {exc}"})
                return
            self.send_json({"ok": True, "message": message})
            return

        if url.path == "/stop":
            with JOB_LOCK:
                proc = JOB.get("_proc")
                running = JOB["running"]
                JOB["_stop_requested"] = True
            if not running:
                self.send_json({"ok": False, "error": "Nothing is running."})
                return
            if proc is not None:
                try:
                    proc.terminate()
                except OSError:
                    pass
            self.send_json({"ok": True})
            return

        if url.path == "/start":
            try:
                payload = json.loads(raw)
            except ValueError:
                self.send_json({"ok": False, "error": "malformed request"}, 400)
                return
            self.send_json(self.start_job(payload))
            return

        self.send_error(404)

    # -- the work --
    def start_job(self, p: Dict[str, Any]) -> Dict[str, Any]:
        # Claim the job here, under the lock, not in the worker thread: between
        # the check and the thread's first instruction there was a window in
        # which a double click started two pipelines against the same files.
        with JOB_LOCK:
            if JOB["running"]:
                return {"ok": False, "error": "A run is already in progress."}
            JOB["running"] = True

        def fail(msg: str) -> Dict[str, Any]:
            with JOB_LOCK:
                JOB["running"] = False
            return {"ok": False, "error": msg}

        name = (p.get("name") or "").strip()
        if not name:
            return fail("The brand needs a name.")
        key = slug(p.get("key") or name)
        handle = norm_handle(p.get("handle") or "")
        tag = slug(p.get("hashtag") or key)
        if not handle:
            return fail("The brand needs a TikTok handle.")

        competitors: List[Dict[str, str]] = []
        for c in p.get("competitors") or []:
            cname = (c.get("name") or "").strip()
            if not cname:
                continue
            competitors.append({
                "key": slug(c.get("key") or cname),
                "name": cname,
                "handle": norm_handle(c.get("handle") or ""),
                "hashtag": slug(c.get("hashtag") or cname),
            })
        if not competitors:
            return fail("Add at least one competitor: without a category there is nothing to compare against.")

        brief = p.get("brief") or ""
        if brief_is_template(brief):
            return fail("The brief is still the template. Every brand fit score answers to it, so an unwritten one produces numbers that look authoritative and mean nothing.")
        if len(brief.strip()) < 400:
            return fail("The brief is too short to score against. Describe what is on brand, what is off brand, and where the bands sit.")

        try:
            pool = max(1, int(p.get("pool", 10)))
            htag = max(0, int(p.get("htag", 5)))
        except (TypeError, ValueError):
            return fail("Pool sizes must be numbers.")

        brief_path = CONFIG_DIR / f"{key}-brief.md"
        brief_path.write_text(brief, encoding="utf-8")
        config_path = CONFIG_DIR / f"{key}.json"
        config_path.write_text(json.dumps({
            "subject": {"key": key, "name": name, "handle": handle, "hashtag": tag},
            "competitors": competitors,
            "brief": f"config/{key}-brief.md",
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        handle_key = handle.lstrip("@").lower()
        if handle_key not in VERIFIED_HANDLES:
            try:
                res = derive_mod.verify_accounts([(key, handle)])
                verdict = res.get(key, {})
                if not verdict.get("exists"):
                    return fail(
                        "The subject handle {} returned nothing on TikTok, so the "
                        "scrape would spend money on the wrong account. Fix the "
                        "handle, or use Check the handles to see what is there.".format(handle)
                    )
                if verdict.get("ok"):
                    VERIFIED_HANDLES.add(handle_key)
                else:
                    log_note = verdict.get("note", "")
                    return fail(
                        "The subject handle {} looks wrong: {} Use Check the "
                        "handles, fix it, and run again. If you are certain it is "
                        "right, verify it once and the run will proceed.".format(handle, log_note)
                    )
            except derive_mod.DeriveError:
                pass    # verification unavailable (no key): do not block the run

        # One cheap probe over every configured hashtag before the real spend.
        # A dead subject hashtag makes the owners versus brand story an
        # artifact of missing data, which is exactly what happened once.
        try:
            all_tags = [tag] + [c["hashtag"] for c in competitors]
            probe = derive_mod.probe_hashtags(all_tags)
        except derive_mod.DeriveError:
            probe = {}
        subject_state = (probe.get(tag) or {}).get("state")
        if subject_state == "dead":
            return fail(
                "The subject hashtag #{} returns nothing on TikTok, so the corpus "
                "would have no owner or creator content for {} and the headline "
                "insight would be an artifact. Pick a different hashtag "
                "(a compound like {}official or {}clothing often works).".format(tag, name, tag, tag)
            )
        if subject_state == "weak":
            return fail(
                "The subject hashtag #{} has no tag page on TikTok, only fuzzy "
                "search matches. That usually means a typo or a tag nobody uses. "
                "Check it and run again.".format(tag)
            )
        self._probe_notes = [
            "⚠️  Competitor hashtag #{} looks {} on TikTok; its owner pool may come back thin.".format(k, v["state"])
            for k, v in probe.items() if k != tag and v.get("state") != "alive"
        ]

        try:
            timeline_pool = max(0, int(p.get("timeline_pool", 0)))
        except (TypeError, ValueError):
            timeline_pool = 0
        notes = getattr(self, "_probe_notes", [])
        threading.Thread(
            target=run_pipeline, args=(config_path, pool, htag, timeline_pool, notes), daemon=True
        ).start()
        return {"ok": True, "config": config_path.name}


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TikTok Analyzer · new brand</title>
<style>
  :root{
    --bg:#0E0D0B; --panel:#15140F; --line:#2A2822; --ink:#ECE6DA; --ink-2:#A9A296;
    --ink-3:#6E675C; --accent:#D2451E; --good:#7FA05A; --warn:#C8A24B;
    --sans:ui-sans-serif,-apple-system,"Helvetica Neue",Arial,sans-serif;
    --mono:ui-monospace,"SF Mono",Menlo,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--sans)}
  .wrap{max-width:940px;margin:0 auto;padding:48px 28px 96px}
  h1{font-size:26px;margin:0 0 6px;letter-spacing:-.01em}
  .sub{color:var(--ink-2);margin:0 0 34px;max-width:62ch}
  fieldset{border:1px solid var(--line);border-radius:10px;background:var(--panel);
    padding:22px 24px;margin:0 0 20px}
  legend{padding:0 10px;font:600 11px/1 var(--mono);letter-spacing:.14em;
    text-transform:uppercase;color:var(--ink-3)}
  .hint{color:var(--ink-3);font-size:13px;margin:-4px 0 18px;max-width:66ch}
  .row{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}
  label{display:block;font-size:12px;color:var(--ink-2);margin:0 0 5px;
    letter-spacing:.04em;text-transform:uppercase}
  input,textarea{width:100%;background:#100F0C;color:var(--ink);border:1px solid var(--line);
    border-radius:7px;padding:9px 11px;font:14px var(--sans)}
  textarea{font:13px/1.6 var(--mono);resize:vertical;min-height:340px}
  input:focus,textarea:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:transparent}
  .comp{border-top:1px solid var(--line);padding-top:16px;margin-top:16px}
  .flags{margin-top:10px;font-size:13px;color:var(--warn);min-height:19px}
  button{font:600 14px var(--sans);border-radius:8px;padding:11px 20px;cursor:pointer;border:1px solid transparent}
  .primary{background:var(--accent);color:#fff}
  .primary:disabled{background:#3A2A22;color:var(--ink-3);cursor:not-allowed}
  .ghost{background:transparent;color:var(--ink-2);border-color:var(--line)}
  .bar{display:flex;gap:12px;align-items:center;margin-top:22px;flex-wrap:wrap}
  .cost{color:var(--ink-2);font-size:13px}
  .err{color:#E06B4A;font-size:14px;margin-top:14px;white-space:pre-wrap}
  #run{display:none}
  .steps{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 16px}
  .step{font:11px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;
    padding:7px 11px;border:1px solid var(--line);border-radius:999px;color:var(--ink-3)}
  .step.on{color:var(--ink);border-color:var(--accent)}
  .step.done{color:var(--good);border-color:#31402A}
  pre{background:#0A0907;border:1px solid var(--line);border-radius:8px;padding:14px;
    max-height:360px;overflow:auto;font:12px/1.5 var(--mono);color:var(--ink-2);white-space:pre-wrap}
  a.dash{display:inline-block;margin-top:16px}
</style></head><body><div class="wrap">

<h1>Point the analyzer at a new brand</h1>
<p class="sub">The subject is the brand you are advising: its own posts become the baseline the
gap analysis measures against. The competitors supply the category.</p>

<fieldset id="deriveBox"><legend>Start from a name or a URL</legend>
  <p class="hint">Fills everything below, including a draft brief. The competitors and the
  brief are a good starting point. The handles are not: a model will invent a plausible one,
  and a wrong handle scrapes a fan account and quietly inverts the whole analysis. They get
  searched and checked against TikTok, and anything unconvincing is flagged for you to fix.</p>
  <div class="row">
    <div style="grid-column:1/-1"><label for="seed">Brand name or website</label>
      <input id="seed" placeholder="patagonia.com"></div>
  </div>
  <div class="bar">
    <button type="button" class="primary" id="derive">Derive</button>
    <span class="cost" id="deriveState"></span>
  </div>
  <div class="err" id="deriveErr"></div>
</fieldset>

<form id="form">
  <fieldset><legend>Subject</legend>
    <div class="row">
      <div><label for="name">Brand</label><input id="name" required placeholder="Nike"></div>
      <div><label for="handle">TikTok handle</label><input id="handle" required placeholder="@nike"></div>
      <div><label for="hashtag">Hashtag</label><input id="hashtag" placeholder="nike"></div>
    </div>
    <div class="flags" id="flagSubject"></div>
  </fieldset>

  <fieldset><legend>Competitors</legend>
    <p class="hint">Two is the tested shape: the closest challenger and the category anchor.</p>
    <div id="comps"></div>
    <div class="bar">
      <button type="button" class="ghost" id="addComp">Add another</button>
      <button type="button" class="ghost" id="verify">Check handles and hashtags on TikTok</button>
    </div>
    <div class="flags" id="flagComp"></div>
    <div id="verifyOut"></div>
  </fieldset>

  <fieldset><legend>Brief</legend>
    <p class="hint">This is the rubric every brand fit score answers to, and the only thing the
    pipeline knows about the brand. Source it from the real positioning. Left as the template,
    it produces numbers that look authoritative and mean nothing, so the run will refuse to start.</p>
    <textarea id="brief" spellcheck="false"></textarea>
  </fieldset>

  <fieldset><legend>Scrape</legend>
    <p class="hint">The only step that spends money. Apify's free tier covers roughly 50 videos a month.</p>
    <div class="row">
      <div><label for="pool">Profile videos per brand</label><input id="pool" type="number" min="1" value="10"></div>
      <div><label for="htag">Hashtag videos per brand</label><input id="htag" type="number" min="0" value="5"></div>
      <div><label for="tlpool">Timeline: subject posts, metadata only (0 = skip)</label><input id="tlpool" type="number" min="0" value="150"></div>
    </div>
    <div class="bar">
      <button class="primary" id="go" type="submit">Run</button>
      <span class="cost" id="cost"></span>
    </div>
    <div class="err" id="err"></div>
  </fieldset>
</form>

<div id="run">
  <fieldset><legend>Progress</legend>
    <div class="steps" id="steps"></div>
    <div class="bar" style="margin-bottom:12px"><button type="button" class="ghost" id="stopBtn">Stop the run</button></div>
    <div id="warnings"></div>
    <pre id="log"></pre>
    <div id="done"></div>
  </fieldset>
</div>

<fieldset style="margin-top:20px"><legend>Past runs</legend>
  <p class="hint">Every run archives the previous one before touching anything. These are the snapshots in archive/.</p>
  <div id="runsList" class="hint">loading…</div>
  <div class="bar" style="margin-top:16px">
    <select id="cmpA" style="background:#100F0C;color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:8px"></select>
    <span class="hint" style="margin:0">→</span>
    <select id="cmpB" style="background:#100F0C;color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:8px"></select>
    <button type="button" class="ghost" id="cmpBtn">Compare</button>
  </div>
  <div id="cmpOut"></div>
</fieldset>

<script>
const TEMPLATE = __TEMPLATE_BRIEF__;
const AMBIGUOUS = ["genesis","patagonia","apple","orange","shell","gap","target","amazon",
  "puma","jaguar","polo","columbia","arc","north","face","canada","moon","sport","vans","coach","guess"];
const $ = id => document.getElementById(id);
const slug = s => (s||"").toLowerCase().replace(/[^a-z0-9]/g,"");

$("brief").value = TEMPLATE;

function compRow(i){
  const d = document.createElement("div");
  d.className = "comp";
  d.innerHTML = `<div class="row">
    <div><label>Competitor ${i}</label><input class="cname" placeholder="Adidas"></div>
    <div><label>Handle</label><input class="chandle" placeholder="@adidas"></div>
    <div><label>Hashtag</label><input class="ctag" placeholder="adidas"></div>
  </div>`;
  return d;
}
let n = 0;
function addComp(){ n++; $("comps").appendChild(compRow(n)); wire(); }
addComp(); addComp();
$("addComp").onclick = addComp;

function tagWarning(tag){
  const t = slug(tag);
  if(!t) return "";
  if(AMBIGUOUS.includes(t))
    return `“#${t}” on TikTok is mostly a different subject: a place, a band, a common word. `
         + `Something like “${t}gear” or “${t}official” keeps the pool on the brand.`;
  if(t.length <= 2) return `“#${t}” is very short and will pull in anything.`;
  return "";
}

function wire(){
  document.querySelectorAll("input").forEach(el => { el.oninput = update; });
}
function update(){
  const brands = 1 + document.querySelectorAll(".cname").length;
  const pool = Number($("pool").value||0), ht = Number($("htag").value||0);
  const total = (pool + ht) * brands;
  let costMsg = `${total} videos across ${brands} brands, plus comments and video files.`;
  if(pool + ht < 8) costMsg += " Below about 8 per brand the tiers are anecdotes, not a baseline.";
  $("cost").textContent = costMsg;
  $("flagSubject").textContent = tagWarning($("hashtag").value || $("name").value);
  let cw = "";
  document.querySelectorAll(".comp").forEach(c => {
    const t = c.querySelector(".ctag").value || c.querySelector(".cname").value;
    const w = tagWarning(t);
    if(w && !cw) cw = w;
  });
  $("flagComp").textContent = cw;
}
wire(); update();

function setComps(list){
  $("comps").innerHTML = ""; n = 0;
  (list.length ? list : [{},{}]).forEach(c => {
    addComp();
    const row = $("comps").lastElementChild;
    row.querySelector(".cname").value   = c.name || "";
    row.querySelector(".chandle").value = c.handle || "";
    row.querySelector(".ctag").value    = c.hashtag || "";
  });
}

$("derive").onclick = async () => {
  const q = $("seed").value.trim();
  if(!q){ $("deriveErr").textContent = "Give me a brand name or a website."; return; }
  $("derive").disabled = true;
  $("deriveErr").textContent = "";
  $("deriveState").textContent = "Reading the brand, drafting the brief, searching the handles. About a minute.";
  try{
    const r = await fetch("/derive", {method:"POST", body: JSON.stringify({query:q})}).then(r=>r.json());
    if(!r.ok){ $("deriveErr").textContent = r.error; return; }
    const p = r.proposal;
    $("name").value = p.name || ""; $("handle").value = p.handle || ""; $("hashtag").value = p.hashtag || "";
    setComps(p.competitors || []);
    if(p.brief) $("brief").value = p.brief;
    $("deriveState").textContent =
      `Filled in. Brief drafted ${p.site_text_used ? "from the site" : "from the model's own knowledge"}, `
      + `handle confidence ${p.handle_confidence || "unknown"}. Read the brief and check the handles before running.`;
    update();
  } finally { $("derive").disabled = false; }
};

$("verify").onclick = async () => {
  const accounts = [[slug($("name").value)||"subject", $("handle").value]];
  const hashtags = [slug($("hashtag").value || $("name").value)];
  document.querySelectorAll(".comp").forEach(c => {
    const nm = c.querySelector(".cname").value, h = c.querySelector(".chandle").value;
    if(nm && h) accounts.push([slug(nm), h]);
    const ht = slug(c.querySelector(".ctag").value || nm);
    if(ht) hashtags.push(ht);
  });
  $("verify").disabled = true;
  $("verifyOut").innerHTML = `<p class="hint">Asking TikTok for one video per account...</p>`;
  try{
    const r = await fetch("/verify", {method:"POST", body: JSON.stringify({accounts, hashtags})}).then(r=>r.json());
    if(!r.ok){ $("verifyOut").innerHTML = `<p class="err">${r.error}</p>`; return; }
    $("verifyOut").innerHTML = Object.entries(r.results).map(([k,v]) => {
      const colour = v.ok ? "var(--good)" : (v.exists ? "var(--warn)" : "#E06B4A");
      const state  = v.ok ? "looks right" : (v.exists ? "suspect" : "wrong");
      const fol    = v.followers != null ? ` · ${v.followers.toLocaleString()} followers` : "";
      const badge  = v.verified ? " · verified" : "";
      return `<div style="margin-top:10px;font-size:13px">
        <span style="color:${colour};font-weight:600">${state}</span>
        <span style="color:var(--ink-2)"> ${k} ${v.handle}${fol}${badge}</span>
        ${v.note ? `<div style="color:var(--ink-3);margin-top:2px">${v.note}</div>` : ""}
      </div>`;
    }).join("") + Object.entries(r.hashtags || {}).map(([tag, v]) => {
      const colour = v.state === "alive" ? "var(--good)" : (v.state === "weak" ? "var(--warn)" : "#E06B4A");
      const label = v.state === "alive" ? "alive" : (v.state === "weak" ? "no tag page, fuzzy matches only" : "dead");
      const views = v.views ? ` · ${(v.views/1e6).toFixed(1)}M tag views` : "";
      return `<div style="margin-top:10px;font-size:13px">
        <span style="color:${colour};font-weight:600">#${tag}</span>
        <span style="color:var(--ink-2)"> ${label}${views}</span>
      </div>`;
    }).join("");
  } finally { $("verify").disabled = false; }
};

$("stopBtn") && ($("stopBtn").onclick = async () => {
  $("stopBtn").disabled = true;
  await fetch("/stop", {method:"POST", body:"{}"}).catch(()=>{});
});

async function loadRuns(){
  try{
    const runs = await fetch("/runs").then(r=>r.json());
    $("runsList").innerHTML = runs.length
      ? runs.map(r => `<div style="margin-top:8px;font-family:var(--mono);font-size:12px;color:var(--ink-2);display:flex;gap:10px;align-items:center">`
          + `<button type="button" class="ghost" style="padding:4px 10px;font-size:11px" data-restore="${r.dir}">Restore</button>`
          + `<span>${r.dir} · ${r.subject || "?"} · ${r.n_videos ?? "?"} videos</span></div>`).join("")
      : "none yet";
    document.querySelectorAll("[data-restore]").forEach(b => b.onclick = async () => {
      b.disabled = true; b.textContent = "…";
      const r = await fetch("/restore", {method:"POST", body: JSON.stringify({dir: b.dataset.restore})}).then(r=>r.json());
      b.textContent = r.ok ? "Restored" : "Failed";
      if(!r.ok) alert(r.error);
      else b.insertAdjacentHTML("afterend", `<a href="/dashboard" style="font-size:11px">open dashboard</a>`);
      loadRuns;
    });
    const opts = [`<option value="current">current</option>`]
      .concat(runs.map(r => `<option value="${r.dir}">${r.dir}</option>`)).join("");
    $("cmpA").innerHTML = opts; $("cmpB").innerHTML = opts;
    if(runs.length) $("cmpA").value = runs[0].dir;
  } catch(e){ $("runsList").textContent = "unavailable"; }
}
loadRuns();

$("cmpBtn").onclick = async () => {
  $("cmpBtn").disabled = true;
  $("cmpOut").innerHTML = `<p class="hint">comparing…</p>`;
  try{
    const r = await fetch(`/compare?a=${encodeURIComponent($("cmpA").value)}&b=${encodeURIComponent($("cmpB").value)}`).then(r=>r.json());
    if(!r.ok){ $("cmpOut").innerHTML = `<p class="err">${r.error}</p>`; return; }
    const d = r.diff;
    const arrow = v => v > 0 ? `<span style="color:var(--good)">+${v}</span>` : (v < 0 ? `<span style="color:#E06B4A">${v}</span>` : `<span style="color:var(--ink-3)">0</span>`);
    let h = `<div style="margin-top:14px;font-family:var(--mono);font-size:12px;line-height:2;color:var(--ink-2)">`;
    h += `<div style="color:var(--ink)">${d.subject} · ${d.a.generated_at.slice(0,10)} (${d.a.n_videos} videos) → ${d.b.generated_at.slice(0,10)} (${d.b.n_videos} videos)</div>`;
    if(d.size_caveat) h += `<div style="color:var(--warn)">⚠ ${d.size_caveat}</div>`;
    if(d.owner_share) h += `<div>owner share: ${d.owner_share.a}% → ${d.owner_share.b}% (${arrow(d.owner_share.delta)})</div>`;
    d.gap.common.forEach(g => { h += `<div>fit ${arrow(g.fit_delta)} · ${g.trait} (${g.fit_a.toFixed(2)} → ${g.fit_b.toFixed(2)})</div>`; });
    d.gap.appeared.forEach(t2 => { h += `<div style="color:var(--good)">new gap row: ${t2}</div>`; });
    d.gap.disappeared.forEach(t2 => { h += `<div style="color:var(--ink-3)">gone: ${t2}</div>`; });
    d.patterns.appeared.forEach(t2 => { h += `<div style="color:var(--good)">new winning pattern: ${t2}</div>`; });
    d.patterns.disappeared.forEach(t2 => { h += `<div style="color:var(--ink-3)">no longer winning: ${t2}</div>`; });
    $("cmpOut").innerHTML = h + `</div>`;
  } finally { $("cmpBtn").disabled = false; }
};

$("form").onsubmit = async e => {
  e.preventDefault();
  $("err").textContent = "";
  $("go").disabled = true;
  const competitors = [...document.querySelectorAll(".comp")].map(c => ({
    name: c.querySelector(".cname").value,
    handle: c.querySelector(".chandle").value,
    hashtag: c.querySelector(".ctag").value,
  })).filter(c => c.name.trim());
  const payload = {
    name: $("name").value, handle: $("handle").value, hashtag: $("hashtag").value,
    competitors, brief: $("brief").value,
    pool: Number($("pool").value), htag: Number($("htag").value),
    timeline_pool: Number($("tlpool").value||0),
  };
  const r = await fetch("/start", {method:"POST", body: JSON.stringify(payload)}).then(r=>r.json());
  if(!r.ok){ $("err").textContent = r.error; $("go").disabled = false; return; }
  $("form").style.display = "none";
  $("run").style.display = "block";
  poll();
};

const LABELS = ["Scraping TikTok","Reading the videos","Building the signal","Building the table","Building the timeline"];
async function poll(){
  const s = await fetch("/status").then(r=>r.json());
  $("steps").innerHTML = LABELS.slice(0, s.steps_total || LABELS.length).map((l,i) => {
    const cls = i < s.steps_done ? "done" : (l === s.step ? "on" : "");
    return `<span class="step ${cls}">${l}</span>`;
  }).join("");
  const warn = (s.log||[]).filter(l => l.includes("\u26a0") || l.startsWith("ERROR") || l.includes("\u274c"));
  $("warnings").innerHTML = warn.length
    ? `<div style="border:1px solid var(--warn);border-radius:8px;padding:10px 14px;margin-bottom:12px;font-size:13px;color:var(--warn)">`
      + warn.map(w => `<div>${w.replace(/</g,"&lt;")}</div>`).join("") + `</div>`
    : "";
  const log = $("log");
  const stuck = log.scrollTop + log.clientHeight >= log.scrollHeight - 24;
  log.textContent = (s.log||[]).join("\n");
  if(stuck) log.scrollTop = log.scrollHeight;
  if(s.finished){
    loadRuns();
    $("done").innerHTML = s.error
      ? `<p class="err">${s.error}</p>`
      : `<a class="dash primary" style="text-decoration:none;padding:11px 20px;display:inline-block;border-radius:8px" href="/dashboard">Open the dashboard</a>`;
    return;
  }
  setTimeout(poll, 1000);
}
</script>
</div></body></html>
"""


class Server(ThreadingHTTPServer):
    """
    ThreadingHTTPServer without the reverse DNS lookup.

    HTTPServer.server_bind calls socket.getfqdn() purely to fill in server_name.
    In a normal shell that returns instantly; launched from a .app bundle it can
    block for a long time, which looks exactly like the server failing to start:
    the process is alive, the port never opens, and nothing is logged. Binding
    through TCPServer directly skips the lookup, and server_name is only used in
    headers we do not depend on.
    """

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


def main() -> None:
    ap = argparse.ArgumentParser(description="Local panel for configuring a new brand.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    if not TEMPLATE_BRIEF.is_file():
        raise SystemExit(f"error: {TEMPLATE_BRIEF} is missing")

    server = Server(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"TikTok Analyzer panel on {url}  (ctrl-c to stop)")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
