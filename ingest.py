#!/usr/bin/env python3
"""
ingest.py — SIGNAL ingestion module.

Scrapes the official TikTok accounts named in the brand config, pools videos
per brand, splits into top / bottom performers by view count (top third =
"top", bottom third = "bottom", middle dropped to sharpen the baseline),
scrapes comments per kept video, downloads cover images AND mp4 files, and
writes per-brand raw records per Contract A (docs/contracts.md).

Ranking metric: views (playCount).
  Rationale: views measure raw algorithmic distribution and audience reach.
  They reflect what the platform is amplifying, and are harder to game than
  engagement rate alone, making them the cleanest baseline signal.

Balance rule: after splitting each brand independently, cap each tier at
min(tier_count_across_brands) so every brand contributes equal video counts.

Reuse note: imports TikTokAnalyzer from the vendored analyzer.py and wraps
its Apify run / poll / dataset pattern in our own helpers (required by
Contract A; allows shouldDownloadCovers + shouldDownloadVideos = True and
the clockworks~tiktok-comments-scraper actor).

Usage:
    python ingest.py                         # all brands, default pool size
    python ingest.py --brand <key>           # single brand from the config
    python ingest.py --pool-size 40          # override pool size
    python ingest.py --config config/other.json --pool-size 20
"""

import os
import json
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

# Vendored analyzer — we import TikTokAnalyzer to reuse its Apify key
# setup and mirror its run/poll/dataset pattern (Contract A requirement).
from analyzer import TikTokAnalyzer  # noqa: F401 — imported per contract

# Brand identity lives entirely in the config, never in this module.
from brandconfig import BrandConfigError, add_config_argument, load_config

# ── Environment ──────────────────────────────────────────────────────────────

load_dotenv()
APIFY_API_KEY: Optional[str] = os.getenv("APIFY_API_KEY")

# ── Brand config ─────────────────────────────────────────────────────────────
# Which brands to scrape, their handles and their hashtags all come from the
# brand config (config/brand.json by default). Nothing about any particular
# brand is hardcoded here. See brandconfig.py and config/TEMPLATE.json.
#
# Handle matching is case-insensitive (normalised inside is_brand_account).
# Choose hashtags carefully: bare brand words collide, which is why the
# shipped example uses "genesismotors" rather than "genesis", the latter returning
# a rock band and religious content.

DEFAULT_POOL_SIZE:         int = 40   # profile videos scraped per brand
DEFAULT_HASHTAG_POOL_SIZE: int = 25   # hashtag (owner/creator) videos per brand
DEFAULT_COMMENT_CAP:       int = 30   # max comments fetched per kept video
COMMENT_BATCH_CHUNK:       int = 20   # postURLs per comments run (actor drops URLs above a ceiling)
MIN_VIEWS_FLOOR:           int = 1000 # a video needs this many views to qualify as a top performer

# ── Apify actor IDs ──────────────────────────────────────────────────────────

ACTOR_VIDEOS:   str = "clockworks~tiktok-scraper"
ACTOR_COMMENTS: str = "clockworks~tiktok-comments-scraper"

# ── Output paths (relative to cwd = repo root when running python ingest.py) ─

DATA_DIR:   Path = Path("data")
RAW_DIR:    Path = DATA_DIR / "raw"
THUMBS_DIR: Path = RAW_DIR / "thumbs"
VIDEOS_DIR: Path = DATA_DIR / "videos"


# ════════════════════════════════════════════════════════════════════════════
# Pure transform helpers — no I/O; fully unit-testable
# ════════════════════════════════════════════════════════════════════════════

def calc_engagement_rate(likes: int, comments: int, shares: int, views: int) -> float:
    """
    (likes + comments + shares) / views * 100, rounded to 2 decimal places.
    Returns 0.0 when views <= 0 to avoid ZeroDivisionError.
    Identical formula to analyzer.py.
    """
    if views <= 0:
        return 0.0
    return round((likes + comments + shares) / views * 100, 2)


def is_brand_account(author_handle: str, official_handle: str) -> bool:
    """
    True when author_handle matches the brand's official handle.
    Strips leading '@' and lowercases before comparing so '@BrandUSA'
    correctly matches '@brandusa'.
    """
    def _norm(h: str) -> str:
        return h.lstrip("@").lower().strip()

    return _norm(author_handle) == _norm(official_handle)


def _raw_metric(raw: Dict, key: str) -> int:
    """
    Read a metric from a raw Apify item, top-level first (real clockworks
    shape), falling back to the nested videoMeta shape for older fixtures.
    """
    if key in raw:
        try:
            return int(raw[key] or 0)
        except (TypeError, ValueError):
            return 0
    try:
        return int(raw.get("videoMeta", {}).get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _raw_views(raw: Dict) -> int:
    return _raw_metric(raw, "playCount")


def _raw_engagement(raw: Dict) -> float:
    """Engagement rate of a raw item: (likes + comments + shares) / views * 100."""
    views = _raw_views(raw)
    if views <= 0:
        return 0.0
    likes  = _raw_metric(raw, "diggCount")
    coms   = _raw_metric(raw, "commentCount")
    shares = _raw_metric(raw, "shareCount")
    return (likes + coms + shares) / views * 100


def split_top_bottom(
    videos: List[Dict],
    min_views_floor: int = MIN_VIEWS_FLOOR,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Tier the combined corpus by ENGAGEMENT RATE (not raw views).

    Rationale: the corpus mixes brand-profile content (high reach, lower
    engagement rate) with owner / creator hashtag content (lower reach, higher
    engagement rate). Ranking by views would just sort on follower count;
    engagement rate is the resonance signal and yields the real winners vs
    losers craft baseline (owner craft tends to win, brand commercial style
    tends to lose).

    - top: the highest-engagement third, restricted to videos with
      views >= min_views_floor (cuts micro-video noise from the winners).
    - bottom: the lowest-engagement third (no floor; genuine low performers).
    The middle is dropped. Sets 'performance_tier' in place.

    Degenerate cases:
      - Empty pool → ([], [])
      - Tiny pool  → third = 1; top and bottom may not overlap because top
                     excludes anything already chosen for bottom.
    """
    if not videos:
        return [], []

    # Secondary key: views. Breaks ties deterministically when the engagement
    # rate is identical, which is not hypothetical: a video posted hours ago
    # has no likes, comments or shares yet, so several fresh videos land on
    # exactly 0.0. Without the tiebreaker their order falls back to whatever
    # order Apify happened to return, and the same corpus can tier differently
    # from one run to the next. The bottom tier feeds the baseline delta, so
    # that instability would propagate into "what the losers do".
    ranked = sorted(
        videos,
        key=lambda v: (_raw_engagement(v), _raw_views(v)),
        reverse=True,
    )
    n      = len(ranked)
    third  = max(1, n // 3)

    top: List[Dict] = [v for v in ranked if _raw_views(v) >= min_views_floor][:third]
    top_ids = {id(v) for v in top}
    bottom: List[Dict] = [v for v in reversed(ranked) if id(v) not in top_ids][:third]

    for v in top:
        v["performance_tier"] = "top"
    for v in bottom:
        v["performance_tier"] = "bottom"

    return top, bottom


def _extract_signals(raw: Dict) -> Dict:
    """
    Extract virality signals from one raw Apify item.
    All fields have safe defaults — never crashes on absent or malformed data.

    Sources (confirmed real clockworks~tiktok-scraper item structure):
      sound       ← raw["musicMeta"]  (musicId, musicName, musicAuthor,
                                        musicOriginal)
      hashtags    ← raw["hashtags"]   (list of dicts; take each ["name"])
      duration_sec← raw["videoMeta"]["duration"]
                    NOTE: videoMeta DOES exist and holds duration + dimensions.
                    It does NOT hold play/digg counts — those are top-level.
    """
    # ── Sound ─────────────────────────────────────────────────────────────
    music = raw.get("musicMeta") or {}
    sound: Dict = {
        "id":          str(music.get("musicId")     or ""),
        "title":       str(music.get("musicName")   or ""),
        "author":      str(music.get("musicAuthor") or ""),
        "is_original": bool(music.get("musicOriginal", False)),
    }

    # ── Hashtags ──────────────────────────────────────────────────────────
    raw_tags = raw.get("hashtags") or []
    hashtags: List[str] = [
        str(tag["name"])
        for tag in raw_tags
        if isinstance(tag, dict) and tag.get("name")
    ]

    # ── Duration ──────────────────────────────────────────────────────────
    video_meta = raw.get("videoMeta") or {}
    duration_sec: int = int(video_meta.get("duration") or 0)

    return {
        "sound":        sound,
        "hashtags":     hashtags,
        "duration_sec": duration_sec,
    }


def transform_video(
    raw: Dict,
    brand_key: str,
    official_handle: str,
    tier: str,
    thumb_path: str = "",
    video_path: str = "",
    comments: Optional[List[Dict]] = None,
) -> Dict:
    """
    Map one raw Apify dataset item to a Contract A video record.
    All Contract A keys are present; missing source fields use safe defaults
    (empty string / 0 / False).

    Args:
        raw:             Apify dataset item dict.
        brand_key:       Lowercase brand key from the config.
        official_handle: The brand's official TikTok handle.
        tier:            'top' or 'bottom'.
        thumb_path:      Relative path to downloaded cover, or remote URL, or "".
        video_path:      Relative path to downloaded mp4, or "".
        comments:        Pre-fetched comment list (or None → empty list).
    """
    meta = raw.get("videoMeta", {})
    auth = raw.get("authorMeta", {})

    video_id = str(raw.get("id") or raw.get("awemeId") or "")

    # Normalise author handle: ensure leading '@'
    handle = str(auth.get("name") or auth.get("nickName") or "").strip()
    if handle and not handle.startswith("@"):
        handle = "@" + handle

    # Metrics — top-level-first (real clockworks~tiktok-scraper shape),
    # with fallback to nested videoMeta for backward compat / unit tests.
    # Use explicit key-in-dict checks so a genuine 0 value is not skipped.
    views  = int(raw["playCount"]    if "playCount"    in raw else meta.get("playCount",    0))
    likes  = int(raw["diggCount"]    if "diggCount"    in raw else meta.get("diggCount",    0))
    n_coms = int(raw["commentCount"] if "commentCount" in raw else meta.get("commentCount", 0))
    shares = int(raw["shareCount"]   if "shareCount"   in raw else meta.get("shareCount",   0))

    # created_at: prefer unix epoch → ISO-8601; fall back to raw string
    ts = raw.get("createTime") or raw.get("createTimeISO") or ""
    if isinstance(ts, (int, float)) and ts > 0:
        created_at: str = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    else:
        created_at = str(ts)

    return {
        "id":    video_id,
        "brand": brand_key,
        "url":   str(raw.get("webVideoUrl") or raw.get("shareUrl") or ""),
        "thumb": thumb_path,
        "local_video_path": video_path,
        "text":  str(raw.get("text") or raw.get("desc") or ""),
        "created_at": created_at,
        "author": {
            "name":             handle,
            "verified":         bool(auth.get("verified", False)),
            "is_brand_account": is_brand_account(handle, official_handle),
        },
        "metrics": {
            "views":           views,
            "likes":           likes,
            "comments":        n_coms,
            "shares":          shares,
            "engagement_rate": calc_engagement_rate(likes, n_coms, shares, views),
        },
        "performance_tier": tier,
        "source": str(raw.get("source") or "profile"),
        "comments": comments if comments is not None else [],
        "signals":  _extract_signals(raw),
    }


def make_index_record(video: Dict) -> Dict:
    """
    Extract the flat fields required by data/raw/index.json from a
    Contract A video record.
    """
    return {
        "id":               video["id"],
        "brand":            video["brand"],
        "performance_tier": video["performance_tier"],
        "source":           video.get("source", "profile"),
        "is_brand_account": video["author"]["is_brand_account"],
        "views":            video["metrics"]["views"],
        "engagement_rate":  video["metrics"]["engagement_rate"],
        "url":              video["url"],
        "thumb":            video["thumb"],
    }


# ════════════════════════════════════════════════════════════════════════════
# Comment grouping helpers — pure / no I/O; unit-testable
# ════════════════════════════════════════════════════════════════════════════

def _extract_video_id_from_url(url: str) -> str:
    """
    Extract the numeric TikTok video ID from a URL.
    TikTok URL pattern: https://www.tiktok.com/@handle/video/<id>
    Returns "" if no all-digit segment is found.
    """
    for part in reversed(url.rstrip("/").split("/")):
        if part.isdigit():
            return part
    return ""


def _normalise_comment(item: Dict) -> Dict:
    """
    Map one raw Apify comment dataset item to {text, likes, author}.
    Used by both the per-video and the batch comment scrapers.
    """
    text   = str(item.get("text") or item.get("commentText") or "").strip()
    likes  = int(item.get("diggCount") or item.get("likes") or 0)
    author = str(item.get("uniqueId") or item.get("author") or "").strip()
    if author and not author.startswith("@"):
        author = "@" + author
    return {"text": text, "likes": likes, "author": author}


def _group_comments_by_video(
    items: List[Dict],
    submitted_urls: List[str],
    cap: int,
) -> Dict[str, List[Dict]]:
    """
    Group raw comment dataset items back to their source video URL.

    Tries to match each item using these fields in order:
      1. videoWebUrl       — direct URL match against submitted_urls
      2. submittedVideoUrl — actor may echo the input URL
      3. postUrl           — alternative field name
      4. awemeId / videoId / id — numeric ID extracted from submitted URLs

    Items that cannot be matched to any submitted URL are discarded with a
    warning log (non-crashing; comments are non-blocking).

    Args:
        items:          Raw dataset items from the comments actor.
        submitted_urls: The postURLs sent to the actor (defines the keyset).
        cap:            Max comments per video applied after grouping.

    Returns:
        {url: [{text, likes, author}]} for every submitted URL.
        Unmatched items are dropped.
    """
    if not submitted_urls:
        return {}

    # Pre-build: TikTok video ID → submitted URL for ID-based fallback
    id_to_url: Dict[str, str] = {}
    for url in submitted_urls:
        vid_id = _extract_video_id_from_url(url)
        if vid_id:
            id_to_url[vid_id] = url

    grouped: Dict[str, List[Dict]] = {url: [] for url in submitted_urls}
    n_ungrouped = 0

    for item in items:
        comment = _normalise_comment(item)
        if not comment["text"]:
            continue  # skip blank comments

        matched = False

        # Try URL fields in order
        for field in ("videoWebUrl", "submittedVideoUrl", "postUrl"):
            source = item.get(field, "")
            if source and source in grouped:
                grouped[source].append(comment)
                matched = True
                break

        if matched:
            continue

        # Try numeric video ID from item
        item_id = str(
            item.get("awemeId") or item.get("videoId") or item.get("id") or ""
        ).strip()
        if item_id and item_id in id_to_url:
            grouped[id_to_url[item_id]].append(comment)
            continue

        n_ungrouped += 1

    if n_ungrouped:
        print(
            f"      ⚠️   {n_ungrouped} comment(s) could not be matched to a "
            f"submitted URL or video ID — discarded."
        )

    # Apply per-video cap
    return {url: comments[:cap] for url, comments in grouped.items()}


# ════════════════════════════════════════════════════════════════════════════
# Apify helpers — run / poll / dataset pattern (mirrors analyzer.py)
# ════════════════════════════════════════════════════════════════════════════

def _apify_run_and_poll(apify_key: str, actor_id: str, run_input: Dict) -> Dict:
    """
    POST a run to Apify and poll until SUCCEEDED.
    Returns the completed run's data dict (contains defaultDatasetId,
    defaultKeyValueStoreId, etc.).
    Raises RuntimeError on FAILED / ABORTED / TIMED-OUT.
    """
    start_url = f"https://api.apify.com/v2/acts/{actor_id}/runs?token={apify_key}"
    resp = requests.post(start_url, json=run_input, timeout=30)
    resp.raise_for_status()
    run_id = resp.json()["data"]["id"]
    print(f"      ⏳  Apify run {run_id[:10]}… ({actor_id})")

    while True:
        status_url = (
            f"https://api.apify.com/v2/acts/{actor_id}/runs/{run_id}"
            f"?token={apify_key}"
        )
        run_data = requests.get(status_url, timeout=30).json()
        status   = run_data["data"]["status"]

        if status == "SUCCEEDED":
            print(f"      ✅  Run {run_id[:10]} succeeded.")
            return run_data["data"]

        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(
                f"Apify run {run_id} ended with status: {status}"
            )

        time.sleep(5)


def _apify_dataset_items(apify_key: str, dataset_id: str) -> List[Dict]:
    """Retrieve all items from an Apify dataset."""
    url  = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={apify_key}"
    resp = requests.get(url, timeout=90)
    resp.raise_for_status()
    return resp.json()


def _apify_kv_keys(apify_key: str, kv_id: str) -> List[Dict]:
    """List all keys in an Apify Key-Value Store."""
    url  = f"https://api.apify.com/v2/key-value-stores/{kv_id}/keys?token={apify_key}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("items", [])


def _apify_kv_get(apify_key: str, kv_id: str, key_name: str) -> bytes:
    """Download the raw bytes of one Key-Value Store record."""
    url = (
        f"https://api.apify.com/v2/key-value-stores/{kv_id}/records/{key_name}"
        f"?token={apify_key}"
    )
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    return resp.content


# ════════════════════════════════════════════════════════════════════════════
# Scraping helpers
# ════════════════════════════════════════════════════════════════════════════

def scrape_brand_pool(
    apify_key: str,
    handle: str,
    pool_size: int,
    videos_dir: Path,
    thumbs_dir: Path,
    source: str = "profile",
) -> List[Dict]:
    """
    Scrape up to pool_size videos from a TikTok source.

    source="profile" → scrape the brand profile `handle`.
    source="hashtag" → scrape the hashtag `handle`; this is
                       owner / creator content where is_brand_account is False.

    Downloads:
      mp4  → videos_dir/<id>.mp4   (shouldDownloadVideos: True)
      cover → thumbs_dir/<id>.jpg  (shouldDownloadCovers: True)

    Returns a list of raw Apify metadata dicts enriched with:
      'local_video_path': str  — relative path or "" if not downloaded
      'local_thumb_path': str  — relative path or "" if not downloaded
      'source':           str  — "profile" or "hashtag"
    """
    videos_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    if source == "hashtag":
        tag = handle.lstrip("#")
        print(f"\n  🔍  Scraping #{tag}  (hashtag, pool_size={pool_size})…")
        run_input: Dict = {
            "hashtags":                [tag],
            "resultsPerPage":          pool_size,
            "shouldDownloadVideos":    True,
            "shouldDownloadCovers":    True,
            "shouldDownloadSubtitles": False,
        }
    else:
        print(f"\n  🔍  Scraping {handle}  (profile, pool_size={pool_size})…")
        run_input = {
            "profiles":                [handle],
            "resultsPerPage":          pool_size,
            "shouldDownloadVideos":    True,
            "shouldDownloadCovers":    True,
            "shouldDownloadSubtitles": False,
        }

    run_data = _apify_run_and_poll(apify_key, ACTOR_VIDEOS, run_input)
    ds_id    = run_data["defaultDatasetId"]
    kv_id    = run_data["defaultKeyValueStoreId"]

    # ── Metadata from dataset ─────────────────────────────────────────────
    items: List[Dict] = _apify_dataset_items(apify_key, ds_id)
    meta_by_id: Dict[str, Dict] = {}
    for item in items:
        vid_id = str(item.get("id") or item.get("awemeId") or "")
        if vid_id:
            meta_by_id[vid_id] = item

    print(f"      📋  {len(meta_by_id)} metadata records from dataset.")

    # ── Files from KV store ───────────────────────────────────────────────
    # clockworks~tiktok-scraper stores files with keys like:
    #   video-<awemeId>.mp4   for videos
    #   cover-<awemeId>.jpg   for covers
    kv_keys  = _apify_kv_keys(apify_key, kv_id)
    enriched: Dict[str, Dict] = {}  # vid_id → enriched metadata

    for key_info in kv_keys:
        key_name: str = key_info["key"]
        low = key_name.lower()

        is_video = low.endswith(".mp4")
        is_cover = low.endswith((".jpg", ".jpeg", ".png", ".webp"))

        if not (is_video or is_cover):
            continue

        # Extract video ID: strip extension, then strip known prefixes
        base = key_name.rsplit(".", 1)[0]
        for prefix in ("video-", "cover-", "VIDEO-", "COVER-"):
            if base.startswith(prefix):
                base = base[len(prefix):]
                break
        vid_id = base

        # Resolve to a known metadata ID (exact match, then suffix fallback)
        if vid_id not in meta_by_id:
            vid_id = next(
                (k for k in meta_by_id
                 if k.endswith(vid_id) or vid_id.endswith(k)),
                None,  # type: ignore[arg-type]
            )
            if vid_id is None:
                continue

        if vid_id not in enriched:
            enriched[vid_id] = dict(meta_by_id[vid_id])
            enriched[vid_id]["local_video_path"] = ""
            enriched[vid_id]["local_thumb_path"]  = ""

        try:
            content = _apify_kv_get(apify_key, kv_id, key_name)
        except Exception as exc:
            print(f"      ⚠️   Could not download {key_name}: {exc}")
            continue

        if is_video:
            dest = videos_dir / f"{vid_id}.mp4"
            dest.write_bytes(content)
            enriched[vid_id]["local_video_path"] = str(dest)
            print(f"      📥  Video {vid_id}.mp4  ({len(content)//1024} KB)")
        else:
            ext  = key_name.rsplit(".", 1)[-1].lower()
            dest = thumbs_dir / f"{vid_id}.{ext}"
            dest.write_bytes(content)
            enriched[vid_id]["local_thumb_path"] = str(dest)
            print(f"      🖼️   Cover {vid_id}.{ext}")

    # Add any metadata entries whose files never appeared in the KV store
    for vid_id, meta in meta_by_id.items():
        if vid_id not in enriched:
            enriched[vid_id] = dict(meta)
            enriched[vid_id]["local_video_path"] = ""
            enriched[vid_id]["local_thumb_path"]  = ""

    result = list(enriched.values())
    for v in result:
        v["source"] = source
    if result:
        print(f"      ✅  Pool collected: {len(result)} videos.")
    else:
        # A zero result is not a success, and printing it with a checkmark is
        # how a real run went blind without anyone noticing: the subject's
        # hashtag pool came back empty, so the corpus had no owner or creator
        # content for that brand and the owners versus brand insight was an
        # artifact of the missing data, not a finding.
        print(f"      ⚠️   Pool EMPTY for {handle} ({source}). "
              "The actor ran fine but returned nothing. If this is a hashtag, "
              "try a different tag; if a profile, check the handle.")
    return result


def scrape_comments_for_video(
    apify_key: str,
    video_url: str,
    cap: int = DEFAULT_COMMENT_CAP,
) -> List[Dict]:
    """
    Scrape up to cap comments for ONE TikTok video URL (single Apify run).
    Used as a fallback when scrape_comments_batch returns nothing.

    Actor: clockworks~tiktok-comments-scraper
      postURLs        — list of TikTok video URLs
      commentsPerPost — max comments per URL

    Returns [{text, likes, author}] or [] on any error (non-blocking).
    """
    if not video_url:
        return []

    run_input: Dict = {
        "postURLs":        [video_url],
        "commentsPerPost": cap,
    }

    try:
        run_data = _apify_run_and_poll(apify_key, ACTOR_COMMENTS, run_input)
        items    = _apify_dataset_items(apify_key, run_data["defaultDatasetId"])
    except Exception as exc:
        print(f"      ⚠️   Comment scrape failed for {video_url}: {exc}")
        return []

    return [
        c for c in (_normalise_comment(i) for i in items[:cap]) if c["text"]
    ]


def scrape_comments_batch(
    apify_key: str,
    video_urls: List[str],
    cap: int = DEFAULT_COMMENT_CAP,
    chunk_size: int = COMMENT_BATCH_CHUNK,
) -> Dict[str, List[Dict]]:
    """
    Scrape comments for MANY TikTok video URLs in a SINGLE Apify run.

    One cold-start instead of N sequential runs: ~105 videos → 1 run vs ~105,
    saving roughly an hour of wall-clock time and significant compute units.

    Actor input:
      postURLs        — all kept video URLs submitted at once
      commentsPerPost — per-video cap

    The returned dataset items are grouped back to their source video by
    _group_comments_by_video (tries videoWebUrl / submittedVideoUrl / postUrl
    / numeric video ID in that order; see its docstring for fallback logic).

    Args:
        apify_key:   Apify API key.
        video_urls:  All kept video URLs across all brands.
        cap:         Max comments per video.

    Returns:
        {video_url: [{text, likes, author}]} for every submitted URL.
        On any Apify error returns {url: []} for all URLs (non-crashing).
    """
    if not video_urls:
        return {}

    # Deduplicate while preserving order (in case the same URL appears twice)
    seen: Dict[str, None] = {}
    unique_urls: List[str] = []
    for u in video_urls:
        if u not in seen:
            seen[u] = None
            unique_urls.append(u)

    # Chunk the URLs. The comments actor silently drops URLs above a per-run
    # ceiling (a single 96-URL run returned 0 comments for a whole brand while
    # the same video returned 30 in a small batch), so we submit small chunks.
    chunks: List[List[str]] = [
        unique_urls[i:i + chunk_size]
        for i in range(0, len(unique_urls), chunk_size)
    ]
    print(
        f"\n  💬  Batch comment scrape: {len(unique_urls)} URLs "
        f"in {len(chunks)} chunk(s) of <= {chunk_size}…"
    )

    grouped: Dict[str, List[Dict]] = {url: [] for url in unique_urls}

    for ci, chunk in enumerate(chunks, 1):
        run_input: Dict = {
            "postURLs":        chunk,
            "commentsPerPost": cap,
        }
        try:
            run_data = _apify_run_and_poll(apify_key, ACTOR_COMMENTS, run_input)
            items    = _apify_dataset_items(apify_key, run_data["defaultDatasetId"])
        except Exception as exc:
            print(f"      ⚠️   Comment chunk {ci}/{len(chunks)} failed: {exc}")
            continue
        chunk_grouped = _group_comments_by_video(items, chunk, cap)
        for url, coms in chunk_grouped.items():
            grouped[url] = coms

    # Re-key by original (possibly duplicated) URLs so callers always find their key
    return {url: grouped.get(url, []) for url in video_urls}


# ════════════════════════════════════════════════════════════════════════════
# Orchestration
# ════════════════════════════════════════════════════════════════════════════

def run_ingestion(
    brands: Dict[str, str],
    pool_size: int = DEFAULT_POOL_SIZE,
    comment_cap: int = DEFAULT_COMMENT_CAP,
    raw_dir: Path = RAW_DIR,
    thumbs_dir: Path = THUMBS_DIR,
    videos_dir: Path = VIDEOS_DIR,
    apify_key: Optional[str] = None,
    hashtags: Optional[Dict[str, str]] = None,
    hashtag_pool_size: int = DEFAULT_HASHTAG_POOL_SIZE,
) -> None:
    """
    Full ingestion pipeline for one or more brands.

    Phase 1 — scrape video pools (metadata + mp4 + cover) for every brand.
    Phase 2 — split each pool top/bottom, then balance tiers cross-brand.
    Phase 3 — batch comment scrape (ONE Apify run for all kept videos).
    Phase 4 — transform to Contract A, write per-brand JSON + index.json.

    Args:
        brands:      {brand_key: handle} dict, from the brand config.
        pool_size:   Videos scraped per brand before the split.
        comment_cap: Max comments per kept video.
        raw_dir:     Output dir for brand JSON files (default: data/raw/).
        thumbs_dir:  Output dir for cover images (default: data/raw/thumbs/).
        videos_dir:  Output dir for mp4 files (default: data/videos/).
        apify_key:   Override APIFY_API_KEY env var (tests / CI).
    """
    # Resolve API key: prefer explicit arg, fall back to env, then raise.
    # Instantiate TikTokAnalyzer to reuse its key-loading setup (contract req).
    _analyzer = TikTokAnalyzer()
    key: Optional[str] = apify_key or _analyzer.apify_key or APIFY_API_KEY
    if not key:
        raise EnvironmentError(
            "APIFY_API_KEY not set. Add it to .env or pass apify_key= explicitly."
        )

    raw_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    videos_dir.mkdir(parents=True, exist_ok=True)

    # ── Phase 1: Scrape all brand pools ──────────────────────────────────
    _sep()
    print("PHASE 1 — scrape video pools")
    _sep()

    if hashtags is None:
        # No hashtag map supplied: scrape profiles only. Callers that want the
        # owner and creator corpus pass hashtags explicitly, and main() always
        # does, from the brand config.
        hashtags = {}

    pools: Dict[str, List[Dict]] = {}
    for brand_key, handle in brands.items():
        print(f"\n▶  {brand_key.upper()} ({handle})")

        # Brand profile content (is_brand_account True)
        profile_pool = scrape_brand_pool(
            key, handle, pool_size, videos_dir, thumbs_dir, source="profile"
        )

        # Owner / creator content via the brand hashtag (is_brand_account False)
        tag = hashtags.get(brand_key)
        hashtag_pool: List[Dict] = []
        if tag and hashtag_pool_size > 0:
            hashtag_pool = scrape_brand_pool(
                key, tag, hashtag_pool_size, videos_dir, thumbs_dir, source="hashtag"
            )

        # Combine + dedup by id, preferring the profile copy of any duplicate
        combined: Dict[str, Dict] = {}
        for raw in profile_pool:
            vid = str(raw.get("id") or raw.get("awemeId") or "")
            if vid:
                combined[vid] = raw
        for raw in hashtag_pool:
            vid = str(raw.get("id") or raw.get("awemeId") or "")
            if vid and vid not in combined:
                combined[vid] = raw

        pool = list(combined.values())
        n_owner = sum(1 for v in pool if v.get("source") == "hashtag")
        print(f"      🧮  Combined pool: {len(pool)} videos "
              f"({len(pool) - n_owner} profile / {n_owner} hashtag).")
        pools[brand_key] = pool

    # ── Phase 2: Split + balance across brands ────────────────────────────
    _sep()
    print("PHASE 2 — split top/bottom, balance tiers")
    _sep()

    splits: Dict[str, Tuple[List[Dict], List[Dict]]] = {}
    for brand_key, pool in pools.items():
        top, bottom = split_top_bottom(pool)
        splits[brand_key] = (top, bottom)
        print(f"  {brand_key:10s}: {len(top)} top / {len(bottom)} bottom "
              f"(from pool of {len(pool)})")

    # Cap each tier at the cross-brand minimum
    min_top    = min((len(t) for t, _ in splits.values()), default=0)
    min_bottom = min((len(b) for _, b in splits.values()), default=0)
    print(f"\n  Balance → {min_top} top / {min_bottom} bottom per brand")

    # The balance rule levels every brand down to the weakest one, so a single
    # failed hashtag scrape can quietly throw away most of what was paid for.
    # In a real run one empty pool shrank 19 scraped videos to 6 analyzed.
    scraped = sum(len(p) for p in pools.values())
    kept = (min_top + min_bottom) * len(splits)
    if scraped and kept < scraped * 0.6:
        print(f"  ⚠️   Balancing keeps {kept} of {scraped} scraped videos. "
              "One brand's pool is much smaller than the others (an empty "
              "hashtag result, a wrong handle, or a tiny profile). The money "
              "for the discarded videos is already spent; consider fixing the "
              "weak brand and rerunning before trusting the baseline.")
    if kept and kept < 12:
        print(f"  ⚠️   Only {kept} videos survive tiering. Percentages on a "
              "corpus this size are anecdotes, not a baseline. The dashboard "
              "will say so.")

    for brand_key in splits:
        t, b = splits[brand_key]
        splits[brand_key] = (t[:min_top], b[:min_bottom])

    # ── Phase 3: Batch comment scrape (one Apify run for all kept videos) ──
    _sep()
    print("PHASE 3 — batch comment scrape")
    _sep()

    # Flatten kept videos across all brands; preserve brand association
    kept_by_brand: Dict[str, List[Dict]] = {}
    all_urls: List[str] = []

    for brand_key in brands:
        top, bottom  = splits[brand_key]
        kept         = top + bottom
        kept_by_brand[brand_key] = kept
        for raw in kept:
            url = str(raw.get("webVideoUrl") or raw.get("shareUrl") or "")
            if url:
                all_urls.append(url)

    total_kept = sum(len(v) for v in kept_by_brand.values())
    print(f"\n  {total_kept} kept videos across {len(brands)} brand(s).")

    comments_by_url: Dict[str, List[Dict]] = scrape_comments_batch(
        key, all_urls, cap=comment_cap
    )

    # Fallback: if the entire batch returned no comments, retry per-video
    if all_urls and not any(comments_by_url.values()):
        print(
            "  ⚠️   Batch returned no comments for any URL. "
            "Falling back to per-video scraping."
        )
        for url in all_urls:
            comments_by_url[url] = scrape_comments_for_video(
                key, url, cap=comment_cap
            )

    total_comments = sum(len(v) for v in comments_by_url.values())
    print(f"  ✅  {total_comments} comments collected across all videos.")

    # ── Phase 4: Transform + write ────────────────────────────────────────
    _sep()
    print("PHASE 4 — transform and write")
    _sep()

    all_index: List[Dict] = []
    scraped_at: str = datetime.now(tz=timezone.utc).isoformat()

    for brand_key, handle in brands.items():
        print(f"\n▶  {brand_key.upper()} ({handle})")
        kept = kept_by_brand[brand_key]

        video_records: List[Dict] = []
        for raw in kept:
            tier   = raw["performance_tier"]
            vid_id = str(raw.get("id") or raw.get("awemeId") or "")
            url    = str(raw.get("webVideoUrl") or raw.get("shareUrl") or "")

            # Relative thumb path: local download preferred, then remote cover URL
            local_thumb = raw.get("local_thumb_path", "")
            if local_thumb and Path(local_thumb).exists():
                thumb_rel = local_thumb
            else:
                cover_meta = raw.get("coverMeta") or {}
                if isinstance(cover_meta, dict):
                    thumb_rel = (
                        cover_meta.get("originCover")
                        or cover_meta.get("dynamicCover")
                        or ""
                    )
                else:
                    thumb_rel = ""

            # Relative video path
            local_video = raw.get("local_video_path", "")
            video_rel   = local_video if (local_video and Path(local_video).exists()) else ""

            # Comments pre-fetched in Phase 3
            coms = comments_by_url.get(url, [])
            print(f"  [{tier:6s}] {vid_id[:12]}…  {len(coms)} comment(s).")

            record = transform_video(
                raw             = raw,
                brand_key       = brand_key,
                official_handle = handle,
                tier            = tier,
                thumb_path      = thumb_rel,
                video_path      = video_rel,
                comments        = coms,
            )
            video_records.append(record)

        # Write per-brand Contract A file
        brand_doc: Dict = {
            "brand":      brand_key,
            "profile":    handle,
            "scraped_at": scraped_at,
            "pool_size":  len(pools.get(brand_key, [])),
            "videos":     video_records,
        }
        out_path = raw_dir / f"{brand_key}.json"
        out_path.write_text(
            json.dumps(brand_doc, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  💾  {out_path}  ({len(video_records)} videos)")

        all_index.extend(make_index_record(v) for v in video_records)

    # Write flat index
    index_path = raw_dir / "index.json"
    index_path.write_text(
        json.dumps(all_index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n📋  index.json  ({len(all_index)} total records)")
    print("\n✅  Ingestion complete.")


def _sep() -> None:
    print("\n" + "=" * 60)


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "SIGNAL ingestion: scrape TikTok brand accounts and write "
            "data/raw/<brand>.json + data/raw/index.json."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python ingest.py                           # all brands, defaults
  python ingest.py --brand <key>             # one brand from the config
  python ingest.py --pool-size 40            # larger pool per brand
  python ingest.py --config config/other.json --pool-size 20
        """,
    )
    parser.add_argument(
        "--brand",
        default=None,
        metavar="KEY",
        help=(
            "Scrape a single brand key from the config only "
            "(default: every brand in the config)."
        ),
    )
    add_config_argument(parser)
    parser.add_argument(
        "--pool-size",
        type=int,
        default=DEFAULT_POOL_SIZE,
        metavar="N",
        help=f"Videos to scrape per brand before split (default: {DEFAULT_POOL_SIZE}).",
    )
    parser.add_argument(
        "--comment-cap",
        type=int,
        default=DEFAULT_COMMENT_CAP,
        metavar="N",
        help=f"Max comments per kept video (default: {DEFAULT_COMMENT_CAP}).",
    )
    parser.add_argument(
        "--hashtag-pool-size",
        type=int,
        default=DEFAULT_HASHTAG_POOL_SIZE,
        metavar="N",
        help=(
            "Owner/creator videos to scrape per brand hashtag "
            f"(default: {DEFAULT_HASHTAG_POOL_SIZE}; set 0 to disable hashtags)."
        ),
    )

    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except BrandConfigError as exc:
        print(f"❌  {exc}")
        raise SystemExit(1)

    handles  = cfg.handles
    hashtags = cfg.hashtags

    if args.brand:
        if args.brand not in handles:
            print(
                "❌  '{}' is not in {}. Available: {}".format(
                    args.brand, cfg.source_path.name, ", ".join(cfg.keys)
                )
            )
            raise SystemExit(1)
        brands_to_run: Dict[str, str] = {args.brand: handles[args.brand]}
        hashtags = {args.brand: hashtags[args.brand]}
    else:
        brands_to_run = dict(handles)

    _sep()
    print("SIGNAL — INGESTION")
    print(f"Config     : {cfg.source_path.name}")
    print(f"Subject    : {cfg.subject.name} ({cfg.subject.handle})")
    print(f"Brands     : {list(brands_to_run.keys())}")
    print(f"Pool size  : {args.pool_size}")
    print(f"Comment cap: {args.comment_cap}")
    _sep()

    if not APIFY_API_KEY:
        print("❌  APIFY_API_KEY not found in .env — aborting.")
        raise SystemExit(1)

    run_ingestion(
        brands            = brands_to_run,
        pool_size         = args.pool_size,
        comment_cap       = args.comment_cap,
        hashtags          = hashtags,
        hashtag_pool_size = args.hashtag_pool_size,
    )


if __name__ == "__main__":
    main()
