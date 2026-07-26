#!/usr/bin/env python3
"""
runstore.py  --  keep the previous run from being silently destroyed.

analyze.py overwrites data/analysis.json and build_signal.py overwrites
signal.json on every run. The first time the panel was used for real, that
erased a thirty record analysis whose strategic layer had cost actual API
money, and it was only recoverable because the file happened to be in git.

archive_previous_run() snapshots the small JSON outputs of whatever run is
currently on disk into archive/<subject>-<timestamp>/ before a new run starts.
It copies analysis.json, signal.json, and the per brand raw records. It does
NOT copy videos or thumbs: they are large, they are not overwritten by a
different brand set anyway, and the raw records are what the analysis can be
rebuilt from.

    from runstore import archive_previous_run
    dest = archive_previous_run()        # None when there was nothing to save
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent
DATA = ROOT / "data"
ARCHIVE = ROOT / "archive"


def _previous_subject() -> str:
    """Best available name for the run currently on disk."""
    for candidate in (ROOT / "signal.json", DATA / "analysis.json"):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        subject = str((payload.get("meta") or {}).get("subject", "")).strip()
        if subject:
            return re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-") or "run"
        videos = payload.get("videos") or []
        if videos:
            return str(videos[0].get("brand", "run"))
    return "run"


def archive_previous_run() -> Optional[Path]:
    """
    Snapshot the current run's JSON outputs before they are overwritten.

    Returns the archive directory, or None when there was no analysis on disk
    (a fresh checkout, nothing to lose). Never raises: failing to archive must
    not block a run, but it does its best and reports through the return value.
    """
    analysis = DATA / "analysis.json"
    if not analysis.is_file():
        return None

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = ARCHIVE / f"{_previous_subject()}-{stamp}"

    try:
        dest.mkdir(parents=True, exist_ok=False)
        shutil.copy(analysis, dest / "analysis.json")
        for name in ("signal.json", "timeline.json"):
            src = ROOT / name
            if src.is_file():
                shutil.copy(src, dest / name)
        table = ROOT / "web" / "videos.json"
        if table.is_file():
            shutil.copy(table, dest / "videos.json")

        # The raw records of the brands the current analysis covers. Small
        # JSON, and enough to rebuild the whole signal without rescraping.
        try:
            brands = {
                str(v.get("brand", "")).lower()
                for v in json.loads(analysis.read_text(encoding="utf-8")).get("videos", [])
            }
        except (OSError, ValueError):
            brands = set()
        for brand in sorted(b for b in brands if b):
            raw = DATA / "raw" / f"{brand}.json"
            if raw.is_file():
                shutil.copy(raw, dest / f"{brand}.json")
        return dest
    except OSError:
        return dest if dest.exists() else None


def restore_run(dir_name: str) -> str:
    """
    Bring an archived run back as the live one.

    The current state is archived first, so a restore is never destructive and
    two restores in a row simply swap back. Copies the analysis, the signal,
    the per brand raw records and, when the archive has them, the dashboard
    table and the timeline. Older archives predate table archiving; for those
    the table is rebuilt from the restored records, using the brand list the
    archived signal itself declares rather than whatever config is active.

    Returns a short human message. Raises ValueError on a bad directory.
    """
    name = Path(dir_name).name                     # no path traversal
    src = ARCHIVE / name
    if not src.is_dir() or not (src / "signal.json").is_file():
        raise ValueError("No archived run called {}.".format(name))

    archive_previous_run()

    if (src / "analysis.json").is_file():
        DATA.mkdir(exist_ok=True)
        shutil.copy(src / "analysis.json", DATA / "analysis.json")
    shutil.copy(src / "signal.json", ROOT / "signal.json")
    shutil.copy(src / "signal.json", ROOT / "web" / "signal.json")

    (DATA / "raw").mkdir(parents=True, exist_ok=True)
    sig = json.loads((src / "signal.json").read_text(encoding="utf-8"))
    brands = [str(b).lower() for b in (sig.get("meta") or {}).get("brands", [])]
    for brand in brands:
        raw = src / f"{brand}.json"
        if raw.is_file():
            shutil.copy(raw, DATA / "raw" / f"{brand}.json")

    if (src / "timeline.json").is_file():
        shutil.copy(src / "timeline.json", ROOT / "timeline.json")
        shutil.copy(src / "timeline.json", ROOT / "web" / "timeline.json")
    else:
        # A stale timeline under a restored signal describes the wrong subject.
        for stale in (ROOT / "web" / "timeline.json", ROOT / "timeline.json"):
            stale.unlink(missing_ok=True)

    if (src / "videos.json").is_file():
        shutil.copy(src / "videos.json", ROOT / "web" / "videos.json")
    else:
        try:
            from build_videos_json import build as build_table
            rows = build_table(DATA, brands)
            (ROOT / "web" / "videos.json").write_text(
                json.dumps({"videos": rows}, ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8",
            )
        except Exception:                          # noqa: BLE001
            (ROOT / "web" / "videos.json").write_text('{"videos": []}\n', encoding="utf-8")

    subject = (sig.get("meta") or {}).get("subject", name)
    return "Restored {} ({} videos).".format(subject, (sig.get("meta") or {}).get("n_videos", "?"))
