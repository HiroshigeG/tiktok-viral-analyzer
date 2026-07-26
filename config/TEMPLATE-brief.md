# [Brand] Brand Brief (for brand-fit scoring)

> One page. This is the contract `analyze.py` scores every winning category pattern
> against, to produce `brand_fit_score` (0..1) in `signal.json -> insight.brand_gap[]`.
> Nothing else in the pipeline knows what this brand is. If you leave this file
> describing someone else's brand, every score comes out authoritative and meaningless.
>
> Source it from the brand's real positioning: the brand book, the campaign platform,
> the deck. Do not invent one. If you do not have it, ask for it before running.
>
> `config/lexus-brief.md` is a filled-in example of this shape.

## The essence

Three or four sentences. What is the brand's core truth, the thing it owns that no
competitor can claim? Name the register (calm, loud, irreverent, authoritative) and the
deepest moat. Write it the way the brand would speak, because the model reads this as
voice as well as fact.

## What is on brand (scores high)

Six or so bullets. Concrete craft moves, not adjectives. "Macro detail of the made
object, stitching and grain" beats "high quality production". These are the traits that
should land in the 0.85 to 1.0 band.

- ...
- ...

## What is off brand (scores low)

Six or so bullets. The moves that would win in the feed but cost the brand something.
Be specific about what they cost: dignity, credibility, price positioning.

- ...
- ...

## Adjacent, use selectively (mid score)

Three or four bullets. Traits that work under conditions. Say the condition, because
that is what separates a 0.7 from a 0.4.

- ...

## Scoring rubric: brand_fit_score (0.0 to 1.0)

Score how on brand a **category winning trait** is for this brand.

| Band | Meaning | Examples |
|---|---|---|
| 0.85 to 1.0 | Core brand truth. The brand should own this outright. | ... |
| 0.55 to 0.84 | Compatible with care. On brand if executed in the brand's register. | ... |
| 0.35 to 0.54 | Tension. Works in the category but pulls against the register. | ... |
| 0.0 to 0.34 | Off brand. Wins in the feed but would cost the brand. | ... |

Heuristics to combine into the score. Replace these with the ones that actually
discriminate for this brand; the five below are a starting shape, not a law.

1. **Craft alignment.** Does the trait foreground what the brand is good at? (+)
2. **Register alignment.** Does it match how the brand speaks? (+) Fight it? (minus)
3. **Substance over noise.** Does it say something, or just attract? (+/minus)
4. **Relationship.** Does it centre the customer and the long relationship? (+)
5. **Dignity.** Would it ever cheapen the brand? If yes, cap at 0.34.

Return a float rounded to two decimals plus a one sentence `rationale` in the brand voice.

## How this feeds the analysis

`brand_gap[]` pairs each high engagement category trait with what the brand does today,
derived from its own scraped posts, and the `brand_fit_score` from this rubric. The story
the output tells: the feed already rewards things the brand is good at and underuses, and
punishes the reflex the brand still leads with. That gap, scored, is the insight native
analytics cannot show.

## Brand voice rule

Never use a hyphen or an em dash as a generic connector in any string that renders.
Compound modifiers such as "top performing" are fine. This is the fastest tell of machine
writing and it costs credibility.
