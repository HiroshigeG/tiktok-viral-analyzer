# Lexus Brand Brief (for brand-fit scoring)

> One page. The contract `analyze.py` scores every winning category pattern against, to produce
> `brand_fit_score` (0..1) in `signal.json -> insight.brand_gap[]`. Sourced from the brand's
> public positioning and SPEC §7. Brand voice rule applies to any rationale that renders.

## The essence

Lexus is **The Standard of Amazing**. Japanese luxury. The brand truth is **takumi craft**: the
made object, finished by a master's hand, the hours you can feel. The **spindle grille** is the
unmistakable signature. The register is **emotion over spec**: name a feeling, never recite horsepower.
The deepest, uncopyable moat is **owner loyalty**, drivers on their third, fourth, fifth Lexus.

Tone: calm, confident, intimate, premium. Restraint reads as luxury. The salesperson voice destroys it.

## What is on brand (scores high)

- Macro craft, the made object in extreme closeup: stitching, leather grain, the spindle grille, a turned dial.
- Owner and real cabin point of view, handheld honesty, no narration.
- Ambient sound design and quiet scored beds. Sound that does the selling without a word.
- One held shot, confidence in stillness, a single clean reveal.
- Emotion, memory, loyalty, family, the long relationship with the car.
- Quiet typographic restraint, one short line that names a feeling.

## What is off brand (scores low)

- Announcer voiceover, hard sell, "available now at your dealer".
- Spec sheets and stacked numbers on screen.
- Frantic montage, a cut every second, hype edit energy.
- Loud meme formats, broad comedy, anything that cheapens the object.
- Logo first beauty shots that show everything at once.

## Adjacent, use selectively (mid score)

- Trending audio: fits when the bed is calm, fights the register when it is a loud beat drop.
- Creator collaborations: strong when the creator respects the craft, weak when it is a stunt.
- Spectacle and tech overlays: on brand only when precision reads as art, never as a gimmick.

## Scoring rubric: brand_fit_score (0.0 to 1.0)

Score how on brand a **category winning trait** is for Lexus. Start at a baseline and adjust.

| Band | Meaning | Examples |
|---|---|---|
| 0.85 to 1.0 | Core brand truth. Lexus should own this outright. | macro craft hook, owner point of view, ambient sound, emotion over spec |
| 0.55 to 0.84 | Compatible with care. On brand if executed in the Lexus register. | calm trending audio, respectful creator work, a single clean reveal |
| 0.35 to 0.54 | Tension. Works in the category but pulls against the register. Use selectively. | loud beat drop audio, fast trending transitions |
| 0.0 to 0.34 | Off brand. Wins in the feed but would cheapen Lexus. Recommend avoiding. | spec sheets on screen, announcer voiceover, hype montage, broad comedy |

Heuristics to combine into the score:
1. **Craft alignment.** Does the trait foreground a made object or human craft? (+)
2. **Register alignment.** Calm, confident, restrained? (+) Loud, hard sell, frantic? (minus)
3. **Emotion over spec.** Names a feeling? (+) Lists numbers? (minus)
4. **Loyalty and ownership.** Centers the owner or the long relationship? (+)
5. **Dignity.** Would it ever cheapen the object or the badge? If yes, cap at 0.34.

Return a float rounded to two decimals plus a one sentence `rationale`. Keep the rationale in the
brand voice (no hyphen or em dash as a generic connector).

## How this feeds the demo

`brand_gap[]` pairs each high engagement category trait with what Lexus US does today and the
`brand_fit_score`. The story the pitch tells: the feed already rewards what Lexus is best at
(high fit traits Lexus is underusing), and punishes the commercial reflex Lexus still leads with.
That gap, scored, is the insight native analytics cannot show.
