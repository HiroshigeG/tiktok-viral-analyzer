#!/usr/bin/env python3
"""
compare_runs.py  --  what changed between two runs of the same subject.

A signal.json is a snapshot. The question a strategist actually asks arrives a
month later: is the gap closing, did the winning pattern rotate, is the owner
share still growing? The archive keeps every snapshot; this module reads two of
them and states the differences, so the story becomes a film instead of a photo.

Comparisons are only honest between runs about the same subject, so a mismatch
is an error rather than a table of meaningless deltas. Corpus sizes ride along
with every number: a delta between a 6 video run and a 126 video run is shown,
but it is labeled for what it is.

    python3 compare_runs.py archive/adidas-20260726T101343 current
    python3 compare_runs.py archive/runA archive/runB
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent


def load_signal(ref: str) -> Dict[str, Any]:
    """
    Resolve "current", an archive directory name, or a path to a signal.json.
    """
    if ref == "current":
        path = ROOT / "signal.json"
    else:
        p = Path(ref)
        if p.is_dir():
            path = p / "signal.json"
        elif (ROOT / "archive" / Path(ref).name).is_dir():
            path = ROOT / "archive" / Path(ref).name / "signal.json"
        else:
            path = p
    if not path.is_file():
        raise ValueError("No signal at {}.".format(ref))
    return json.loads(path.read_text(encoding="utf-8"))


def _rows_by_trait(signal: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(g.get("category_winning_trait", "")): g
        for g in signal.get("insight", {}).get("brand_gap", [])
    }


def _patterns(signal: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(p.get("pattern", "")): p
        for p in signal.get("segnale", {}).get("winning_patterns", [])
    }


def compare(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """
    Diff two signals, oldest first. Pure function, raises ValueError when the
    subjects differ: a Lexus to Patagonia delta is not a comparison.
    """
    ma, mb = a.get("meta", {}), b.get("meta", {})
    sa = str(ma.get("subject", "")).strip().lower()
    sb = str(mb.get("subject", "")).strip().lower()
    if not sa or sa != sb:
        raise ValueError(
            "The runs are about different subjects ({} vs {}); the deltas would "
            "mean nothing.".format(ma.get("subject", "?"), mb.get("subject", "?"))
        )

    out: Dict[str, Any] = {
        "subject": ma.get("subject"),
        "a": {"generated_at": ma.get("generated_at", ""), "n_videos": ma.get("n_videos", 0)},
        "b": {"generated_at": mb.get("generated_at", ""), "n_videos": mb.get("n_videos", 0)},
        "size_caveat": "",
    }
    na, nb = out["a"]["n_videos"] or 0, out["b"]["n_videos"] or 0
    if na and nb and (max(na, nb) >= 3 * max(1, min(na, nb))):
        out["size_caveat"] = (
            "The corpora differ badly in size ({} against {} videos); read the "
            "deltas as direction, not measurement.".format(na, nb)
        )

    # ── owners versus brand ──────────────────────────────────────────────
    oa = a.get("insight", {}).get("owners_vs_brand", {}) or {}
    ob = b.get("insight", {}).get("owners_vs_brand", {}) or {}
    if "owner_share_pct" in oa or "owner_share_pct" in ob:
        pa = float(oa.get("owner_share_pct") or 0)
        pb = float(ob.get("owner_share_pct") or 0)
        out["owner_share"] = {"a": pa, "b": pb, "delta": round(pb - pa, 1)}

    # ── brand gap rows, matched by trait ─────────────────────────────────
    ra, rb = _rows_by_trait(a), _rows_by_trait(b)
    common, appeared, disappeared = [], [], []
    for trait, row in rb.items():
        if trait in ra:
            fa = float(ra[trait].get("brand_fit_score") or 0)
            fb = float(row.get("brand_fit_score") or 0)
            common.append({
                "trait": trait,
                "fit_a": fa, "fit_b": fb, "fit_delta": round(fb - fa, 2),
                "today_b": row.get("brand_today", ""),
            })
        else:
            appeared.append(trait)
    disappeared = [t for t in ra if t not in rb]
    out["gap"] = {"common": common, "appeared": appeared, "disappeared": disappeared}

    # ── winning patterns ─────────────────────────────────────────────────
    pa_, pb_ = _patterns(a), _patterns(b)
    out["patterns"] = {
        "appeared": [t for t in pb_ if t not in pa_],
        "disappeared": [t for t in pa_ if t not in pb_],
        "common": [
            {
                "pattern": t,
                "engagement_a": pa_[t].get("avg_engagement"),
                "engagement_b": pb_[t].get("avg_engagement"),
            }
            for t in pb_ if t in pa_
        ],
    }
    return out


def render_text(diff: Dict[str, Any]) -> str:
    lines: List[str] = []
    push = lines.append
    push("{}  ·  {} ({} videos)  →  {} ({} videos)".format(
        diff["subject"],
        diff["a"]["generated_at"][:10] or "?", diff["a"]["n_videos"],
        diff["b"]["generated_at"][:10] or "?", diff["b"]["n_videos"],
    ))
    if diff.get("size_caveat"):
        push("⚠  " + diff["size_caveat"])
    if "owner_share" in diff:
        o = diff["owner_share"]
        push("owner share of engagement: {}% → {}%  ({:+.1f})".format(o["a"], o["b"], o["delta"]))
    for row in diff["gap"]["common"]:
        push("fit {:+.2f}  {}  ({:.2f} → {:.2f})".format(
            row["fit_delta"], row["trait"], row["fit_a"], row["fit_b"]))
    for t in diff["gap"]["appeared"]:
        push("new gap row: " + t)
    for t in diff["gap"]["disappeared"]:
        push("gone from the gap: " + t)
    for t in diff["patterns"]["appeared"]:
        push("new winning pattern: " + t)
    for t in diff["patterns"]["disappeared"]:
        push("pattern no longer winning: " + t)
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: python3 compare_runs.py <runA|current> <runB|current>")
    diff = compare(load_signal(sys.argv[1]), load_signal(sys.argv[2]))
    print(render_text(diff))


if __name__ == "__main__":
    main()
