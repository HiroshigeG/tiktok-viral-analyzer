#!/usr/bin/env python3
"""
derive.py  --  turn a brand name or a URL into a filled in config and a draft brief.

The pipeline needs a subject handle, a hashtag, a competitor set and a brief.
Typing all of that is the thing standing between "I want to look at this brand"
and an answer, so this module proposes it and the caller confirms.

What it does and does not claim:

  competitors   a model knows categories well. Treat as a good default.
  brief         drafted from the brand's own site when a URL is given, from the
                model's knowledge otherwise. It is a DRAFT. A homepage gives you
                marketing copy, not the positioning, and every brand_fit score
                answers to this file. Read it before running.
  handles       a model will happily invent a plausible @handle that does not
                exist, and a wrong handle scrapes a fan account, which silently
                inverts the owners versus brand insight. So handles are verified
                against TikTok before they are trusted. Never skip that.
  hashtags      same risk, plus the collision problem: bare brand words are
                often a place or a band first. Verified the same way.

Usage as a module:
    from derive import derive_brand, verify_accounts
    proposal = derive_brand("nike.com")
    checked  = verify_accounts([("nike", "@nike"), ("adidas", "@adidas")])

Usage from the shell:
    python3 derive.py "patagonia.com"
"""

from __future__ import annotations

import json
import os
import re
import sys
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
APIFY_API_KEY: Optional[str] = os.getenv("APIFY_API_KEY")

ACTOR_VIDEOS = "clockworks~tiktok-scraper"
SITE_CHARS = 12000          # enough of a homepage to read the positioning from

# Below this, an unverified account is almost certainly not the brand's own.
FOLLOWER_FLOOR = 10000


class DeriveError(Exception):
    """Raised with a message meant for a person, not a stack trace."""


# ── Reading the brand's own site ─────────────────────────────────────────────

class _Text(HTMLParser):
    """Strip a page down to readable copy, dropping script, style and nav noise."""

    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if len(text) > 2:
            self.parts.append(text)


def looks_like_url(value: str) -> bool:
    v = value.strip().lower()
    return v.startswith(("http://", "https://")) or bool(
        re.match(r"^[a-z0-9][a-z0-9\-]*(\.[a-z]{2,})+(/|$)", v)
    )


def fetch_site(url: str, timeout: int = 12) -> str:
    """Fetch a page and return its readable text. Empty string on any failure."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = requests.get(
            url, timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SignalBot/1.0)"},
        )
        r.raise_for_status()
    except requests.RequestException:
        return ""
    parser = _Text()
    try:
        parser.feed(r.text)
    except Exception:                                      # noqa: BLE001
        return ""
    text = "\n".join(parser.parts)
    return re.sub(r"\n{3,}", "\n\n", text)[:SITE_CHARS]


# ── The proposal ─────────────────────────────────────────────────────────────

_SCHEMA_NOTE = """Return ONLY valid JSON, no markdown fences, no commentary, exactly this shape:

{
  "name": "the brand's display name",
  "key": "lowercase, letters and digits only",
  "handle": "@theirTikTokHandle",
  "hashtag": "the hashtag where owner and creator content about them lives, no #",
  "handle_confidence": "high | medium | low",
  "competitors": [
    {"name": "", "key": "", "handle": "@", "hashtag": ""},
    {"name": "", "key": "", "handle": "@", "hashtag": ""}
  ]
}"""

_BRIEF_SHAPE = """The brief must follow this structure exactly, in English:

# <Brand> Brand Brief (for brand-fit scoring)

## The essence
Three or four sentences. The core truth the brand owns and no competitor can claim,
the register it speaks in, and its deepest moat. Write it in the brand's own voice.

## What is on brand (scores high)
Six bullets. Concrete craft moves, not adjectives. "Macro detail of the made object"
discriminates; "high quality production" does not.

## What is off brand (scores low)
Six bullets. Moves that would win in the feed but cost the brand something. Say what.

## Adjacent, use selectively (mid score)
Three or four bullets, each naming the condition under which it works.

## Scoring rubric: brand_fit_score (0.0 to 1.0)
A markdown table with the four bands (0.85 to 1.0 core truth, 0.55 to 0.84 compatible
with care, 0.35 to 0.54 tension, 0.0 to 0.34 off brand), each with real examples for
THIS brand. Then five numbered heuristics that actually discriminate for this brand.
The last one is a cap: name the thing that, if present, caps the score at 0.34.

## Brand voice rule
Never use a hyphen or an em dash as a generic connector in any string that renders."""


def derive_brand(query: str, site_text: str = "") -> Dict[str, Any]:
    """
    Propose a full config plus a draft brief from a brand name or URL.

    Raises DeriveError with a readable message when the key is missing or the
    model returns something unusable.
    """
    if not ANTHROPIC_API_KEY:
        raise DeriveError(
            "ANTHROPIC_API_KEY is not set, so nothing can be derived. "
            "Fill the fields in by hand, or add the key to .env."
        )
    query = query.strip()
    if not query:
        raise DeriveError("Give me a brand name or a URL.")

    try:
        import anthropic
    except ImportError:
        raise DeriveError("The anthropic package is not installed: pip3 install anthropic")

    if not site_text and looks_like_url(query):
        site_text = fetch_site(query)

    source = (
        f"\n\nThe brand's own site says:\n---\n{site_text}\n---\n"
        if site_text else
        "\n\nNo site text was available, so work from what you know about the brand.\n"
    )

    setup_prompt = f"""You are setting up a competitive TikTok analysis for this brand: {query}
{source}
Propose the analysis setup.

Pick two competitors: the closest challenger and the category anchor. They must be
brands that actually post on TikTok.

For every handle, give the real TikTok account if you know it. If you are not sure,
still give your best guess but set handle_confidence to low, because the caller
verifies these against TikTok before spending anything.

For every hashtag, avoid bare brand words that are a different subject on TikTok
before they are a brand: a place, a band, a common noun. Prefer a specific compound
when the bare word is ambiguous.

{_SCHEMA_NOTE}"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def call(prompt: str, max_tokens: int) -> str:
        try:
            msg = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:                           # noqa: BLE001
            raise DeriveError(f"The model call failed: {exc}")
        if getattr(msg, "stop_reason", "") == "max_tokens":
            raise DeriveError(
                "The model ran out of room before finishing. Try again, or fill "
                "the form in by hand."
            )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()

    # The setup is small and structured, so it goes as JSON. The brief is long
    # prose and comes back as plain markdown: embedding it in JSON meant one
    # truncated response destroyed the whole proposal, and every newline and
    # quote in the brief was another chance to produce unparseable output.
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", call(setup_prompt, 1500))
    try:
        data = json.loads(raw)
    except ValueError:
        raise DeriveError("The model did not return usable JSON. Try again, or fill the form in by hand.")

    for field in ("name", "handle"):
        if not str(data.get(field, "")).strip():
            raise DeriveError(f"The proposal came back without a {field}. Fill the form in by hand.")

    brief_prompt = f"""Write the brand brief for {data.get("name", query)}.
{source}
{_BRIEF_SHAPE}

Return ONLY the markdown. No preamble, no code fences."""
    data["brief"] = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", call(brief_prompt, 8000))
    if len(data["brief"].strip()) < 400:
        raise DeriveError("The drafted brief came back too short to be usable.")

    data["key"] = re.sub(r"[^a-z0-9]", "", str(data.get("key") or data["name"]).lower())
    data["handle"] = _norm_handle(data["handle"])
    data["hashtag"] = re.sub(r"[^a-z0-9]", "", str(data.get("hashtag") or data["key"]).lower())
    out_comps = []
    for c in data.get("competitors") or []:
        if not str(c.get("name", "")).strip():
            continue
        out_comps.append({
            "name": c["name"],
            "key": re.sub(r"[^a-z0-9]", "", str(c.get("key") or c["name"]).lower()),
            "handle": _norm_handle(c.get("handle", "")),
            "hashtag": re.sub(r"[^a-z0-9]", "", str(c.get("hashtag") or c.get("name", "")).lower()),
        })
    data["competitors"] = out_comps
    data["site_text_used"] = bool(site_text)
    return data


def _norm_handle(h: str) -> str:
    h = str(h or "").strip()
    return h if h.startswith("@") else ("@" + h if h else "")


# ── Verification, the part that must not be skipped ──────────────────────────

def search_handle(brand_name: str, timeout: int = 90) -> Optional[Dict[str, Any]]:
    """
    Find a brand's real TikTok account by searching, rather than recalling it.

    A model's memory of handles is unreliable: across two runs on the same brand
    it produced two different handles and neither was the official account. The
    platform knows the answer, so ask it. Ranks candidates by verification first
    and audience second, and returns None when nothing convincing turns up.
    """
    if not APIFY_API_KEY:
        return None
    url = (
        f"https://api.apify.com/v2/acts/{ACTOR_VIDEOS}/run-sync-get-dataset-items"
        f"?token={APIFY_API_KEY}"
    )
    try:
        r = requests.post(url, json={
            "searchQueries": [brand_name],
            "resultsPerPage": 20,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
        }, timeout=timeout)
        r.raise_for_status()
        items = r.json()
    except (requests.RequestException, ValueError):
        return None

    target = re.sub(r"[^a-z0-9]", "", brand_name.lower())
    best: Optional[Dict[str, Any]] = None
    for item in items if isinstance(items, list) else []:
        meta = item.get("authorMeta") or {}
        name = str(meta.get("name", ""))
        if not name:
            continue
        flat = re.sub(r"[^a-z0-9]", "", name.lower())
        # Strict on purpose. A loose substring match returned @raphamohamud for
        # Rapha: a person whose name contains the brand. This search reads video
        # results, so most authors are creators rather than the brand, and a
        # confidently wrong handle is worse than no suggestion at all. Accept the
        # exact name, or the name plus a common official suffix, and nothing else.
        suffixes = ("", "official", "hq", "usa", "us", "uk", "eu", "global", "brand")
        if flat not in {target + s for s in suffixes}:
            continue
        cand = {
            "handle": "@" + name,
            "followers": meta.get("fans") or 0,
            "verified": bool(meta.get("verified")),
        }
        if best is None or (cand["verified"], cand["followers"]) > (best["verified"], best["followers"]):
            best = cand
    # Stay silent unless the find is credible on its own terms.
    if best and not (best["verified"] or best["followers"] >= FOLLOWER_FLOOR):
        return None
    return best


def probe_hashtags(tags: List[str], timeout: int = 90) -> Dict[str, Dict[str, Any]]:
    """
    Ask TikTok whether each hashtag actually carries content, before the real
    scrape spends anything on it.

    One actor run for all tags, two results per tag requested. A returned video
    is attributed to a tag when its own hashtag list carries it, which hashtag
    search results almost always do. The check exists because a real run
    scraped an empty hashtag pool with a green checkmark: the subject had no
    owner content in the corpus and the headline insight was an artifact.

    Returns {tag: {"state": "alive"|"weak"|"dead", "sample": int, "views": int|None}}.
    "alive" means the tag page exists with a view counter; "weak" means the
    search returned items but no tag page, which is the fuzzy fallback firing
    (a nonsense tag also gets one of those), so treat it as probably wrong;
    "dead" means nothing came back. Raises DeriveError only when Apify is
    unreachable: an empty result IS the answer.
    """
    tags = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in tags if t and t.strip()]
    tags = [t for t in tags if t]
    if not tags:
        return {}
    if not APIFY_API_KEY:
        raise DeriveError("APIFY_API_KEY is not set, so hashtags cannot be probed.")
    url = (
        f"https://api.apify.com/v2/acts/{ACTOR_VIDEOS}/run-sync-get-dataset-items"
        f"?token={APIFY_API_KEY}"
    )
    try:
        r = requests.post(url, json={
            "hashtags": tags,
            "resultsPerPage": 2,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
        }, timeout=timeout)
        r.raise_for_status()
        items = r.json()
    except requests.RequestException as exc:
        raise DeriveError(f"Could not reach Apify to probe the hashtags: {exc}")
    except ValueError:
        raise DeriveError("Apify returned something unreadable while probing hashtags.")

    # Attribution is exact: every item carries the searched term in `input`,
    # and `searchHashtag` adds the tag's total view count. The first version
    # matched against each video's own hashtag list, which fails whenever the
    # video does not literally tag the searched compound.
    out: Dict[str, Dict[str, Any]] = {t: {"alive": False, "sample": 0, "views": None} for t in tags}
    for item in items if isinstance(items, list) else []:
        searched = re.sub(r"[^a-z0-9]", "", str(item.get("input", "")).lower())
        if searched not in out:
            sh = item.get("searchHashtag") or {}
            searched = re.sub(r"[^a-z0-9]", "", str(sh.get("name", "")).lower())
        if searched in out:
            out[searched]["alive"] = True
            out[searched]["sample"] += 1
            sh = item.get("searchHashtag") or {}
            if isinstance(sh.get("views"), (int, float)):
                out[searched]["views"] = int(sh["views"])
    for tag, r in out.items():
        if r["views"] is not None:
            r["state"] = "alive"
        elif r["sample"] > 0:
            r["state"] = "weak"
        else:
            r["state"] = "dead"
        r.pop("alive", None)
    return out


def verify_accounts(accounts: List[Tuple[str, str]], timeout: int = 90) -> Dict[str, Any]:
    """
    Confirm each TikTok handle exists, by asking Apify for one video per profile.

    accounts: list of (label, handle). Returns {label: {"exists": bool, "handle": str,
    "followers": int|None, "note": str}}.

    One video per account is the cheapest question that distinguishes a real
    account from a plausible invention.
    """
    if not APIFY_API_KEY:
        raise DeriveError("APIFY_API_KEY is not set, so handles cannot be verified.")
    if not accounts:
        return {}

    profiles = [h.lstrip("@") for _, h in accounts if h]
    url = (
        f"https://api.apify.com/v2/acts/{ACTOR_VIDEOS}/run-sync-get-dataset-items"
        f"?token={APIFY_API_KEY}"
    )
    payload = {
        "profiles": profiles,
        "resultsPerPage": 1,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadSubtitles": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        items = r.json()
    except requests.RequestException as exc:
        raise DeriveError(f"Could not reach Apify to verify the handles: {exc}")
    except ValueError:
        raise DeriveError("Apify returned something unreadable while verifying handles.")

    seen: Dict[str, Dict[str, Any]] = {}
    for item in items if isinstance(items, list) else []:
        meta = item.get("authorMeta") or {}
        name = str(meta.get("name", "")).lower()
        if name:
            seen[name] = {
                "followers": meta.get("fans"),
                "verified": bool(meta.get("verified")),
            }

    out: Dict[str, Any] = {}
    for label, handle in accounts:
        h = handle.lstrip("@").lower()
        hit = seen.get(h)
        if not hit:
            out[label] = {
                "handle": handle, "exists": False, "ok": False,
                "followers": None, "verified": False,
                "note": "nothing came back for this handle, so it is wrong",
            }
            continue

        followers = hit.get("followers") or 0
        verified = hit.get("verified", False)
        # Existence alone is a weak test. Handles get squatted, and an abandoned
        # account with a few hundred followers scrapes cleanly while telling you
        # nothing about the brand. A real brand account is verified, or it has
        # an audience. Neither is proof, but the pair catches the common miss.
        ok = verified or followers >= FOLLOWER_FLOOR
        if ok:
            note = ""
        elif followers < 1000:
            note = (
                "exists but has only {:,} followers, which is not a brand account "
                "for a company of any size. Probably squatted or abandoned."
            ).format(followers)
        else:
            note = (
                "exists with {:,} followers and no verification badge. Check it is "
                "the official account and not a regional or fan one."
            ).format(followers)

        out[label] = {
            "handle": handle, "exists": True, "ok": ok,
            "followers": followers, "verified": verified, "note": note,
        }
    return out


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python3 derive.py \"<brand name or url>\"")
    query = " ".join(sys.argv[1:])
    print(f"Deriving from: {query}")
    proposal = derive_brand(query)
    print(f"\nsubject   : {proposal['name']}  {proposal['handle']}  #{proposal['hashtag']}")
    print(f"confidence: {proposal.get('handle_confidence', 'unknown')}")
    print(f"site read : {'yes' if proposal['site_text_used'] else 'no'}")
    for c in proposal["competitors"]:
        print(f"competitor: {c['name']}  {c['handle']}  #{c['hashtag']}")
    print(f"brief     : {len(proposal['brief'])} characters\n")

    accounts = [(proposal["key"], proposal["handle"])]
    accounts += [(c["key"], c["handle"]) for c in proposal["competitors"]]
    print("Verifying the handles against TikTok...")
    bad = 0
    for label, res in verify_accounts(accounts).items():
        if res["ok"]:
            mark = "ok     "
        elif res["exists"]:
            mark = "SUSPECT"
            bad += 1
        else:
            mark = "WRONG  "
            bad += 1
        fol = f"{res['followers']:,} followers" if res.get("followers") else ""
        badge = " verified" if res.get("verified") else ""
        print(f"  {mark} {label:12} {res['handle']:22} {fol}{badge}")
        if res["note"]:
            print(f"          {res['note']}")
    if bad:
        print(f"\n{bad} handle(s) need a look before spending anything on a scrape.")


if __name__ == "__main__":
    main()
