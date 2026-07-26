#!/usr/bin/env python3
"""
geo_scrape.py  --  honest creator-origin geolocation for Signal.

Public TikTok scraping does NOT expose viewer geography (that needs the Business
API). What it DOES expose is the creator's account region. This script re-scrapes
metadata only (no video or cover downloads) for the videos already in data/raw,
extracts authorMeta.region per video, aggregates by country, and writes the result
into signal.json under insight.geo, clearly labeled as creator origin.

Usage:
    python geo_scrape.py
"""

from __future__ import annotations

import json

from brandconfig import load_config
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import requests
from dotenv import load_dotenv

load_dotenv()
APIFY_API_KEY = os.getenv("APIFY_API_KEY")
ACTOR = "clockworks~tiktok-scraper"

# Minimal ISO 3166-1 alpha-2 -> (name, flag emoji) for the countries we expect.
COUNTRY = {
    "US": ("United States", "\U0001F1FA\U0001F1F8"), "GB": ("United Kingdom", "\U0001F1EC\U0001F1E7"),
    "CA": ("Canada", "\U0001F1E8\U0001F1E6"), "AU": ("Australia", "\U0001F1E6\U0001F1FA"),
    "DE": ("Germany", "\U0001F1E9\U0001F1EA"), "IT": ("Italy", "\U0001F1EE\U0001F1F9"),
    "FR": ("France", "\U0001F1EB\U0001F1F7"), "ES": ("Spain", "\U0001F1EA\U0001F1F8"),
    "NL": ("Netherlands", "\U0001F1F3\U0001F1F1"), "JP": ("Japan", "\U0001F1EF\U0001F1F5"),
    "KR": ("South Korea", "\U0001F1F0\U0001F1F7"), "ID": ("Indonesia", "\U0001F1EE\U0001F1E9"),
    "PH": ("Philippines", "\U0001F1F5\U0001F1ED"), "BR": ("Brazil", "\U0001F1E7\U0001F1F7"),
    "MX": ("Mexico", "\U0001F1F2\U0001F1FD"), "IN": ("India", "\U0001F1EE\U0001F1F3"),
    "AE": ("United Arab Emirates", "\U0001F1E6\U0001F1EA"), "SA": ("Saudi Arabia", "\U0001F1F8\U0001F1E6"),
    "TR": ("Turkey", "\U0001F1F9\U0001F1F7"), "PL": ("Poland", "\U0001F1F5\U0001F1F1"),
    "TH": ("Thailand", "\U0001F1F9\U0001F1ED"), "MY": ("Malaysia", "\U0001F1F2\U0001F1FE"),
    "VN": ("Vietnam", "\U0001F1FB\U0001F1F3"), "SG": ("Singapore", "\U0001F1F8\U0001F1EC"),
    "RU": ("Russia", "\U0001F1F7\U0001F1FA"), "ZA": ("South Africa", "\U0001F1FF\U0001F1E6"),
    "CH": ("Switzerland", "\U0001F1E8\U0001F1ED"), "SE": ("Sweden", "\U0001F1F8\U0001F1EA"),
    "AT": ("Austria", "\U0001F1E6\U0001F1F9"), "BE": ("Belgium", "\U0001F1E7\U0001F1EA"),
}


def _run_and_poll(run_input: Dict) -> Dict:
    url = f"https://api.apify.com/v2/acts/{ACTOR}/runs?token={APIFY_API_KEY}"
    r = requests.post(url, json=run_input, timeout=30); r.raise_for_status()
    rid = r.json()["data"]["id"]
    print(f"  Apify run {rid[:10]}...")
    while True:
        s = requests.get(f"https://api.apify.com/v2/acts/{ACTOR}/runs/{rid}?token={APIFY_API_KEY}", timeout=30).json()
        st = s["data"]["status"]
        if st == "SUCCEEDED":
            return s["data"]
        if st in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run ended: {st}")
        time.sleep(5)


def _dataset(ds_id: str) -> List[Dict]:
    r = requests.get(f"https://api.apify.com/v2/datasets/{ds_id}/items?token={APIFY_API_KEY}", timeout=120)
    r.raise_for_status()
    return r.json()


def main() -> None:
    if not APIFY_API_KEY:
        raise SystemExit("APIFY_API_KEY not set")

    # Build id -> brand/engagement from existing data, and the URL list.
    by_id: Dict[str, Dict] = {}
    urls: List[str] = []
    for b in load_config().keys:
        for v in json.load(open(f"data/raw/{b}.json"))["videos"]:
            by_id[v["id"]] = v
            if v.get("url"):
                urls.append(v["url"])
    print(f"Scraping creator region for {len(urls)} videos (metadata only)...")

    # Chunk postURLs to stay under any per run ceiling.
    region_by_id: Dict[str, str] = {}
    CHUNK = 30
    for i in range(0, len(urls), CHUNK):
        chunk = urls[i:i + CHUNK]
        run_input = {
            "postURLs": chunk,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
        }
        try:
            data = _run_and_poll(run_input)
            items = _dataset(data["defaultDatasetId"])
        except Exception as exc:
            print(f"  chunk {i//CHUNK+1} failed: {exc}")
            continue
        for it in items:
            vid = str(it.get("id") or it.get("awemeId") or "")
            # locationCreated is the country the post was created in (creator origin).
            region = str(it.get("locationCreated") or "").upper().strip()
            if vid and region:
                region_by_id[vid] = region
        print(f"  chunk {i//CHUNK+1}: {len(region_by_id)} regions so far")

    # Aggregate by country: count + avg engagement of those creators' videos.
    agg: Dict[str, Dict] = defaultdict(lambda: {"count": 0, "eng_sum": 0.0})
    matched = 0
    for vid, region in region_by_id.items():
        rec = by_id.get(vid)
        if not rec:
            continue
        matched += 1
        a = agg[region]
        a["count"] += 1
        a["eng_sum"] += float(rec.get("metrics", {}).get("engagement_rate", 0.0))

    total = sum(a["count"] for a in agg.values()) or 1
    geo = []
    for code, a in agg.items():
        name, flag = COUNTRY.get(code, (code, "\U0001F3F3"))
        geo.append({
            "code": code,
            "country": name,
            "flag": flag,
            "count": a["count"],
            "pct": round(a["count"] / total * 100, 1),
            "avg_engagement": round(a["eng_sum"] / a["count"], 2) if a["count"] else 0.0,
        })
    geo.sort(key=lambda g: -g["count"])

    print(f"Matched {matched} videos to a creator region across {len(geo)} countries.")
    for g in geo[:8]:
        print(f"  {g['flag']} {g['country']:18s} {g['count']:3d}  ({g['pct']}%)  eng {g['avg_engagement']}")

    # Merge into signal.json under insight.geo (with an honest note).
    sig = json.load(open("signal.json"))
    sig.setdefault("insight", {})["geo"] = {
        "note": "Creator origin, not audience location. Public TikTok data does not expose viewer geography; this is where the accounts that posted are based.",
        "total_located": matched,
        "countries": geo,
    }
    json.dump(sig, open("signal.json", "w"), indent=2, ensure_ascii=False)
    print(f"Wrote insight.geo to signal.json ({matched} located, {len(geo)} countries).")


if __name__ == "__main__":
    main()
