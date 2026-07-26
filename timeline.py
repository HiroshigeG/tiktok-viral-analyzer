#!/usr/bin/env python3
"""
timeline.py  --  how the subject brand's account has moved over time.

Scrapes a deep, metadata only pool of the subject's own profile (no video
downloads, so it is cheap), buckets the posts by publish date, and measures
two things per period:

  engagement   average engagement rate, likes, comments, shares, views of the
               posts published in that period
  sentiment    positive versus negative share of a sampled set of comments on
               those posts, scored with a small lexicon

Output is timeline.json, which the dashboard renders as the evolution charts.

Honesty, because this file feeds a chart people will point at:

  * Metrics are TODAY'S totals for posts grouped by WHEN THEY WERE PUBLISHED.
    TikTok engagement concentrates in a video's first days, so this is a fair
    proxy for "how posts from that era performed", but it is not a historical
    export of the account's analytics. The output labels it.
  * "Since the account opened" is bounded by what the scraper returns. The
    profile endpoint serves newest first; the oldest post in the pool is the
    real start of the timeline, and meta.covers_from says what it is.
  * Sentiment is a keyword and emoji lexicon over a sample of comments,
    labeled directional. It reads obvious praise and obvious contempt; it
    does not read irony. Counts are shown so nobody mistakes 12 comments
    for a survey.

Usage:
    python3 timeline.py                          # subject from config, pool 120
    python3 timeline.py --pool 200 --comment-videos 30
    python3 timeline.py --config config/other.json --output timeline.json
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from brandconfig import BrandConfigError, add_config_argument, load_config
from ingest import APIFY_API_KEY, scrape_comments_batch

load_dotenv()

ACTOR = "clockworks~tiktok-scraper"

DEFAULT_POOL = 120           # metadata records to pull from the profile
DEFAULT_COMMENT_VIDEOS = 24  # videos sampled across the span for sentiment
DEFAULT_COMMENT_CAP = 12     # comments per sampled video


# ── Sentiment lexicon ────────────────────────────────────────────────────────
# Deliberately small and obvious. The point is direction, not nuance, and a
# lexicon that only fires on unambiguous signals is more honest than one that
# guesses. Multilingual-lite: English plus common Italian and Spanish praise.

_POS_WORDS = frozenset("""
love loved loving amazing beautiful gorgeous perfect fire goat insane stunning
clean crisp best epic wow obsessed need want iconic classic heat hard slaps
banger masterpiece chef great awesome incredible legendary crazy sick dope
bellissimo bellissima stupendo adoro amore perfetto increibile hermoso
precioso guapo lindo
""".split())

_NEG_WORDS = frozenset("""
worst bad ugly hate hated terrible awful disappointing overpriced trash boring
mid cheap flop cringe scam fake greedy lazy downgrade ruined worse skip
brutto orribile deludente schifo pessimo feo malo caro
""".split())

_POS_EMOJI = tuple("❤🔥😍🤩👏💯🙌✨😻🥰")
_NEG_EMOJI = tuple("👎🤮😡💩🙄😤")


def score_comment(text: str) -> int:
    """+1 positive, -1 negative, 0 neutral or unreadable."""
    t = (text or "").lower()
    pos = sum(1 for w in _POS_WORDS if w in t.split()) + sum(t.count(e) for e in _POS_EMOJI)
    neg = sum(1 for w in _NEG_WORDS if w in t.split()) + sum(t.count(e) for e in _NEG_EMOJI)
    if pos > neg:
        return 1
    if neg > pos:
        return -1
    return 0


# ── Scrape ───────────────────────────────────────────────────────────────────

def _metric(item: Dict, key: str) -> int:
    if key in item:
        try:
            return int(item[key] or 0)
        except (TypeError, ValueError):
            return 0
    try:
        return int(item.get("videoMeta", {}).get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _created_at(item: Dict) -> Optional[datetime]:
    iso = item.get("createTimeISO")
    if iso:
        try:
            return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        except ValueError:
            pass
    epoch = item.get("createTime")
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def scrape_profile_metadata(handle: str, pool: int) -> List[Dict[str, Any]]:
    """Metadata only profile scrape: no videos, no covers, cheap and fast."""
    if not APIFY_API_KEY:
        raise SystemExit("APIFY_API_KEY not set; the timeline needs the scraper.")
    start = requests.post(
        f"https://api.apify.com/v2/acts/{ACTOR}/runs?token={APIFY_API_KEY}",
        json={
            "profiles": [handle.lstrip("@")],
            "resultsPerPage": pool,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
        },
        timeout=30,
    )
    start.raise_for_status()
    run_id = start.json()["data"]["id"]
    print(f"  Apify run {run_id[:10]}… (metadata only, pool {pool})")
    while True:
        state = requests.get(
            f"https://api.apify.com/v2/acts/{ACTOR}/runs/{run_id}?token={APIFY_API_KEY}",
            timeout=30,
        ).json()["data"]
        if state["status"] == "SUCCEEDED":
            break
        if state["status"] in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise SystemExit(f"Apify run ended: {state['status']}")
        time.sleep(5)
    items = requests.get(
        f"https://api.apify.com/v2/datasets/{state['defaultDatasetId']}/items?token={APIFY_API_KEY}",
        timeout=120,
    ).json()
    print(f"  {len(items)} posts returned.")
    return items if isinstance(items, list) else []


# ── Bucketing ────────────────────────────────────────────────────────────────

def bucket_key(dt: datetime, quarterly: bool) -> str:
    if quarterly:
        return f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"
    return f"{dt.year}-{dt.month:02d}"


def build_timeline(
    items: List[Dict[str, Any]],
    comment_videos: int,
    comment_cap: int,
) -> Dict[str, Any]:
    posts: List[Dict[str, Any]] = []
    for it in items:
        dt = _created_at(it)
        if not dt:
            continue
        views = _metric(it, "playCount")
        likes = _metric(it, "diggCount")
        comments = _metric(it, "commentCount")
        shares = _metric(it, "shareCount")
        er = round((likes + comments + shares) / views * 100, 2) if views > 0 else 0.0
        posts.append({
            "id": str(it.get("id", "")),
            "url": it.get("webVideoUrl", ""),
            "dt": dt,
            "views": views, "likes": likes, "comments": comments,
            "shares": shares, "er": er,
        })

    if not posts:
        raise SystemExit("No dated posts in the scrape; nothing to build.")

    posts.sort(key=lambda p: p["dt"])
    span_months = (
        (posts[-1]["dt"].year - posts[0]["dt"].year) * 12
        + posts[-1]["dt"].month - posts[0]["dt"].month + 1
    )
    quarterly = span_months > 36

    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in posts:
        groups[bucket_key(p["dt"], quarterly)].append(p)

    # ── Sentiment sample: stratified per bucket ──────────────────────────
    # An even stride across the span left whole quarters unsampled when the
    # posting cadence was uneven (the early years post rarely, the recent ones
    # daily). Guarantee at least one sampled post per bucket first, then give
    # the remaining budget to the busiest buckets.
    with_url = [p for p in posts if p["url"]]
    by_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for pt in with_url:
        by_bucket[bucket_key(pt["dt"], quarterly)].append(pt)
    sampled: List[Dict[str, Any]] = []
    for ps in by_bucket.values():          # one per bucket, the most engaged
        sampled.append(max(ps, key=lambda q: q["er"]))
    rest = sorted(
        (pt for ps in by_bucket.values() for pt in ps if pt not in sampled),
        key=lambda q: -q["views"],
    )
    sampled.extend(rest[: max(0, comment_videos - len(sampled))])
    print(f"  Sampling comments on {len(sampled)} posts across the span…")
    comments_by_url = scrape_comments_batch(
        APIFY_API_KEY, [p["url"] for p in sampled], cap=comment_cap
    )
    sent_by_bucket: Dict[str, Dict[str, int]] = defaultdict(lambda: {"pos": 0, "neg": 0, "neutral": 0, "sampled": 0})
    for p in sampled:
        bucket = bucket_key(p["dt"], quarterly)
        for c in comments_by_url.get(p["url"], []):
            s = score_comment(str(c.get("text", "")))
            sent_by_bucket[bucket]["sampled"] += 1
            if s > 0:
                sent_by_bucket[bucket]["pos"] += 1
            elif s < 0:
                sent_by_bucket[bucket]["neg"] += 1
            else:
                sent_by_bucket[bucket]["neutral"] += 1

    # ── Assemble buckets in chronological order ──────────────────────────
    buckets: List[Dict[str, Any]] = []
    for key in sorted(groups):
        ps = groups[key]
        n = len(ps)
        sent = sent_by_bucket.get(key, {"pos": 0, "neg": 0, "neutral": 0, "sampled": 0})
        scored = sent["pos"] + sent["neg"]
        buckets.append({
            "period": key,
            "n_posts": n,
            "avg_engagement_rate": round(sum(p["er"] for p in ps) / n, 2),
            "avg_views": int(sum(p["views"] for p in ps) / n),
            "avg_likes": int(sum(p["likes"] for p in ps) / n),
            "avg_comments": int(sum(p["comments"] for p in ps) / n),
            "avg_shares": int(sum(p["shares"] for p in ps) / n),
            "sentiment": {
                **sent,
                # -1..1, positive share minus negative share of the scored ones
                "score": round((sent["pos"] - sent["neg"]) / scored, 2) if scored else None,
            },
        })

    return {
        "granularity": "quarter" if quarterly else "month",
        "covers_from": posts[0]["dt"].strftime("%Y-%m-%d"),
        "covers_to": posts[-1]["dt"].strftime("%Y-%m-%d"),
        "n_posts": len(posts),
        "buckets": buckets,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the subject brand's evolution timeline.")
    ap.add_argument("--pool", type=int, default=DEFAULT_POOL,
                    help=f"Profile posts to fetch, metadata only (default {DEFAULT_POOL}).")
    ap.add_argument("--comment-videos", type=int, default=DEFAULT_COMMENT_VIDEOS,
                    help="Posts sampled across the span for sentiment.")
    ap.add_argument("--comment-cap", type=int, default=DEFAULT_COMMENT_CAP,
                    help="Comments per sampled post.")
    ap.add_argument("--output", default="timeline.json")
    add_config_argument(ap)
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except BrandConfigError as exc:
        raise SystemExit(f"error: {exc}")

    print(f"Timeline for {cfg.subject.name} ({cfg.subject.handle})")
    items = scrape_profile_metadata(cfg.subject.handle, args.pool)
    if not items:
        raise SystemExit(
            "The profile scrape returned nothing. Check the handle; nothing was written."
        )

    data = build_timeline(items, args.comment_videos, args.comment_cap)
    data = {
        "meta": {
            "subject": cfg.subject.name,
            "handle": cfg.subject.handle,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "covers_from": data.pop("covers_from"),
            "covers_to": data.pop("covers_to"),
            "n_posts": data.pop("n_posts"),
            "granularity": data.pop("granularity"),
            "metrics_note": (
                "Metrics are today's totals for posts grouped by publish date. "
                "TikTok engagement concentrates in a video's first days, so this "
                "reads as how posts from each period performed, not as a "
                "historical analytics export."
            ),
            "sentiment_note": (
                "Sentiment is a keyword and emoji lexicon over a sample of "
                "comments, labeled directional. Sample sizes are shown per "
                "period; small ones are anecdotes."
            ),
        },
        **data,
    }

    out = Path(args.output)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    b = data["buckets"]
    print(f"Wrote {out}  ({data['meta']['covers_from']} → {data['meta']['covers_to']}, "
          f"{len(b)} {data['meta']['granularity']}s, {data['meta']['n_posts']} posts)")


if __name__ == "__main__":
    main()
