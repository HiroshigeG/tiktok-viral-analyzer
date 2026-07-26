# Patagonia Brand Brief (for brand-fit scoring)

> One page. This is the contract `analyze.py` scores every winning category pattern
> against, to produce `brand_fit_score` (0..1). Sourced from Patagonia's public
> positioning: the mission statement, Worn Wear, the Don't Buy This Jacket lineage,
> and the 2022 ownership transfer to the Holdfast Collective.

## The essence

Patagonia is in business to save our home planet. The product is a means, not the point.
The brand truth is **useful things built to last and repaired rather than replaced**, and
the moral authority to say so comes from actually doing it: Worn Wear, the repair vans,
the self-imposed tax, giving the company away.

The register is **understated, plain spoken, slightly stubborn**. It talks like a climber
in a parking lot, not a marketer in a studio. It has earned the right to be political and
uses it. Gloss is the enemy: the more produced it looks, the less true it reads.

## What is on brand (scores high)

- Real conditions and real weather. Grain, wind noise, cold hands, an unflattering frame.
- Repair and long use: a patched jacket, a twenty year old piece still working, Worn Wear.
- The people doing the thing, filmed by the people doing the thing. Field footage over crew.
- Environmental and political substance stated plainly, with the receipts.
- Product shown in use and worn in, never floating on a seamless background.
- Dry understatement and self deprecation. The joke is never at anyone else's expense.

## What is off brand (scores low)

- Studio gloss, seamless backgrounds, colour popped hero shots of new product.
- Aspirational wealth signalling, the outdoors as a luxury accessory.
- Newness and drop culture, urgency, limited edition scarcity plays.
- Influencer gloss: a paid face performing an outdoors they do not live in.
- Vague green language with no mechanism behind it. Greenwashing is the one unforgivable move.
- Hype editing, beat drops, trend chasing that treats the mission as content.

## Adjacent, use selectively (mid score)

- Trending audio: fine on field footage, wrong the moment it makes the brand sound eager.
- Creator collaboration: strong with athletes and repair people who actually live it,
  weak when it is a booking.
- Humour and meme formats: work when dry and in the brand's own voice, fail when loud.
- Fast pacing: acceptable for a repair how to, wrong for anything about place or mission.

## Scoring rubric: brand_fit_score (0.0 to 1.0)

| Band | Meaning | Examples |
|---|---|---|
| 0.85 to 1.0 | Core brand truth. Patagonia should own this outright. | repair and long use, real field conditions, plain spoken mission, worn in product |
| 0.55 to 0.84 | Compatible with care. On brand in the Patagonia register. | calm trending audio over field footage, athlete creator work, dry humour |
| 0.35 to 0.54 | Tension. Works in the category but pulls against the register. | fast trend edits, product led openings, aspirational lifestyle framing |
| 0.0 to 0.34 | Off brand. Wins in the feed but would cost the brand. | studio gloss, drop hype, scarcity urgency, unbacked green claims |

Heuristics to combine into the score:

1. **Use over newness.** Does it show the thing being used, repaired, kept? (+) Bought? (minus)
2. **Register.** Plain, dry, unpolished? (+) Produced, eager, salesy? (minus)
3. **Substance.** Is there a mechanism behind any claim? (+) Vague virtue? (cap low)
4. **Authorship.** Filmed by someone who lives it? (+) Performed by someone booked? (minus)
5. **Credibility.** Would it ever read as greenwashing or as selling more stuff for its own
   sake? If yes, cap at 0.34. This brand's whole asset is being believed.

Return a float rounded to two decimals plus a one sentence `rationale` in the brand voice.

## Brand voice rule

Never use a hyphen or an em dash as a generic connector in any string that renders.
Compound modifiers such as "twenty year old" are fine.
