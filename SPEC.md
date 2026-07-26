# SIGNAL — Social Intelligence Pitch Demo (Lexus instance)

> **Status:** design approved (brainstorm 29 May 2026). Build via Claude Code agent-team (tmux) in iTerm2.
> **Working name:** "Signal" (product) · `lexus-signal` (this demo instance). Renameable.
> **Owner:** HiroshigeG. Built as an agency pitch demo + portfolio positioning.

---

## 1. What this is (one paragraph)

A **pre-baked, static, deployable web demo** that proves a single thesis: *AI can decode the creative DNA of any public social content — the part a brand's own native analytics structurally cannot see — and turn it into creative direction.* A Python pipeline runs **offline** on **real** TikTok data (Lexus + 2 competitors, top *and* bottom performers), produces a `signal.json`, and a **dark-cinematic static site** renders a narrative scroll: **01 SEGNALE → 02 INSIGHT → 03 DIREZIONE**. It is the "sensing half" of a larger sense and make system, made real.

## 2. Goal / success criteria (MVP = "pitch weapon")

- **WOW + demoable + 100% real data.** A creative-innovation lead sees it and gets it in 60 seconds.
- Deployed as a **Vercel URL** that can be sent or screen-shared. Zero live-scrape risk in the room.
- **Credible**, not anecdote: real baseline (winners vs losers), brand-fit scored, audience voice from real comments, dual-model rigor.
- **On-brand**: identical design language to the internal design reference (dark cinematic).
- Lands on **action** (a creative brief), not numbers.

**NON-goals (YAGNI — explicitly out of MVP):** auth/login, multi-client management, live/on-demand scraping, cross-platform (TikTok only), scheduled reports, a backend/server. These are the documented "phase 2 / product" roadmap, not now.

## 3. Architecture (Approach ① — reuse the proven core, add modules)

```
OFFLINE PIPELINE (Python, run once)              STATIC FRONTEND (web, Vercel)
─────────────────────────────────               ──────────────────────────────
ingest.py   scrape Lexus + Genesis + BMW         web/index.html
            TOP + BOTTOM performers              cinematic narrative scroll
            + comments            ─┐             reads signal.json
                                   │             01 SEGNALE → 02 INSIGHT → 03 DIREZIONE
analyze.py  Gemini (visual craft)  ├─► signal.json ──►  dark cinematic, on-brand
            → Claude (strategy +   │             (house design language)
              brand-fit + brief)   │             deploy: vercel --prod
signal.py   baseline delta +      ─┘
            comments NLP + gap + synthesis
```

Reuse from `~/Desktop/AI stuff/TikTok-Viral-Analyzer/analyzer.py`: the **Apify scrape** code (`scrape_videos`) and the **Gemini vision** call (`analyze_video_with_gemini`). They work. Do not rewrite them; wrap them in the new modules.

## 4. Modules + ownership (one teammate each)

| Module | File(s) | Does | Reuses |
|---|---|---|---|
| **Ingestion** | `ingest.py` | Scrape N videos per brand (Lexus + Genesis + BMW), **top AND bottom performers** (the baseline/control), + scrape **comments** per video. Save raw to `data/raw/`. | analyzer.py Apify code |
| **Analysis** | `analyze.py` | Per video: **Gemini** decodes visual craft (hook, structure, audio, text, why-it-works) → **Claude** adds strategic layer (cultural trend, brand-replication path, risk) + **brand-fit score** vs the Lexus brief + a **creative-brief seed**. | analyzer.py Gemini code + `anthropic` SDK |
| **Signal** | `signal.py` | Aggregate across videos → **winners-vs-losers baseline delta** (what winners do that losers don't), **comment clusters** (audience voice + sentiment), **brand gap** (category-winning traits vs Lexus's own posts), synthesize → `signal.json` to the schema. | — |
| **Frontend** | `web/index.html` (+ assets) | Static dark-cinematic narrative scroll reading `signal.json`. Renders the 3 acts. On-brand with the house design language. Vercel-ready. | house design tokens |

## 5. The contract: `signal.json` schema (lead defines FIRST)

This is the interface every module builds against. Define it before spawning, so all 4 teammates parallelize against it (frontend uses a mock `signal.json` until the real one lands).

```json
{
  "meta": {
    "generated_at": "ISO-8601",
    "brands": ["lexus", "genesis", "bmw"],
    "n_videos": 90,
    "method_note": "Public metrics scraped (real). Craft + strategy = dual-model inference (Gemini + Claude), labeled as such."
  },
  "segnale": {
    "winning_patterns": [
      { "pattern": "string", "frequency": "N/total", "avg_engagement": 0.0, "evidence_video_ids": [] }
    ],
    "baseline_delta": [
      { "dimension": "hook_type|pacing|audio|...", "winners": "value", "losers": "value", "delta_note": "string" }
    ],
    "top_videos": [
      { "id": "", "brand": "", "url": "", "thumb": "", "views": 0, "engagement": 0.0,
        "who": "owner|brand|creator", "hook_type": "", "craft": { "hook": "", "structure": "", "audio": "", "why": [] } }
    ]
  },
  "insight": {
    "owners_vs_brand": { "owner_share_pct": 0, "brand_share_pct": 0, "evidence": [] },
    "brand_gap": [
      { "category_winning_trait": "", "brand_today": "", "gap_note": "", "brand_fit_score": 0.0 }
    ],
    "audience_voice": [
      { "cluster": "", "sentiment": "pos|neg|mixed", "volume": 0, "sample_comments": [] }
    ]
  },
  "direzione": {
    "brief": {
      "title": "", "thesis": "", "concept": "", "format": "", "hook_formula": "",
      "ties_to": ["The Live Standard", "Owner Films"]
    },
    "next_moves": ["string"]
  }
}
```

## 6. Data scope (for the demo)

- **Brands:** Lexus + **Genesis** (closest luxury challenger) + **BMW** (legacy luxury anchor). Adjustable.
- **~20-40 videos/brand**, split top/bottom for the baseline. Total ~60-120.
- Cost: Apify free $5/mo ≈ 50 videos, paid $49 ≈ 500 (fits). Gemini free 1,500/day. Claude API = small.
- **Honest framing in the UI**: a small "method" note — public metrics are real; craft + strategy are dual-model inference, labeled as such. No overclaiming causation beyond the baseline delta.

## 7. Brand brief for fit-scoring (lead writes a 1-pager)

Source from the brand's public positioning. Essence: Lexus = "The Standard of Amazing", Japanese luxury, **takumi craft**, spindle grille, emotion over spec, owner loyalty moat. The brand-fit score = how on-brand a winning category pattern is for Lexus.

## 8. Frontend design (on-brand, non-negotiable)

Design language, dark cinematic:
- Near-black `#0E0D0B`, ivory `#ECE6DA`, cinnabar `#D2451E`, gold `#C8A24B`.
- Fraunces (display) + Hanken Grotesk (body) + JetBrains Mono (labels).
- Filmic grain, slow ambient glow, reveal-on-scroll, scroll progress.
- The three acts as full-bleed cinematic sections. Real video thumbs glow on the dark.
- **Brand voice rule: never use a hyphen or em-dash as a generic connector in user-facing copy.**

## 9. Tech / deps

- Python pipeline: existing deps (`requests`, `python-dotenv`) + **`anthropic`** SDK for the Claude layer.
- `.env`: `APIFY_API_KEY`, `GEMINI_API_KEY` (already exist in TikTok-Viral-Analyzer/.env) + **`ANTHROPIC_API_KEY`** for the Claude strategic layer.
  - ⚠️ **If no `ANTHROPIC_API_KEY`**: for the pre-baked MVP, a Claude teammate can produce the strategic layer directly during the build (human-in-the-loop, one-time). Flag it; a real key makes the pipeline repeatable.
- Frontend: static HTML/CSS/JS (no framework). Deploy `vercel --prod`.

## 10. Agent-team plan (run in iTerm2 / tmux)

**Lead (fixed, the iTerm2 session):**
1. Read this SPEC + `analyzer.py` (reuse) + the design reference.
2. Write a short implementation plan.
3. Define `signal.json` schema + the Lexus brand brief (the contracts).
4. `git init` + a worktree per teammate (modules are file-disjoint, but worktrees avoid any conflict).
5. Spawn 4 teammates, coordinate, integrate `signal.json`, run the real pipeline, deploy Vercel.

**Teammates (Sonnet unless a task needs more):**
- `ingestion` → `ingest.py` (+ `data/raw/`)
- `analysis` → `analyze.py`
- `signal` → `signal.py` (→ `signal.json`); depends on ingestion data + analysis output
- `frontend` → `web/`; builds against the schema with a mock `signal.json` in parallel

**Coordination:** schema-first → 4 parallel against the contract → signal integrates → render → deploy. Peer messaging on contract details. Require a brief plan-approval per teammate before code. Worktrees prevent file conflicts.

## 11. Testing

- `signal.py`: schema validation test on `signal.json` (the critical contract).
- `analyze.py` / `ingest.py`: light unit tests on the transform functions (parsing, baseline split).
- Frontend: Playwright screenshot validation (as the design reference was checked), desktop + mobile.

## 12. Definition of done (MVP)

A Vercel URL showing the dark-cinematic 3-act scroll on **real** Lexus + Genesis + BMW data, with the baseline delta, the owners-vs-brand insight, real audience-voice comments, and a generated creative brief tying to Live Standard + Owner Films. Demoable + sendable. Honest method note present.

## 13. Phase 2 roadmap (NOT now)

Live/on-demand scraping (pick any brand), cross-platform (Reels + Shorts), multi-client dashboard, scheduled weekly reports, multi-model consensus voting, a real backend. The MVP is built so these bolt on later.
