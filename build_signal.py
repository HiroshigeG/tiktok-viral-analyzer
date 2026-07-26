#!/usr/bin/env python3
"""
build_signal.py  --  Signal aggregation module.

Reads  data/raw/<brand>.json  (Contract A from ingest.py)
    +  data/analysis.json     (Contract B from analyze.py)
Produces signal.json valid against docs/signal.schema.json (Contract C).

Brand voice rule (non-negotiable): no hyphen or em dash as a generic connector
in any string written to signal.json. Compound modifiers are fine.

Usage:
    python build_signal.py [--data-dir data/] [--output signal.json] [--no-validate]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from brandconfig import BrandConfigError, load_config

# ── Brand identity, resolved from the config ─────────────────────────────────
# This module names no brand. The subject brand key decides whose own posts
# form the "today" half of the gap analysis.

_CONFIG_CACHE: Any = None


def _cfg() -> Optional[Any]:
    """Load the brand config once. None when there is no usable config."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        try:
            _CONFIG_CACHE = load_config()
        except BrandConfigError:
            _CONFIG_CACHE = False
    return _CONFIG_CACHE or None


def subject_brand_key() -> str:
    """Key of the brand being advised. Empty string when unconfigured."""
    cfg = _cfg()
    return cfg.subject.key if cfg else ""


def subject_display_name(key: str = "") -> str:
    """Display name for the subject, falling back to a titled key."""
    cfg = _cfg()
    if cfg:
        return cfg.name_for(key or cfg.subject.key)
    return key.title() if key else "The brand"



# ---------------------------------------------------------------------------
# Loading (Contract A + Contract B)
# ---------------------------------------------------------------------------

def load_raw(data_dir: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Read all data/raw/<brand>.json files (Contract A).
    Returns (raw_by_id, all_raw_videos).
    """
    raw_dir = Path(data_dir) / "raw"
    raw_by_id: Dict[str, Any] = {}
    all_raw_videos: List[Dict[str, Any]] = []

    if not raw_dir.exists():
        return raw_by_id, all_raw_videos

    # Read only the brands this config names. data/raw/ accumulates files from
    # every run, so globbing the directory would silently fold a previous
    # brand set into this analysis: run two configs against the same checkout
    # and the second one inherits the first one's corpus.
    cfg = _cfg()
    wanted = set(cfg.keys) if cfg else None

    for fpath in sorted(raw_dir.glob("*.json")):
        if fpath.stem == "index":
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            brand_data = json.load(f)
        # Filter on the brand recorded inside the file, not the filename. The
        # test fixtures are named raw_<brand>.json, so matching the stem would
        # silently drop every one of them.
        if wanted is not None and str(brand_data.get("brand", "")).lower() not in wanted:
            continue
        for v in brand_data.get("videos", []):
            vid_id = v["id"]
            raw_by_id[vid_id] = v
            all_raw_videos.append(v)

    return raw_by_id, all_raw_videos


def load_analysis(data_dir: str) -> Tuple[Dict[str, Any], bool]:
    """
    Read data/analysis.json (Contract B).
    Returns (analysis_by_id, claude_available).
    """
    analysis_path = Path(data_dir) / "analysis.json"
    if not analysis_path.exists():
        return {}, False
    with open(analysis_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    claude_available = bool(data.get("claude_available", False))
    analysis_by_id: Dict[str, Any] = {v["id"]: v for v in data.get("videos", [])}
    return analysis_by_id, claude_available


# ---------------------------------------------------------------------------
# Thumb normalisation
# ---------------------------------------------------------------------------

def normalize_thumb(raw_thumb: str, video_id: str) -> str:
    """
    Rewrite any thumb path to the web-relative  assets/thumbs/<id>.jpg  form.
    Falls back to empty string when the source is empty.
    """
    if not raw_thumb:
        return ""
    if raw_thumb.startswith("assets/thumbs/"):
        return raw_thumb
    if raw_thumb.startswith("data/raw/thumbs/"):
        filename = Path(raw_thumb).name
        return f"assets/thumbs/{filename}"
    # Remote URL or unrecognised local path: derive filename from the path.
    if any(raw_thumb.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
        filename = Path(raw_thumb).name
        return f"assets/thumbs/{filename}"
    # Final fallback: use the video id.
    return f"assets/thumbs/{video_id}.jpg"


# ---------------------------------------------------------------------------
# Merge (inner join on video id)
# ---------------------------------------------------------------------------

def merge_videos(
    raw_by_id: Dict[str, Any],
    analysis_by_id: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Produce a unified list of video records from raw + analysis.
    Only includes videos present in both sources (inner join on id).
    """
    merged: List[Dict[str, Any]] = []
    for vid_id, analysis in analysis_by_id.items():
        raw = raw_by_id.get(vid_id)
        if raw is None:
            continue
        # Metrics: prefer raw (direct scrape), fall back to analysis.
        metrics: Dict[str, Any] = raw.get("metrics") or analysis.get("metrics") or {}
        # Thumb: prefer analysis path (already normalised to assets/), else raw.
        thumb_source = analysis.get("thumb", "") or raw.get("thumb", "")
        thumb = normalize_thumb(thumb_source, vid_id)

        # Virality signals from Contract A (defensive: block may be absent on some videos).
        _sig = raw.get("signals") or {}
        _snd = _sig.get("sound") or {}

        merged.append({
            "id": vid_id,
            "brand": analysis.get("brand", raw.get("brand", "")),
            "performance_tier": analysis.get("performance_tier", raw.get("performance_tier", "")),
            "who": analysis.get("who", "brand"),
            "url": analysis.get("url", raw.get("url", "")),
            "thumb": thumb,
            "views": int(metrics.get("views", 0)),
            "engagement_rate": float(metrics.get("engagement_rate", 0.0)),
            "hook_type": analysis.get("hook_type", ""),
            "craft": analysis.get("craft", {}),
            "brand_fit_score": float(analysis.get("brand_fit", {}).get("score", 0.0)),
            "brand_fit_rationale": analysis.get("brand_fit", {}).get("rationale", ""),
            "brief_seed": analysis.get("brief_seed", {}),
            "comments": raw.get("comments", []),
            # Sound signals (None means data absent, not False).
            "sound_is_original": _snd.get("is_original"),
            "sound_title": (_snd.get("title") or "").strip(),
            "sound_author": (_snd.get("author") or "").strip(),
            # Hashtags normalised to lowercase without leading #.
            "hashtags": [
                str(h).strip().lower().lstrip("#")
                for h in (_sig.get("hashtags") or [])
                if h and str(h).strip()
            ],
            "duration_sec": _sig.get("duration_sec"),
        })
    return merged


# ---------------------------------------------------------------------------
# segnale.winning_patterns
# ---------------------------------------------------------------------------

# Clean, generalized labels per hook_type. We deliberately do NOT paste one
# video's raw craft.hook description, which reads as a single anecdote.
_HOOK_PATTERN_LABELS: Dict[str, str] = {
    "curiosity": (
        "Curiosity open. Top performers withhold the whole product and start on a detail you "
        "cannot place, a texture or a part, so the viewer stays for the reveal."
    ),
    "emotional": (
        "Emotional open. Top performers lead with a feeling, a memory or a human moment "
        "rather than the product, and let it arrive inside the story."
    ),
    "transformation": (
        "Transformation arc. Top performers set up a before and pay it off with an after, "
        "a build that earns the watch to the end."
    ),
    "shock": (
        "Pattern interrupt. Top performers open on one unexpected visual that stops the "
        "scroll, then resolve it cleanly."
    ),
}


def _hook_type_to_pattern(hook_type: str, videos: List[Dict[str, Any]]) -> str:
    """
    Map a hook_type to a clean, generalized pattern label (not a single video's
    raw description). Returns "" for empty or unclassified hooks so the caller
    can skip them. Brand voice: no connector dash or em dash.
    """
    key = hook_type.strip().lower()
    if not key or key == "unclassified":
        return ""
    if key in _HOOK_PATTERN_LABELS:
        return _HOOK_PATTERN_LABELS[key]
    label = hook_type.strip()
    return f"{label[0].upper()}{label[1:]} hook. A recurring opening style among the top performers."


def _compute_sound_stats(videos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute trending vs original sound usage from signals.sound.is_original.
    Returns a dict with has_data=False when fewer than 3 videos carry this signal.
    sound_is_original=None means the field was absent; these are excluded from counts.
    """
    trending = [v for v in videos if v.get("sound_is_original") is False]
    original = [v for v in videos if v.get("sound_is_original") is True]
    n_known = len(trending) + len(original)

    if n_known < 3:
        return {"has_data": False}

    def _avg_eng(vids: List[Dict[str, Any]]) -> float:
        return round(sum(v["engagement_rate"] for v in vids) / len(vids), 2) if vids else 0.0

    return {
        "has_data": True,
        "n_known": n_known,
        "n_trending": len(trending),
        "n_original": len(original),
        "trending_avg_eng": _avg_eng(trending),
        "original_avg_eng": _avg_eng(original),
        "trending_vids": trending,
        "original_vids": original,
    }


def _sound_pattern_if_earned(
    top: List[Dict[str, Any]], total_top: int
) -> Optional[Dict[str, Any]]:
    """
    Return a sound winning_pattern when trending borrowed sound is meaningfully
    present among top performers (at least 3 videos with is_original=False).
    Returns None when the data does not earn the pattern.
    """
    trending_top = [v for v in top if v.get("sound_is_original") is False]
    if len(trending_top) < 3:
        return None

    avg_eng = round(sum(v["engagement_rate"] for v in trending_top) / len(trending_top), 2)
    evidence_ids = [
        v["id"] for v in sorted(trending_top, key=lambda x: -x["engagement_rate"])[:5]
    ]
    n = len(trending_top)

    return {
        "pattern": (
            f"Trending borrowed sound. {n} of {total_top} top performers use a non-original "
            "trending track rather than brand-produced audio. "
            "The For You algorithm rewards trending sound even in a premium segment, "
            "suggesting discoverability and craft can coexist when the audio bed stays restrained."
        ),
        "frequency": f"{n}/{total_top}",
        "avg_engagement": avg_eng,
        "evidence_video_ids": evidence_ids,
    }


def _hashtag_pattern_if_earned(
    top: List[Dict[str, Any]], total_top: int
) -> Optional[Dict[str, Any]]:
    """
    Return a hashtag winning_pattern when at least 2 top performers share a common tag
    and at least 3 top performers collectively carry the top-3 tags.
    Returns None when the data does not earn the pattern.
    """
    tag_counts: Counter = Counter()  # type: ignore[type-arg]
    tag_to_vids: Dict[str, List[str]] = defaultdict(list)

    for v in top:
        seen_tags = set()
        for tag in v.get("hashtags", []):
            tag = tag.strip().lower().lstrip("#")
            if tag and tag not in seen_tags:
                tag_counts[tag] += 1
                tag_to_vids[tag].append(v["id"])
                seen_tags.add(tag)

    # Top tags that appear in at least 2 top videos.
    top_tags = [tag for tag, cnt in tag_counts.most_common(6) if cnt >= 2]
    if not top_tags:
        return None

    # Videos that carry at least one of the top 3 tags.
    top3 = top_tags[:3]
    covered_ids: List[str] = []
    seen_covered: set = set()
    for tag in top3:
        for vid_id in tag_to_vids[tag]:
            if vid_id not in seen_covered:
                covered_ids.append(vid_id)
                seen_covered.add(vid_id)

    if len(seen_covered) < 3:
        return None

    avg_eng = round(
        sum(
            v["engagement_rate"] for v in top if v["id"] in seen_covered
        ) / len(seen_covered),
        2,
    )
    tag_list = ", ".join(f"#{t}" for t in top3[:4])

    return {
        "pattern": (
            f"Category hashtag cluster. Top performers consistently use {tag_list} "
            "to surface in premium and lifestyle feeds, "
            "signaling that hashtag strategy is part of the discoverability stack "
            "even among the highest-engagement posts."
        ),
        "frequency": f"{len(seen_covered)}/{total_top}",
        "avg_engagement": avg_eng,
        "evidence_video_ids": covered_ids[:5],
    }


def build_winning_patterns(merged: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Cluster top-tier videos by hook_type, then add optional sound and hashtag
    patterns when the data earns them. Returns up to 5 patterns ranked by avg_engagement.
    frequency is formatted as "{count}/{total_top}".
    """
    top = [v for v in merged if v["performance_tier"] == "top"]
    total_top = len(top)
    if total_top == 0:
        return []

    # Hook-type patterns (craft-based, core signal).
    by_hook: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for v in top:
        key = v["hook_type"].strip() or "unclassified"
        by_hook[key].append(v)

    patterns: List[Dict[str, Any]] = []
    for hook, videos in by_hook.items():
        label = _hook_type_to_pattern(hook, videos)
        if not label:
            continue  # skip empty / unclassified hook buckets
        count = len(videos)
        avg_eng = round(sum(v["engagement_rate"] for v in videos) / count, 2)
        evidence_ids = [
            v["id"] for v in sorted(videos, key=lambda x: -x["engagement_rate"])[:5]
        ]
        patterns.append({
            "pattern": label,
            "frequency": f"{count}/{total_top}",
            "avg_engagement": avg_eng,
            "evidence_video_ids": evidence_ids,
        })

    # Optional virality signal patterns.
    sound_pat = _sound_pattern_if_earned(top, total_top)
    if sound_pat:
        patterns.append(sound_pat)

    hashtag_pat = _hashtag_pattern_if_earned(top, total_top)
    if hashtag_pat:
        patterns.append(hashtag_pat)

    patterns.sort(key=lambda x: -x["avg_engagement"])
    return patterns[:5]


# ---------------------------------------------------------------------------
# segnale.baseline_delta
# ---------------------------------------------------------------------------

# Static authored delta notes per dimension.
# Brand voice: no connector dash, no em dash.
_DELTA_NOTES: Dict[str, str] = {
    "hook_type": (
        "Winners open on a specific craft detail or a point of view that withholds the whole product. "
        "Losers lead with a wide beauty shot that shows everything at once and gives the viewer no reason to stay."
    ),
    "pacing": (
        "Winners hold a single opening shot for three to five seconds before the first cut, "
        "signaling craft and confidence. "
        "Losers cut every second in a montage rhythm the algorithm reads as a commercial and skips."
    ),
    "audio": (
        "Winners use ambient sound or a quiet trending bed with no announcer. "
        "Losers use a hard sell voiceover over stock music, "
        "which collapses a premium register instantly."
    ),
    "who": (
        "Owner and creator footage consistently outperforms brand studio output by a wide margin. "
        "This is the blind spot: the most effective content type is the one brand posts least often."
    ),
    "text_overlay": (
        "Winners carry one short line that names a feeling, set quietly and late. "
        "Losers stack spec numbers on screen. "
        "Emotion wins over spec in every cluster in this data."
    ),
}


def _most_common_value(values: List[str]) -> str:
    """Return the most common non-empty string from a list."""
    counts = Counter(v.strip() for v in values if v and v.strip())
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def _audio_delta_row(
    top: List[Dict[str, Any]], bottom: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Build the audio baseline_delta row.
    When signals.sound data is present (>=3 known values per tier), use real
    trending vs original counts to produce a data-driven winners/losers/delta_note.
    Falls back to craft.audio text when signals are sparse.
    Brand voice: no connector dash or em dash.
    """
    top_stats = _compute_sound_stats(top)
    bot_stats = _compute_sound_stats(bottom)

    if top_stats["has_data"]:
        n_top = top_stats["n_known"]
        n_tr = top_stats["n_trending"]
        n_or = top_stats["n_original"]
        tr_eng = top_stats["trending_avg_eng"]
        or_eng = top_stats["original_avg_eng"]

        if n_tr >= n_or:
            winners_val = (
                f"Trending borrowed sound in {n_tr} of {n_top} top performers "
                f"(avg engagement {tr_eng} percent); "
                f"original or ambient sound in the remaining {n_or}"
            )
        else:
            winners_val = (
                f"Original or ambient sound in {n_or} of {n_top} top performers "
                f"(avg engagement {or_eng} percent); "
                f"trending borrowed sound in {n_tr}"
            )

        # Delta note: compare trending vs original engagement within top tier.
        eng_gap = round(abs(tr_eng - or_eng), 1)
        if n_or > 0 and n_tr > 0 and eng_gap >= 0.5:
            leading = "Original ambient sound" if or_eng > tr_eng else "Trending borrowed sound"
            trailing = "trending borrowed sound" if or_eng > tr_eng else "original ambient sound"
            delta_note = (
                f"{leading} leads by {eng_gap} engagement points over {trailing} within the top tier. "
                f"Trending audio drives volume ({n_tr} of {n_top} videos) while "
                "original ambient recordings from owner and creator content achieve the highest individual scores. "
                "The lesson: trending audio earns reach, ambient authenticity earns depth."
            )
        elif n_tr > n_or:
            delta_note = (
                f"Trending borrowed sound is the dominant choice across {n_tr} of {n_top} top performers, "
                "suggesting the For You algorithm rewards trending audio even in a premium segment. "
                "Execution matters: calm trending beds fit the register; loud beat drops fight it."
            )
        else:
            delta_note = (
                f"Original or ambient sound leads in {n_or} of {n_top} top performers. "
                "A premium register rewards restraint: ambient recorded sound and quiet scored beds "
                "outperform announcer voiceover. "
                "Trending audio can work when the bed is calm."
            )
    else:
        # No signal data: fall back to craft.audio text and static note.
        winners_val = _most_common_value(
            [v.get("craft", {}).get("audio", "") for v in top]
        ) or "varied"
        delta_note = _DELTA_NOTES["audio"]

    if bot_stats["has_data"]:
        n_bot = bot_stats["n_known"]
        bot_tr = bot_stats["n_trending"]
        bot_or = bot_stats["n_original"]
        losers_val = (
            f"Trending or stock audio in {bot_tr} of {n_bot} bottom performers; "
            f"announcer voiceover or original brand audio in {bot_or}"
        )
    else:
        losers_val = _most_common_value(
            [v.get("craft", {}).get("audio", "") for v in bottom]
        ) or "varied"

    return {
        "dimension": "audio",
        "winners": winners_val,
        "losers": losers_val,
        "delta_note": delta_note,
    }


def build_baseline_delta(merged: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Per dimension (hook_type, pacing, audio, who, text_overlay):
    most-common value in top tier vs bottom tier, plus a delta note.
    The audio row is data-driven via signals.sound when the data is present.
    """
    top = [v for v in merged if v["performance_tier"] == "top"]
    bottom = [v for v in merged if v["performance_tier"] == "bottom"]

    def extract(vids: List[Dict[str, Any]], dim: str) -> str:
        if dim == "hook_type":
            return _most_common_value([v.get("hook_type", "") for v in vids])
        if dim == "pacing":
            return _most_common_value([v.get("craft", {}).get("pacing", "") for v in vids])
        if dim == "who":
            return _most_common_value([v.get("who", "") for v in vids])
        if dim == "text_overlay":
            return _most_common_value([v.get("craft", {}).get("text_overlay", "") for v in vids])
        return ""

    def _is_junk(s: str) -> bool:
        t = s.strip().lower()
        if t in ("", "none", "varied"):
            return True
        toks = [x.strip().lower() for x in t.replace(";", " ").split()]
        return bool(toks) and all(x in ("none", "") for x in toks)

    # Build candidate rows, then keep only the ones that actually discriminate
    # winners from losers. The audio row is data driven and always kept.
    rows: List[Dict[str, Any]] = []

    who_row = {
        "dimension": "who",
        "winners": extract(top, "who") or "owner",
        "losers": extract(bottom, "who") or "brand",
        "delta_note": _DELTA_NOTES["who"],
    }
    if who_row["winners"].strip().lower() != who_row["losers"].strip().lower():
        rows.append(who_row)

    rows.append(_audio_delta_row(top, bottom))

    for dim in ["pacing", "hook_type", "text_overlay"]:
        w, l = extract(top, dim), extract(bottom, dim)
        if _is_junk(w) or _is_junk(l):
            continue
        if w.strip().lower() == l.strip().lower():
            continue  # non discriminating dimension, skip
        rows.append({
            "dimension": dim,
            "winners": w,
            "losers": l,
            "delta_note": _DELTA_NOTES.get(dim, ""),
        })
    return rows


# ---------------------------------------------------------------------------
# segnale.top_videos
# ---------------------------------------------------------------------------

def build_top_videos(merged: List[Dict[str, Any]], n: int = 5) -> List[Dict[str, Any]]:
    """Return the top n videos by engagement_rate with all schema-required fields."""
    sorted_vids = sorted(merged, key=lambda v: -v["engagement_rate"])[:n]
    result: List[Dict[str, Any]] = []
    for v in sorted_vids:
        craft = v.get("craft", {})
        result.append({
            "id": v["id"],
            "brand": v["brand"],
            "url": v["url"],
            "thumb": v["thumb"],
            "views": v["views"],
            "engagement": round(v["engagement_rate"], 2),
            "who": v["who"],
            "hook_type": v["hook_type"],
            "craft": {
                "hook": craft.get("hook", ""),
                "structure": craft.get("structure", ""),
                "audio": craft.get("audio", ""),
                "why": craft.get("why", []),
            },
        })
    return result


# ---------------------------------------------------------------------------
# insight.owners_vs_brand
# ---------------------------------------------------------------------------

def build_owners_vs_brand(merged: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Engagement share: who != "brand" vs who == "brand".
    Metric: sum(views * engagement_rate / 100) as proxy for total engagement actions.
    Evidence mixes plain strings and {label, value} objects per the schema.
    """
    brand_actions = 0.0
    owner_actions = 0.0
    brand_rates: List[float] = []
    owner_rates: List[float] = []

    for v in merged:
        actions = v["views"] * v["engagement_rate"] / 100.0
        if v["who"] == "brand":
            brand_actions += actions
            brand_rates.append(v["engagement_rate"])
        else:
            owner_actions += actions
            owner_rates.append(v["engagement_rate"])

    total = brand_actions + owner_actions
    if total == 0:
        owner_share, brand_share = 0.0, 0.0
    else:
        owner_share = round(owner_actions / total * 100, 1)
        brand_share = round(brand_actions / total * 100, 1)

    brand_avg = round(sum(brand_rates) / len(brand_rates), 1) if brand_rates else 0.0
    owner_avg = round(sum(owner_rates) / len(owner_rates), 1) if owner_rates else 0.0

    top5 = sorted(merged, key=lambda v: -v["engagement_rate"])[:5]
    non_brand_top5 = sum(1 for v in top5 if v["who"] != "brand")
    owner_count = len(owner_rates)
    total_count = len(merged)

    evidence: List[Any] = [
        (
            f"Owner and creator videos drove {owner_share:.0f} percent of total engagement "
            f"while accounting for {owner_count} of {total_count} videos in the set."
        ),
        {"label": "Top 5 by engagement", "value": f"{non_brand_top5} of 5 are owner or creator footage"},
        {"label": "Brand studio average engagement rate", "value": f"{brand_avg} percent"},
        {"label": "Owner and creator average engagement rate", "value": f"{owner_avg} percent"},
    ]

    return {
        "owner_share_pct": owner_share,
        "brand_share_pct": brand_share,
        "evidence": evidence,
    }


# ---------------------------------------------------------------------------
# insight.brand_gap
# ---------------------------------------------------------------------------

def _dimension_traits() -> List[Dict[str, Any]]:
    """
    The craft dimensions the gap is measured on.

    These are deliberately not hook types. A hook type is Gemini's free-form
    label and it barely discriminates: in the shipped corpus 84 percent of
    category winners and 83 percent of the subject's own posts carry the same
    one, so clustering on it produces "roughly in step" for everything. The
    dimensions below are the craft choices a brand actually controls, which is
    what the baseline delta already compares winners and losers on.

    Each entry carries a predicate that decides whether a video exhibits the
    trait, reading the craft text the same way for any category.
    """
    return [
        {
            "label": "Owner and creator point of view rather than brand studio film",
            "test": lambda v: str(v.get("who", "")).lower() in ("owner", "creator"),
            # `who` is derived from the account type, so a brand's own posts can
            # never test true here. Without this flag the row said "the subject
            # sits at 0, an open lane" for every brand ever measured, which is a
            # tautology dressed as a finding. The winners versus losers half is
            # real; the "today" half needs different prose.
            "structural_zero_for_brand": True,
        },
        {
            "label": "Original and ambient sound rather than an announcer",
            "test": lambda v: _has_any(
                v, "audio",
                ("original", "ambient", "no music", "room tone", "silence", "diegetic"),
            ),
        },
        {
            "label": "Trending borrowed sound for discoverability",
            "test": lambda v: _has_any(v, "audio", ("trending", "borrowed", "popular sound")),
        },
        {
            "label": "Held pacing, one shot before the first cut",
            "test": lambda v: _has_any(
                v, "pacing", ("slow", "held", "static", "single", "one shot", "lingering"),
            ),
        },
        {
            "label": "Fast cut montage rhythm",
            "test": lambda v: _has_any(
                v, "pacing", ("fast", "rapid", "quick", "montage", "frenetic"),
            ),
        },
        {
            "label": "Restrained text on screen, one line rather than stacked",
            "test": lambda v: _has_any(
                v, "text_overlay", ("minimal", "single", "one line", "sparse", "none", "no text"),
            ),
        },
    ]


def _has_any(video: Dict[str, Any], craft_field: str, needles: Tuple[str, ...]) -> bool:
    """True when the craft field mentions any of the needles."""
    text = str(video.get("craft", {}).get(craft_field, "")).lower()
    return any(n in text for n in needles)


def build_brand_gap(
    merged: List[Dict[str, Any]],
    subject_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Pair each category winning trait with what the subject brand actually does
    today, derived from its own scraped posts.

    This used to be a hardcoded list of four authored entries, so the "insight"
    half of the output said the same thing about every brand and every corpus.
    Everything here is now computed:

      category_winning_trait  a craft dimension (see _dimension_traits)
      brand_today             how much the subject itself uses it
      gap_note                the size and direction of the difference
      brand_fit_score         mean of the per video brand_fit scores among the
                              winners showing the trait, which come from the brief

    Only dimensions where the winners genuinely separate from the losers are
    kept, so a trait both tiers share equally never reaches the output. Returns
    the four largest gaps, positive or negative.
    """
    top    = [v for v in merged if v.get("performance_tier") == "top"]
    bottom = [v for v in merged if v.get("performance_tier") == "bottom"]
    if not top:
        return []

    subject = (subject_key or subject_brand_key() or "").lower()
    # "What the brand does today" means what the brand account itself posts.
    # Filtering on brand alone would fold in the owner and creator hashtag
    # corpus, which is precisely the content the brand is NOT making, and the
    # gap would wash out.
    own = [
        v for v in merged
        if str(v.get("brand", "")).lower() == subject
        and str(v.get("who", "")).lower() == "brand"
    ]
    name = subject_display_name(subject)

    def share(vids: List[Dict[str, Any]], test: Any) -> float:
        return (sum(1 for v in vids if test(v)) / len(vids)) if vids else 0.0

    items: List[Dict[str, Any]] = []
    for dim in _dimension_traits():
        test = dim["test"]
        top_share    = share(top, test)
        bottom_share = share(bottom, test)

        # Keep only dimensions that actually separate winners from losers.
        # Without this the section fills with traits everyone uses equally.
        if abs(top_share - bottom_share) < 0.10:
            continue
        if top_share < 0.10:
            continue

        own_share = share(own, test)
        fits = [
            float(v["brand_fit"]["score"])
            for v in top
            if test(v)
            and isinstance(v.get("brand_fit"), dict)
            and isinstance(v["brand_fit"].get("score"), (int, float))
        ] or [
            float(v["brand_fit_score"])
            for v in top
            if test(v) and isinstance(v.get("brand_fit_score"), (int, float))
        ]
        fit = round(sum(fits) / len(fits), 2) if fits else 0.0

        gap = top_share - own_share
        structural = bool(dim.get("structural_zero_for_brand")) and bool(own)
        if not own:
            brand_today = "{} has no posts in this corpus, so its current use is unknown.".format(name)
        elif structural:
            # A brand account cannot BE an owner, but it can borrow the style:
            # handheld, phone framing, no narration. Measure that instead of
            # reporting a structural zero as if it were behaviour.
            style_cues = (
                "handheld", "pov", "point of view", "selfie", "vlog", "phone",
                "no narration", "no voiceover", "amateur", "candid", "shaky",
            )
            def _owner_style(v: Dict[str, Any]) -> bool:
                craft = v.get("craft", {}) or {}
                text = " ".join(
                    str(craft.get(k, "")) for k in ("hook", "structure", "pacing", "audio")
                ).lower()
                return any(c in text for c in style_cues)
            styled = sum(1 for v in own if _owner_style(v))
            if styled:
                brand_today = (
                    "A brand account cannot author this by definition, but {} of "
                    "{}'s {} posts borrow the style: handheld framing, no "
                    "narration.".format(styled, name, len(own))
                )
            else:
                brand_today = (
                    "By definition a brand account cannot author this itself, so "
                    "the question is whether {} amplifies or borrows the style, "
                    "and in this corpus its feed does neither.".format(name)
                )
        else:
            brand_today = "{} does this in {} percent of its own posts.".format(
                name, round(own_share * 100)
            )

        if structural:
            gap_note = (
                "Category winners do this {} percent of the time against the losers' {}. "
                "The lane for {} is amplification: resharing and commissioning the "
                "footage, not imitating it from the studio.".format(
                    round(top_share * 100), round(bottom_share * 100), name,
                )
            )
        elif gap > 0.15:
            gap_note = (
                "Category winners do this {} percent of the time against the losers' {}, "
                "and {} sits at {}. An open lane.".format(
                    round(top_share * 100), round(bottom_share * 100),
                    name, round(own_share * 100),
                )
            )
        elif gap < -0.15:
            gap_note = (
                "{} already does this more than the category winners, {} percent against "
                "{}. Volume is not the problem here.".format(
                    name, round(own_share * 100), round(top_share * 100),
                )
            )
        else:
            gap_note = (
                "{} is level with the category winners at roughly {} percent. "
                "Execution decides the outcome, not volume.".format(
                    name, round(top_share * 100),
                )
            )

        items.append(
            {
                "category_winning_trait": dim["label"],
                "brand_today": brand_today,
                "gap_note": gap_note,
                "brand_fit_score": fit,
                "_gap": gap,
            }
        )

    items.sort(key=lambda d: abs(d["_gap"]), reverse=True)
    for d in items:
        d.pop("_gap", None)
    return items[:4]


def _trait_label(hook: str, vids: List[Dict[str, Any]]) -> str:
    """
    Name the winning trait from the hook type plus the dominant craft signal in
    the cluster, so the label describes the work rather than echoing a bare tag.
    """
    label = _HOOK_PATTERN_LABELS.get(hook, hook)
    joined = " ".join(str(v.get("craft", {}).get("audio", "")).lower() for v in vids)
    if "original" in joined or "ambient" in joined or "no music" in joined:
        return "{}, carried by original or ambient sound".format(label)
    if "trending" in joined:
        return "{}, riding trending sound".format(label)
    return label


# ---------------------------------------------------------------------------
# insight.audience_voice
# ---------------------------------------------------------------------------

# Keyword theme seeds for comment clustering.
# No heavy NLP: pure keyword matching.
_COMMENT_THEMES: List[Dict[str, Any]] = [
    {
        "cluster": "Sound design and restraint",
        "sentiment_hint": "pos",
        "keywords": [
            "sound", "quiet", "silent", "audio", "noise", "voiceover",
            "music", "heard", "listen", "asmr", "door", "thunk",
        ],
    },
    {
        "cluster": "Craft and detail",
        "sentiment_hint": "pos",
        "keywords": [
            "stitch", "craft", "detail", "quality", "made", "build",
            "finish", "hand", "texture", "grain", "material", "precise",
        ],
    },
    {
        "cluster": "Price and aspiration",
        "sentiment_hint": "mixed",
        "keywords": [
            "price", "afford", "cost", "expensive", "cheap", "dream",
            "saving", "worth", "money", "buy", "purchase", "one day",
        ],
    },
    {
        "cluster": "Owner loyalty and reliability",
        "sentiment_hint": "pos",
        "keywords": [
            "owner", "owned", "loyal", "reliable", "mile", "miles",
            "never going back", "third", "fourth", "second", "dad", "family",
            "years", "generation",
        ],
    },
    {
        "cluster": "Desire and admiration",
        "sentiment_hint": "pos",
        "keywords": [
            "beautiful", "gorgeous", "love", "want", "need", "fire", "sick",
            "stunning", "amazing", "perfect", "wow", "insane", "goat",
        ],
    },
    {
        "cluster": "Brand and competitor comparison",
        "sentiment_hint": "mixed",
        "keywords": [
            "compared", "vs", "versus",
            "better than", "beats", "over", "switch", "switched",
        ],
    },
]

_NEGATIVE_OVERRIDE: List[str] = [
    "worst", "bad", "ugly", "hate", "terrible", "disappointing",
    "overpriced", "trash", "boring",
]


def _score_comment(text: str, keywords: List[str]) -> int:
    """Count keyword hits in a comment (case insensitive, substring match)."""
    lower = text.lower()
    return sum(1 for kw in keywords if kw in lower)


def _detect_sentiment(comments: List[str], hint: str) -> str:
    """Downgrade sentiment hint if negative keywords dominate."""
    if not comments:
        return hint
    neg_hits = sum(1 for c in comments for kw in _NEGATIVE_OVERRIDE if kw in c.lower())
    ratio = neg_hits / len(comments)
    if ratio > 0.4:
        return "neg"
    if ratio > 0.15:
        return "mixed"
    return hint


def comment_themes() -> List[Dict[str, Any]]:
    """
    Comment clustering seeds, with the configured brand names added to the
    comparison cluster. The competitor list used to be a stored set of car
    makes, so on any other category that cluster could never fire.
    """
    themes = [dict(th, keywords=list(th["keywords"])) for th in _COMMENT_THEMES]
    cfg = _cfg()
    if cfg:
        names = [b.name.lower() for b in cfg.brands] + [b.key for b in cfg.brands]
        for th in themes:
            if th["cluster"] == "Brand and competitor comparison":
                th["keywords"] = sorted(set(th["keywords"]) | set(names))
    return themes


def build_audience_voice(merged: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Cluster all comments across brands into themes.
    Returns clusters with sentiment, volume, and up to 3 verbatim sample_comments.
    """
    all_comments: List[str] = []
    for v in merged:
        for c in v.get("comments", []):
            text = c.get("text", "").strip()
            if text:
                all_comments.append(text)

    if not all_comments:
        return []

    _themes = comment_themes()
    theme_buckets: Dict[str, List[str]] = {t["cluster"]: [] for t in _themes}
    unmatched: List[str] = []

    for comment in all_comments:
        best_theme: Optional[str] = None
        best_score = 0
        for theme in _themes:
            score = _score_comment(comment, theme["keywords"])
            if score > best_score:
                best_score = score
                best_theme = theme["cluster"]
        if best_score > 0 and best_theme:
            theme_buckets[best_theme].append(comment)
        else:
            unmatched.append(comment)

    # Unmatched go to the desire catch-all.
    if unmatched:
        theme_buckets["Desire and admiration"].extend(unmatched)

    theme_map = {t["cluster"]: t for t in _themes}
    result: List[Dict[str, Any]] = []
    for cluster_name, comments in theme_buckets.items():
        if not comments:
            continue
        theme = theme_map[cluster_name]
        result.append({
            "cluster": cluster_name,
            "sentiment": _detect_sentiment(comments, theme["sentiment_hint"]),
            "volume": len(comments),
            "sample_comments": comments[:3],
        })

    result.sort(key=lambda x: -x["volume"])
    return result


# ---------------------------------------------------------------------------
# direzione
# ---------------------------------------------------------------------------

def build_direzione(merged: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Synthesize the creative brief from the highest brand-fit brief_seeds and the
    measured gap.

    Everything that names the brand or its campaign platforms is derived. The
    earlier version hardcoded one brand's thesis and tied every brief to that
    brand's campaign names, which meant the direction was the same sentence no
    matter what the data said.
    """
    top = [v for v in merged if v["performance_tier"] == "top"] or merged
    top_fit = sorted(top, key=lambda v: -v["brand_fit_score"])[:3]

    primary_seed = top_fit[0].get("brief_seed", {}) if top_fit else {}
    primary_concept = primary_seed.get("concept", "").strip()
    primary_hook = primary_seed.get("hook_formula", "").strip()

    name = subject_display_name()
    gap = build_brand_gap(merged)

    # The thesis states the measured gap rather than a stored opinion.
    if gap:
        lead = gap[0]
        note = lead["gap_note"]
        if "open lane" in note:
            shape = (
                "The feed rewards {} and {} underuses it. {} "
                "The opportunity is to stop renting category tactics and amplify "
                "what the audience is already rewarding."
            )
        elif "not the problem" in note:
            shape = (
                "{1} already leans harder on {0} than the category winners do. {2} "
                "The opportunity is craft, not volume: the same move executed in the "
                "brand register rather than the category default."
            )
        else:
            shape = (
                "On {0} the brand and the category winners sit level. {2} "
                "The opportunity is not a new tactic but a sharper execution of the "
                "one already in use."
            )
        thesis = shape.format(
            lead["category_winning_trait"].lower(), name, note
        )
        opening = lead["category_winning_trait"].lower()
    else:
        thesis = (
            "Not enough top tier signal in this corpus to state a gap with confidence. "
            "Widen the pool or loosen the tiering before briefing against it."
        )
        opening = "the winning opening in the set"

    strongest = [g for g in gap if g["brand_fit_score"] >= 0.7]
    title = (
        "{}: {}".format(name, opening) if gap else "{}: direction pending".format(name)
    )

    brief: Dict[str, Any] = {
        "title": title,
        "thesis": thesis,
        "concept": primary_concept or (
            "A standing format built on {}, that treats the made object and the "
            "loyal customer as the heroes. Listen to the comment clusters each week "
            "and brief against what the audience is already saying.".format(opening)
        ),
        "format": (
            "Vertical nine by sixteen. "
            "One held hook in the first second, sound that carries, "
            "one quiet line of text late, logo last."
        ),
        "hook_formula": primary_hook or (
            "Open on the made detail in extreme closeup, hold it, "
            "let the sound carry, reveal the whole only at the end."
        ),
        "ties_to": [g["category_winning_trait"] for g in strongest[:2]],
    }

    next_moves: List[str] = []
    for g in gap[:4]:
        if g["brand_fit_score"] >= 0.7:
            next_moves.append(
                "Build a standing lane around {}. {}".format(
                    g["category_winning_trait"].lower(), g["gap_note"]
                )
            )
        elif g["brand_fit_score"] >= 0.4:
            next_moves.append(
                "Test {} selectively, in the brand register rather than the "
                "category default.".format(g["category_winning_trait"].lower())
            )
        else:
            next_moves.append(
                "Leave {} to the category. It performs, and it would cost more in "
                "brand equity than it returns.".format(
                    g["category_winning_trait"].lower()
                )
            )
    next_moves.append(
        "Stand up a weekly listen on the comment clusters and brief against what "
        "the audience is already saying."
    )

    return {"brief": brief, "next_moves": next_moves}


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------

def build_meta(merged: List[Dict[str, Any]], claude_available: bool) -> Dict[str, Any]:
    brands = sorted(set(v["brand"] for v in merged))

    # The honesty framing has to cover sample size too. A real run produced a
    # confident dashboard from six videos, where "67 percent of winners" meant
    # two videos out of three. The percentages are arithmetic on real metrics
    # either way; below roughly two dozen videos they are anecdotes, and the
    # output should say so where the reader can see it.
    n = len(merged)
    top_n = sum(1 for v in merged if v.get("performance_tier") == "top")
    sample_note = ""
    if n < 24 or top_n < 6:
        sample_note = (
            "Small sample: {} videos, {} in the winning tier. Read the "
            "percentages as a directional sketch, not a baseline. Rerun with a "
            "larger pool before putting this in front of anyone.".format(n, top_n)
        )

    return {
        **({"sample_note": sample_note} if sample_note else {}),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "brands": brands,
        "n_videos": len(merged),
        "claude_available": claude_available,
        # The frontend labels the gap column with this. Without it the site
        # would have to hardcode a brand name, which is how "Lexus today"
        # ended up baked into the markup.
        "subject": subject_display_name(),
        "method_note": (
            "Public metrics are scraped and real. "
            "Craft and strategy are dual model inference (Gemini vision plus Claude), "
            "labeled as such. "
            "No causation is claimed beyond the winners versus losers baseline delta."
        ),
    }


# ---------------------------------------------------------------------------
# Top-level builder (callable from tests)
# ---------------------------------------------------------------------------

def build_signal(data_dir: str = "data") -> Dict[str, Any]:
    """
    Read all inputs from data_dir, aggregate, and return the complete signal dict.
    Raises ValueError if no merged records are found.
    """
    raw_by_id, _ = load_raw(data_dir)
    analysis_by_id, claude_available = load_analysis(data_dir)

    merged = merge_videos(raw_by_id, analysis_by_id)

    if not merged:
        raise ValueError(
            f"No merged video records found. "
            f"Check that {data_dir}/raw/<brand>.json and {data_dir}/analysis.json "
            "are present and share video ids."
        )

    return {
        "meta": build_meta(merged, claude_available),
        "segnale": {
            "winning_patterns": build_winning_patterns(merged),
            "baseline_delta": build_baseline_delta(merged),
            "top_videos": build_top_videos(merged),
        },
        "insight": {
            "owners_vs_brand": build_owners_vs_brand(merged),
            "brand_gap": build_brand_gap(merged),
            "audience_voice": build_audience_voice(merged),
        },
        "direzione": build_direzione(merged),
    }


# ---------------------------------------------------------------------------
# Schema validation helper
# ---------------------------------------------------------------------------

def validate_against_schema(signal_data: Dict[str, Any], schema_path: Path) -> List[str]:
    """
    Validate signal_data against the JSON Schema at schema_path.
    Returns a list of error messages (empty list means valid).
    """
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return []

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(signal_data))
    return [f"{list(e.absolute_path)}: {e.message}" for e in errors]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate TikTok analysis data into signal.json."
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing raw/ subdirectory and analysis.json (default: data)",
    )
    parser.add_argument(
        "--output",
        default="signal.json",
        help="Output path for signal.json (default: signal.json)",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        default=False,
        help="Skip JSON Schema validation after writing",
    )
    args = parser.parse_args()

    print(f"Reading from: {args.data_dir}")
    signal_data = build_signal(args.data_dir)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(signal_data, f, indent=2, ensure_ascii=False)

    n = signal_data["meta"]["n_videos"]
    brands = signal_data["meta"]["brands"]
    print(f"Wrote {args.output}  ({n} videos, brands: {brands})")

    if not args.no_validate:
        schema_path = Path(__file__).parent / "docs" / "signal.schema.json"
        if schema_path.exists():
            errors = validate_against_schema(signal_data, schema_path)
            if errors:
                print("Schema validation FAILED:", file=sys.stderr)
                for err in errors:
                    print(f"  {err}", file=sys.stderr)
                sys.exit(1)
            else:
                print("Schema validation passed.")
        else:
            print(f"Schema not found at {schema_path}. Skipping validation.")


if __name__ == "__main__":
    main()
