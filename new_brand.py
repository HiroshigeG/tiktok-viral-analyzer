#!/usr/bin/env python3
"""
new_brand.py  --  point the pipeline at a brand it has never seen.

Asks what it needs, writes the config and a brief pre-filled with the brand's
name, then optionally runs the whole pipeline and leaves you with a dashboard.

    python3 new_brand.py

Two things it deliberately refuses to rush:

  The brief. Every brand_fit score in the output answers to it. A brief left as
  the template produces numbers that look authoritative and mean nothing, so the
  wizard will not run the pipeline until you have actually written it.

  The scrape. It is the only step that spends money. You get the pool size, the
  video count and a rough cost, and nothing is scraped until you say yes.

Usage:
    python3 new_brand.py                 # interactive
    python3 new_brand.py --config-only   # write the files, run nothing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from runstore import archive_previous_run
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent
CONFIG_DIR = ROOT / "config"
TEMPLATE_BRIEF = CONFIG_DIR / "TEMPLATE-brief.md"

# Words that are a whole other subject on TikTok before they are a brand. The
# Genesis hashtag returns a rock band; Patagonia returns a mountain range. A
# polluted pool produces confident nonsense, so the wizard pushes back.
AMBIGUOUS_TAGS = {
    "genesis", "patagonia", "apple", "orange", "shell", "gap", "target",
    "amazon", "puma", "jaguar", "polo", "columbia", "everlane", "arc",
    "north", "face", "canada", "moon", "sport", "vans", "coach", "guess",
}

BOLD = "\033[1m"
DIM = "\033[2m"
WARN = "\033[33m"
GOOD = "\033[32m"
BAD = "\033[31m"
OFF = "\033[0m"


def say(msg: str = "") -> None:
    print(msg)


def rule(title: str = "") -> None:
    print("\n" + BOLD + (title or "") + OFF)
    print(DIM + "─" * 62 + OFF)


def ask(prompt: str, default: str = "", required: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        raw = input(f"  {prompt}{suffix}: ").strip()
        value = raw or default
        if value or not required:
            return value
        say(f"  {BAD}serve una risposta{OFF}")


def ask_yes(prompt: str, default: bool = False) -> bool:
    d = "S/n" if default else "s/N"
    raw = input(f"  {prompt} [{d}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("s", "si", "sì", "y", "yes")


def ask_int(prompt: str, default: int, minimum: int = 0) -> int:
    """Numeric prompt that re-asks instead of dying on a typo."""
    while True:
        raw = ask(prompt, str(default))
        try:
            value = int(raw)
        except ValueError:
            say(f"  {BAD}serve un numero{OFF}")
            continue
        if value < minimum:
            say(f"  {BAD}minimo {minimum}{OFF}")
            continue
        return value


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def norm_handle(handle: str) -> str:
    handle = handle.strip()
    return handle if handle.startswith("@") else "@" + handle


def check_tag(tag: str, brand_key: str) -> None:
    """Warn when a hashtag is likely to return something other than the brand."""
    t = tag.lower().lstrip("#")
    if t in AMBIGUOUS_TAGS:
        say(
            f"  {WARN}attenzione{OFF}: '#{t}' su TikTok è dominato da un altro "
            "soggetto (un luogo, una band, una parola comune)."
        )
        say(
            f"  {DIM}prova qualcosa di più specifico, es. "
            f"'{t}gear', '{t}official', '{brand_key}clothing'{OFF}"
        )
    elif len(t) <= 2:
        say(f"  {WARN}attenzione{OFF}: '#{t}' è molto corto, rischia di pescare di tutto.")


def collect_brand(role: str, default_name: str = "") -> Dict[str, str]:
    name = ask(f"nome {role}", default_name)
    key = ask("chiave (minuscole, senza spazi)", slug(name))
    handle = norm_handle(ask("handle TikTok ufficiale", f"@{slug(name)}"))
    tag = ask("hashtag dove vive il contenuto di owner e creator", slug(name)).lstrip("#")
    check_tag(tag, key)
    return {"key": key, "name": name, "handle": handle, "hashtag": tag}


def write_brief(path: Path, brand_name: str) -> None:
    text = TEMPLATE_BRIEF.read_text(encoding="utf-8")
    text = text.replace("# [Brand] Brand Brief", f"# {brand_name} Brand Brief")
    text = text.replace("this brand", brand_name).replace("the brand's", f"{brand_name}'s")
    path.write_text(text, encoding="utf-8")


def brief_is_template(path: Path) -> bool:
    """True when the brief still reads like the untouched template."""
    text = path.read_text(encoding="utf-8")
    tells = ["- ...", "| ... |", "Three or four sentences", "Six or so bullets"]
    return sum(1 for t in tells if t in text) >= 2


def run_step(label: str, cmd: List[str], env: Dict[str, str]) -> bool:
    say(f"\n{BOLD}▶ {label}{OFF}")
    proc = subprocess.run(cmd, env=env, cwd=str(ROOT))
    if proc.returncode != 0:
        say(f"{BAD}✗ {label} è fallito (codice {proc.returncode}){OFF}")
        return False
    say(f"{GOOD}✓ {label}{OFF}")
    return True


def sync_web(config_path: Path) -> None:
    """Copy the signal and the thumbs it references into web/."""
    signal = ROOT / "signal.json"
    if not signal.is_file():
        return
    shutil.copy(signal, ROOT / "web" / "signal.json")
    sig = json.loads(signal.read_text(encoding="utf-8"))
    dest = ROOT / "web" / "assets" / "thumbs"
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    ids = {v["id"] for v in sig.get("segnale", {}).get("top_videos", [])}
    try:
        table = json.loads((ROOT / "web" / "videos.json").read_text(encoding="utf-8"))
        ids |= {v["id"] for v in table.get("videos", [])}
    except (OSError, ValueError):
        pass
    for vid in ids:
        src = ROOT / "data" / "raw" / "thumbs" / f"{vid}.jpg"
        if src.is_file():
            shutil.copy(src, dest / f"{vid}.jpg")
            copied += 1
    say(f"  {copied} thumb copiate in web/assets/thumbs/")


def main() -> None:
    parser = argparse.ArgumentParser(description="Configura un nuovo brand e lancia la pipeline.")
    parser.add_argument("--config-only", action="store_true",
                        help="Scrive config e brief, non esegue nulla.")
    args = parser.parse_args()

    if not TEMPLATE_BRIEF.is_file():
        raise SystemExit(f"error: manca {TEMPLATE_BRIEF}")

    rule("SIGNAL · nuovo brand")
    say(DIM + "  Il soggetto è il brand che stai consigliando: i suoi post diventano" + OFF)
    say(DIM + "  il termine di paragone dell'analisi. I competitor sono il campo." + OFF)
    say()

    rule("1. Il brand soggetto")
    subject = collect_brand("del brand", "")

    rule("2. I competitor")
    say(DIM + "  Due è la forma testata: lo sfidante più vicino e l'ancora di categoria." + OFF)
    say(DIM + "  Invio a vuoto sul nome per smettere." + OFF)
    competitors: List[Dict[str, str]] = []
    while True:
        say()
        name = ask(f"nome competitor {len(competitors) + 1} (vuoto per finire)", "", required=False)
        if not name:
            if not competitors:
                say(f"  {WARN}senza competitor non c'è categoria con cui confrontarsi{OFF}")
                if not ask_yes("procedo comunque?", False):
                    continue
            break
        key = ask("chiave", slug(name))
        handle = norm_handle(ask("handle TikTok", f"@{slug(name)}"))
        tag = ask("hashtag", slug(name)).lstrip("#")
        check_tag(tag, key)
        competitors.append({"key": key, "name": name, "handle": handle, "hashtag": tag})

    # ── Write the files ──────────────────────────────────────────────────
    key = subject["key"]
    config_path = CONFIG_DIR / f"{key}.json"
    brief_path = CONFIG_DIR / f"{key}-brief.md"

    if config_path.exists() and not ask_yes(f"{config_path.name} esiste già, lo sovrascrivo?", False):
        raise SystemExit("annullato.")

    config: Dict[str, Any] = {
        "subject": subject,
        "competitors": competitors,
        "brief": f"config/{key}-brief.md",
    }
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if brief_path.exists():
        say(f"\n  {brief_path.name} esiste già, lo lascio com'è.")
    else:
        write_brief(brief_path, subject["name"])

    rule("3. Il brief")
    say(f"  scritto: {BOLD}{brief_path}{OFF}")
    say(DIM + "  È la rubrica da cui dipende ogni punteggio brand_fit. Il resto della" + OFF)
    say(DIM + "  pipeline non sa nient'altro del brand. Compilalo davvero: prendilo" + OFF)
    say(DIM + "  dal posizionamento reale, non inventarlo." + OFF)

    editor = os.environ.get("EDITOR")
    if editor and ask_yes(f"\n  lo apro ora in {editor}?", True):
        subprocess.run([editor, str(brief_path)])

    if args.config_only:
        say(f"\n{GOOD}Fatto.{OFF} Per eseguire poi:")
        say(f"  export SIGNAL_BRAND_CONFIG={config_path.relative_to(ROOT)}")
        say("  python3 ingest.py --pool-size 10 --hashtag-pool-size 5")
        return

    if brief_is_template(brief_path):
        rule("Pipeline non avviata")
        say(f"  {WARN}Il brief è ancora il template.{OFF}")
        say("  Ogni punteggio uscirebbe autorevole e privo di significato.")
        say(f"\n  Compila {brief_path.name} e poi:")
        say(f"    export SIGNAL_BRAND_CONFIG={config_path.relative_to(ROOT)}")
        say("    python3 ingest.py --pool-size 10 --hashtag-pool-size 5")
        say("    python3 analyze.py && python3 build_signal.py && python3 build_videos_json.py")
        return

    # ── The scrape: the only step that costs ─────────────────────────────
    rule("4. Lo scrape")
    n_brands = 1 + len(competitors)
    pool = ask_int("video dal profilo per brand", 10, minimum=1)
    htag = ask_int("video dall'hashtag per brand", 5, minimum=0)
    total = (pool + htag) * n_brands
    say(f"\n  {BOLD}{total} video{OFF} da {n_brands} brand, più i commenti e i file video.")
    say(DIM + "  Apify: tier gratuito circa 50 video al mese. Gemini: 1500 al giorno, gratis." + OFF)
    if not ask_yes("\n  procedo?", False):
        say("  annullato. La config resta scritta.")
        return

    saved = archive_previous_run()
    if saved:
        say(f"  {DIM}run precedente archiviato in {saved.relative_to(ROOT)}{OFF}")

    env = dict(os.environ)
    env["SIGNAL_BRAND_CONFIG"] = str(config_path)

    steps = [
        ("scrape (Apify)", [sys.executable, "-u", "ingest.py",
                            "--pool-size", str(pool), "--hashtag-pool-size", str(htag)]),
        ("analisi video (Gemini) e strato strategico", [sys.executable, "-u", "analyze.py"]),
        ("sintesi del segnale", [sys.executable, "-u", "build_signal.py"]),
        ("tabella video", [sys.executable, "-u", "build_videos_json.py"]),
    ]
    for label, cmd in steps:
        if not run_step(label, cmd, env):
            say(f"\n{BAD}Interrotto.{OFF} La config è a posto, puoi riprendere da qui:")
            say(f"  export SIGNAL_BRAND_CONFIG={config_path.relative_to(ROOT)}")
            return

    rule("5. La dashboard")
    sync_web(config_path)
    say(f"\n{GOOD}Fatto.{OFF} Per vederla:")
    say(f"  cd web && python3 -m http.server 8080")
    say(DIM + "  poi apri http://localhost:8080  (con file:// il fetch non parte)" + OFF)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nannullato.")
