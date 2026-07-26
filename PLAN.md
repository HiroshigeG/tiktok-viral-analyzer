# SIGNAL (lexus-signal) — Implementation Plan

> Lead orchestration plan. Source of truth for scope is `SPEC.md`. Contracts live in `docs/`.
> Status: contracts frozen, ready to dispatch the 4 module teammates.

## 1. What we are shipping

A pre-baked, static, Vercel-deployable pitch demo. An offline Python pipeline runs once on real
TikTok data (Lexus + Genesis + BMW, top **and** bottom performers + comments), produces `signal.json`,
and a dark-cinematic static site renders a three-act narrative scroll: **01 SEGNALE → 02 INSIGHT → 03 DIREZIONE**.

MVP is a pitch weapon: wow, demoable, 100 percent real data, on brand with the house design language.
Out of scope: auth, multi-client, live scraping, cross-platform, scheduled reports, a backend.

## 2. Architecture (reuse the proven core, add modules)

```
OFFLINE PIPELINE (python, run once)            STATIC FRONTEND (web, vercel)
ingest.py   scrape 3 brands, top+bottom         web/index.html
            + comments  -> data/raw/            reads ./signal.json
analyze.py  Gemini craft -> Claude strategy      renders the 3 acts
            -> brand fit -> brief seed           on brand with the house design language
            -> data/analysis.json                deploy: vercel --prod
signal.py   baseline delta + comment clusters
            + brand gap + synthesis -> signal.json
```

Reuse from the vendored `analyzer.py` (copied into this repo): `TikTokAnalyzer.scrape_videos`
(Apify) and `TikTokAnalyzer.analyze_video_with_gemini` (Gemini vision). Wrap, do not rewrite.

## 3. The contracts (frozen before any code)

All four modules build against these. The lead owns them. Change requests go to the lead.

- `docs/signal.schema.json` — JSON Schema for the final `signal.json` (the one true interface).
- `docs/signal.example.json` — a realistic, schema-valid mock. Frontend builds against this until the real one lands.
- `docs/contracts.md` — the intermediate data contracts (ingestion raw records, analysis records) and the data flow that connects the three Python modules.
- `docs/brand-brief.md` — the one-page Lexus brand brief plus the brand-fit scoring rubric (0..1) for `analyze.py`.

## 4. Modules and owners (one teammate each)

| Module | Owns | Builds | Reuses |
|---|---|---|---|
| ingestion | `ingest.py`, `data/raw/` | scrape 3 brands top+bottom + comments, write raw records | `analyzer.py` Apify code |
| analysis | `analyze.py`, `data/analysis.json` | per video: Gemini craft -> Claude strategy + brand-fit + brief seed | `analyzer.py` Gemini code + `anthropic` SDK |
| signal | `signal.py`, `signal.json` | aggregate, baseline delta, comment clusters, brand gap, synthesize to schema | the contracts |
| frontend | `web/` | static dark-cinematic 3-act scroll reading `signal.json` | house design tokens |

## 5. Build order and coordination

1. **Schema first (done).** Contracts in `docs/` are frozen.
2. **Plan-approval gate.** Each teammate returns a brief plan. The lead approves before any code.
3. **Four in parallel.** Each teammate works in its own git worktree against the contracts.
   - ingestion + analysis: build + light unit tests on transforms, mock external calls (no live API spend during build).
   - signal: build against `docs/signal.example.json` and the intermediate contracts.
   - frontend: build against a local copy of `docs/signal.example.json` as `web/signal.json`.
4. **Signal integrates last** on the real ingestion + analysis outputs.
5. **Lead runs the real pipeline** in the main tree (where `.env` lives): `ingest.py` -> `analyze.py` -> `signal.py`.
6. **Render + deploy.** Copy real `signal.json` and thumbs into `web/`, Playwright screenshot check (desktop + mobile), `vercel --prod`.

Peer messaging: teammates coordinate contract edges directly (ingestion <-> analysis on raw record shape,
analysis <-> signal on analysis record shape, signal <-> frontend on signal.json field semantics).

## 6. Environment

- `.env` (gitignored) holds `APIFY_API_KEY`, `GEMINI_API_KEY`. `ANTHROPIC_API_KEY` may be absent.
- If `ANTHROPIC_API_KEY` is missing, `analyze.py` produces the strategic layer directly for this pre-baked run and flags it in the output (`source: "gemini+authored"`, `claude_available: false`). A real key makes the pipeline repeatable.
- Worktrees do not inherit the gitignored `.env`. The real API run happens in the main tree, run by the lead. Teammates mock external calls in their tests.

## 7. Testing (per SPEC §11)

- `signal.py`: schema validation test of the produced `signal.json` against `docs/signal.schema.json` (the critical contract).
- `ingest.py` / `analyze.py`: light unit tests on the transform functions (parsing, top/bottom baseline split, Gemini-to-craft mapping, brand-fit scoring).
- Frontend: Playwright screenshot validation, desktop + mobile, served over http (not file://).

## 8. Brand voice rule (non negotiable, all user-facing copy)

Never use a hyphen or em dash as a generic connector in any text that renders on the site
(`signal.json` strings, HTML copy). Compound modifiers (AI-powered, top-performing) are fine.
This is the single fastest tell of machine writing and it burns credibility in the room.

## 9. Definition of done

A Vercel URL: dark-cinematic three-act scroll on real Lexus + Genesis + BMW data, with the baseline delta,
the owners-vs-brand insight, real audience-voice comments, and a generated creative brief tying to
The Live Standard and Owner Films. Demoable and sendable. Honest method note present.
