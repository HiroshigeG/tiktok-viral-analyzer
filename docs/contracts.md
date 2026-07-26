# Intermediate Data Contracts

> The final contract is `docs/signal.schema.json`. This file defines the two **intermediate** formats
> that connect the three Python modules so they build in parallel without guessing. The lead owns these.
> Propose edits to the lead; coordinate edges peer to peer (ingestion <-> analysis, analysis <-> signal).

## Data flow

```
ingest.py   ->  data/raw/<brand>.json   (+ data/raw/index.json, cover images in data/raw/thumbs/)
analyze.py  ->  data/analysis.json       (reads data/raw/*, runs Gemini then Claude)
signal.py   ->  signal.json              (reads data/raw/* + data/analysis.json, aggregates to the schema)
lead        ->  copies signal.json + needed thumbs into web/, deploys
```

All three modules use the brand keys `lexus`, `genesis`, `bmw` (lowercase). TikTok profiles are the
official brand accounts (lead will confirm exact handles, e.g. `@lexususa`, `@genesis`, `@bmw`).

## Contract A: ingestion raw record (`data/raw/<brand>.json`)

One file per brand. `ingest.py` scrapes a larger pool per brand, splits into top and bottom performers
by play count (the baseline / control), and scrapes comments per kept video.

```json
{
  "brand": "lexus",
  "profile": "@lexususa",
  "scraped_at": "ISO-8601",
  "pool_size": 40,
  "videos": [
    {
      "id": "string, stable video id",
      "brand": "lexus",
      "url": "webVideoUrl",
      "thumb": "data/raw/thumbs/<id>.jpg or remote cover url or \"\"",
      "local_video_path": "data/videos/<id>.mp4 or \"\" if not downloaded",
      "text": "caption",
      "created_at": "ISO-8601 or unix epoch",
      "author": {
        "name": "@handle",
        "verified": true,
        "is_brand_account": true
      },
      "metrics": {
        "views": 0, "likes": 0, "comments": 0, "shares": 0,
        "engagement_rate": 0.0
      },
      "performance_tier": "top | bottom",
      "source": "profile | hashtag",
      "signals": {
        "sound": { "id": "", "title": "", "author": "", "is_original": false },
        "hashtags": ["string"],
        "duration_sec": 0
      },
      "comments": [
        { "text": "verbatim comment", "likes": 0, "author": "@handle" }
      ]
    }
  ]
}
```

Notes for ingestion:
- Reuse `analyzer.py TikTokAnalyzer.scrape_videos` run/poll/dataset pattern for the Apify call.
- `engagement_rate` = (likes + comments + shares) / views * 100, rounded to 2 (same formula as analyzer.py).
- Each brand's data is a COMBINED corpus: brand-profile videos (`source: "profile"`) plus hashtag videos (`source: "hashtag"`, owner and creator content). Dedup by id, prefer the profile copy.
- `performance_tier`: rank the combined corpus by `engagement_rate` (not raw views), considering only videos with `views >= 1000` for `top` (a floor that cuts micro-video noise); below the floor default to `bottom`. Top third = `top`, bottom third = `bottom`, drop the middle, balance counts across brands. Rationale: mixing brand reach content with owner engagement content means views just track follower count; engagement_rate is the resonance signal, and it yields the real winners vs losers craft baseline (owner craft tends to win, brand commercial style tends to lose).
- `is_brand_account`: true only when the author handle equals the official brand profile, false for owners and creators (including brand-adjacent posts found under the hashtag). This drives the owners vs brand insight downstream.
- `source`: `"profile"` for videos from the official brand account, `"hashtag"` for videos found under the brand hashtag.
- Download the video file at ingest (set `shouldDownloadVideos`), save to `data/videos/<id>.mp4`, and set `local_video_path`. This lets analysis run Gemini on the local file without a second scrape (the scraper only exposes downloaded files in that same run's key value store).
- Download cover images to `data/raw/thumbs/<id>.jpg` when available (set `shouldDownloadCovers`). Set `thumb` to that relative path. Remote cover urls expire, so prefer the downloaded file.
- Comments: use a TikTok comments Apify actor (e.g. `clockworks~tiktok-comments-scraper`) per video url, cap at a sane number per video (e.g. 30). Same run/poll/dataset pattern.
- Also write `data/raw/index.json`: a flat list of `{id, brand, performance_tier, is_brand_account, views, engagement_rate, url, thumb}` across all brands, for quick downstream loading.

## Contract B: analysis record (`data/analysis.json`)

`analyze.py` reads `data/raw/<brand>.json`, runs Gemini per video (reuse
`analyzer.py TikTokAnalyzer.analyze_video_with_gemini`, map its output into `craft`), then adds the
Claude strategic layer, the brand-fit score (per `docs/brand-brief.md`), and a brief seed.

```json
{
  "generated_at": "ISO-8601",
  "claude_available": true,
  "videos": [
    {
      "id": "string",
      "brand": "lexus",
      "performance_tier": "top | bottom",
      "who": "owner | brand | creator",
      "url": "webVideoUrl",
      "thumb": "assets/thumbs/<id>.jpg",
      "metrics": { "views": 0, "likes": 0, "comments": 0, "shares": 0, "engagement_rate": 0.0 },
      "hook_type": "string",
      "craft": {
        "hook": "string", "structure": "string", "pacing": "string",
        "audio": "string", "text_overlay": "string", "why": ["string"]
      },
      "strategy": {
        "cultural_trend": "string",
        "brand_replication_path": "string",
        "risk": "string"
      },
      "brand_fit": { "score": 0.0, "rationale": "string" },
      "brief_seed": { "concept": "string", "hook_formula": "string" },
      "source": "gemini+claude | gemini+authored"
    }
  ]
}
```

Notes for analysis:
- Map Gemini fields into `craft` and `hook_type`:
  - `VISUAL_HOOK.hook_type` -> `hook_type`; `VISUAL_HOOK.description` -> `craft.hook`
  - `NARRATIVE_STRUCTURE.format` + `.emotional_arc` -> `craft.structure`; `.pacing` -> `craft.pacing`
  - `AUDIO_STRATEGY` -> `craft.audio` (summarize music/voiceover/trending into one line)
  - `TEXT_OVERLAY` -> `craft.text_overlay`; `WHY_IT_WORKS.reasons_for_virality` -> `craft.why`
- `who`: derive from ingestion `author.is_brand_account` (true -> `brand`) and a light owner vs creator heuristic (verified non brand or follower scale -> `creator`, otherwise `owner`). Document your rule.
- `ANTHROPIC_API_KEY` present -> call Claude for `strategy`, `brand_fit.rationale`, and `brief_seed`; set `source: "gemini+claude"`, top level `claude_available: true`.
- `ANTHROPIC_API_KEY` absent -> author the strategic layer directly from the brief and the craft (deterministic helper), set `source: "gemini+authored"`, `claude_available: false`. The pre-baked demo must still produce a complete, honest file.
- `brand_fit.score`: apply the `docs/brand-brief.md` rubric to the video's dominant trait.

## Contract C: signal aggregation (`signal.py` -> `signal.json`)

`signal.py` reads `data/raw/*` (for comments and metrics) and `data/analysis.json` (for craft, strategy,
fit), then produces `signal.json` validated against `docs/signal.schema.json`.

- `segnale.winning_patterns`: cluster recurring craft traits among `top` tier videos, count frequency, average engagement, list `evidence_video_ids`.
- `segnale.baseline_delta`: per dimension (hook_type, pacing, audio, who, text_overlay), summarize what `top` does vs what `bottom` does, with a `delta_note`.
- Virality signals (from Contract A `signals`): use the real `signals.sound` to strengthen the `audio` baseline_delta row (trending or original sound usage in top vs bottom) and, where it earns it, add a trending-sound `winning_pattern`. Optionally surface the most common `signals.hashtags` among top performers. These flow into the existing free-form `winning_patterns` and `baseline_delta` strings, so no schema change is needed.
- `segnale.top_videos`: the highest engagement videos, carrying `who`, `hook_type`, `craft` from analysis and `thumb` as a relative `assets/thumbs/<id>.jpg` path.
- `insight.owners_vs_brand`: share of engagement from `who != brand` vs `who == brand`, with evidence.
- `insight.brand_gap`: pair high engagement category traits with `brand_today` and the `brand_fit_score` from analysis.
- `insight.audience_voice`: cluster the real comments from `data/raw/*` into themes with sentiment, volume, and verbatim `sample_comments`.
- `direzione.brief` and `next_moves`: synthesize from the highest brand-fit `brief_seed`s and the gap, tying to `The Live Standard` and `Owner Films`.
- `meta.claude_available` mirrors the analysis flag. `meta.method_note` keeps the honesty framing.
- Run the produced file through `jsonschema` against `docs/signal.schema.json` in a test. That test is the definition of the contract holding.

## Thumbs for the static site

The frontend references `assets/thumbs/<id>.jpg` (relative to `web/`). Ingestion downloads covers to
`data/raw/thumbs/<id>.jpg` and sets `thumb` to that raw path. Analysis forwards that raw path verbatim.
**`signal.py` owns the normalization**: it rewrites `thumb` to the web relative `assets/thumbs/<id>.jpg`
in the final `signal.json`. At integration the lead copies the thumbs that `signal.json` references into
`web/assets/thumbs/`. Until then the frontend uses `docs/signal.example.json` thumbs paths and shows a
graceful dark placeholder when an image is missing.

## Environment and running

- All Python modules: `from analyzer import TikTokAnalyzer` (vendored at repo root). Load secrets with
  `python-dotenv` from the repo root `.env` (`APIFY_API_KEY`, `GEMINI_API_KEY`, optional `ANTHROPIC_API_KEY`).
- During the parallel build, mock external calls in tests. Do not spend live API budget. The lead runs the
  real pipeline once in the main tree where `.env` lives.
- Python 3.9 on this machine. Keep type hints 3.9 compatible (use `Optional[...]`, `List[...]`, `Dict[...]`).
