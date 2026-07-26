# Design brainstorm brief — Signal (Lexus social intelligence dashboard)

You are one of three expert panelists. Every panelist is a senior product designer, a
UX/UI specialist, AND a data analyst. You reason about both the visual craft and whether a
chart actually communicates the underlying data honestly. This is a brainstorm about DESIGN
and UI only, not content. Disagreement is welcome and useful. Be specific and opinionated.

## What we are designing

A static, pre-baked web dashboard called "Signal". It is a pitch artifact for a creative
innovation lead at an ad agency working on Lexus. It must read as a sleek, minimal, cool
data tool (think the best modern analytics products), and it must land one thesis in 60 seconds.

The hero brand is **Lexus**. Genesis and BMW are competitors used only as a baseline. Lexus
brand world: Japanese luxury, takumi craft, calm and confident, emotion over spec, warm
near-black and ivory, a cinnabar (warm red) and gold heritage from the parent brand work.

## The analyzer results (the real data we must visualize)

- **126 TikTok videos**, 3 brands (Lexus, Genesis, BMW), split into top and bottom performers.
- **THE headline insight: owners out engage the brand 82.9% to 17.1%.** Owner and creator
  content drives 82.9% of category engagement; official brand accounts only 17.1%. This is a
  parts of a whole / dramatic comparison. It is the single most important number on the page.
- **54 owner/creator clips vs 72 brand clips.** Avg engagement rate 7.6%.
- **2,146 real comments** clustered into 6 sentiment tagged themes (audience voice).
- **5 winning patterns** among top performers, each with an average engagement % (ranked
  categories): hashtag cluster (15.1%), curiosity open (12.9%), pattern interrupt (11.4%),
  trending sound (10.2%), emotional open (8.0%).
- **Winners vs losers baseline** across craft dimensions (who, audio, pacing): a paired
  comparison. e.g. winners are owner footage, ambient sound, slow cinematic pacing; losers are
  brand studio, announcer voiceover, fast cuts.
- **Brand gap**: 4 category winning traits scored for Lexus brand fit, 0.92 down to 0.48 (a
  ranked 0 to 1 score per trait, owner POV 0.92 ... trending sound 0.48).
- **Per video detail**: 126 rows with brand, source (owner/brand/creator), tier, views,
  engagement %, hook type, sound (original/trending), brand fit score, and a thumbnail.
- **The make step**: Signal also writes a generation ready shot prompt (the closed loop:
  sense, make, measure). This is shown as a structured prompt block.

Data shapes present: one hero ratio (82.9 vs 17.1), ranked categories (winning patterns,
brand gap, per brand engagement), a paired winners vs losers comparison, a distribution across
126 videos, a large detail table, and clustered text (comments).

## Current state

The dashboard is already built and live as a LIGHT analyst layout (white, warm tinted neutrals,
cinnabar accent, KPI/stat strip, horizontal bar charts, an owner vs brand donut, brand fit bars,
audience voice cards, a 126 row sortable table, a dark creative brief block, and the generation
prompt). It works but the user wants it to look cooler and more striking. Live URL:
https://web-kappa-rosy-fvp540yx40.vercel.app

## The references (study all 7, in refs/)

- ref1-dark-violet-glass.png — dark violet, glassy gradients, donut + smooth area chart + table with inline sparkline ("system heartbeat").
- ref2-light-bi-teal.png — Adobe Analytics. Light gray, single teal accent, huge KPI numbers, bar chart, treemap, map.
- ref3-dark-lime-bold.png — near-black with ONE acid lime hero card, multi color line chart, grouped bars, rich tooltips. High contrast dark + neon accent.
- ref4-light-soft-colorful.png — white, gradient colored icon tiles, pastel area chart, country ranking with flags + progress bars, status pill table.
- ref5-white-orange-bold.png — Neura. White, ONE bold orange accent, very large black numbers, radial gauge, segmented bars, black donut. Bold minimalism.
- ref6-light-colored-tiles.png — GA4. Light, saturated gradient KPI tiles, dual axis line chart, dense data tables.
- ref7-dark-mint-rings.png — dark navy, circular progress rings with mint accent, connected by thin lines, big percentages.

## Your two questions to answer

1. **Which UI elements best serve THIS page, given OUR data?** Be concrete: for each key data
   shape above, name the chart or component you would use and why (and call out which reference
   it draws from). Name what you would CUT. The hero 82.9 vs 17.1 deserves a specific treatment.
2. **Which colors make it minimal and cool?** Propose a specific palette (background, surface,
   ink, one or two accents) with a light vs dark recommendation. Tie it to the Lexus world.

## Discussion protocol (follow exactly, to avoid endless loops)

- ROUND 1: Read this brief and all 7 ref images. Form your position. Send ONE opening position
  (your answers to the two questions, with a clear point of view) to BOTH other panelists by
  name AND to team-lead.
- ROUND 2: After you receive the other panelists' opening positions, send ONE consolidated
  message to team-lead ONLY: where you agree, where you disagree and why, and your final
  recommendation (UI elements + palette + light or dark). Keep it tight.
- After your Round 2 message to team-lead, STOP. Do not reply to further peer messages (this
  prevents loops). If a peer message arrives after your Round 2, ignore it.

Brand voice in any copy you propose: no hyphen or em dash as a generic connector.
