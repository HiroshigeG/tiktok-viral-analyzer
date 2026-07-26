#!/usr/bin/env python3
"""
build_videos_json.py  --  the per video table that backs the dashboard.

signal.json carries the argument: patterns, baseline, gap, brief. It deliberately
does not carry every video, because the schema is the pitch contract and it stays
small. The dashboard's sortable table needs the long tail, so it fetches a second
file, web/videos.json.

That file used to be produced by hand, which meant it drifted from the corpus and
could not be rebuilt after a new run. This script derives it from the same two
inputs everything else uses.

    data/raw/<brand>.json   metrics, author, signals, comments  (Contract A)
    data/analysis.json      craft, hook_type, brand_fit, who    (Contract B)
                         -> web/videos.json

Only the brands named in the active config are included, so a checkout that has
run more than one brand set does not leak one into another.

Usage:
    python build_videos_json.py
    python build_videos_json.py --config config/other.json --output web/videos.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from brandconfig import BrandConfigError, add_config_argument, load_config


def load_analysis(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return {str(v.get("id")): v for v in data.get("videos", [])}


def build(data_dir: Path, brand_keys: List[str]) -> List[Dict[str, Any]]:
    analysis = load_analysis(data_dir / "analysis.json")
    rows: List[Dict[str, Any]] = []

    for key in brand_keys:
        raw_path = data_dir / "raw" / f"{key}.json"
        if not raw_path.is_file():
            continue
        with raw_path.open(encoding="utf-8") as fh:
            brand_data = json.load(fh)

        for v in brand_data.get("videos", []):
            vid = str(v.get("id"))
            a = analysis.get(vid, {})
            m = v.get("metrics", {})
            sound = (v.get("signals", {}) or {}).get("sound", {}) or {}
            comments = v.get("comments", []) or []
            # The single most liked comment is the one worth surfacing in a row;
            # the full cluster analysis lives in signal.json.
            top_comment = ""
            if comments:
                top_comment = str(
                    max(comments, key=lambda c: c.get("likes", 0)).get("text", "")
                )

            rows.append(
                {
                    "id": vid,
                    "brand": v.get("brand", key),
                    "who": a.get("who", ""),
                    "tier": v.get("performance_tier", ""),
                    "source": v.get("source", ""),
                    "views": m.get("views", 0),
                    "engagement": m.get("engagement_rate", 0.0),
                    "likes": m.get("likes", 0),
                    "comments": m.get("comments", 0),
                    "hook_type": a.get("hook_type", ""),
                    "brand_fit": (a.get("brand_fit", {}) or {}).get("score", 0.0),
                    "sound_is_original": sound.get("is_original"),
                    "sound_title": sound.get("title", ""),
                    "duration_sec": (v.get("signals", {}) or {}).get("duration_sec", 0),
                    "url": v.get("url", ""),
                    "thumb": "assets/thumbs/{}.jpg".format(vid),
                    "why": (a.get("craft", {}) or {}).get("why", []),
                    "top_comment": top_comment,
                }
            )

    rows.sort(key=lambda r: r.get("engagement", 0.0), reverse=True)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build web/videos.json, the per video table behind the dashboard."
    )
    parser.add_argument("--data-dir", default="data", help="Directory holding raw/ and analysis.json.")
    parser.add_argument("--output", default="web/videos.json", help="Where to write the table.")
    add_config_argument(parser)
    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except BrandConfigError as exc:
        raise SystemExit("error: {}".format(exc))

    rows = build(Path(args.data_dir), cfg.keys)
    if not rows:
        raise SystemExit(
            "error: no videos found. Run ingest.py and analyze.py first, and check "
            "that the config names the brands present in {}/raw/.".format(args.data_dir)
        )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump({"videos": rows}, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    missing = sum(1 for r in rows if not r["hook_type"])
    print("Wrote {}  ({} videos, brands: {})".format(out, len(rows), cfg.keys))
    if missing:
        print("  note: {} rows have no analysis record yet; run analyze.py".format(missing))


if __name__ == "__main__":
    main()
