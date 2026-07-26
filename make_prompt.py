#!/usr/bin/env python3
"""
make_prompt.py  --  the MAKE step of Signal.

Reads signal.json (the sense output), synthesizes a generation ready shot prompt
from the real signal (winning patterns, brand fit, brief, audience voice), and
writes it back into signal.json under direzione.generation. This closes the loop
from sense to make: Signal does not just brief, it writes the direction a GenAI
pipeline would execute. "Decide it before you shoot it."

Claude (the same model as the analysis pass) authors the cinematic blocks when
ANTHROPIC_API_KEY is set; otherwise a deterministic composer runs and the block
is flagged source="authored". The NEGATIVE block is always built from the real
low brand fit traits, so the system encodes what NOT to do, from the data.

Brand voice rule: no hyphen or em dash as a generic connector in any string.

Usage:
    python make_prompt.py [--signal signal.json]
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from brandconfig import BrandConfigError, load_config

load_dotenv()
ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

SCHEMA_PATH = Path("docs/signal.schema.json")

# Pipeline line: describes the generation stack, supplied by the config.
# Generation pipeline and grade come from the config so the MAKE step describes
# the brand's own look rather than one that was pasted in once.
_DEFAULT_PIPELINE_LINE = (
    "ComfyUI: a photoreal base model plus a brand LoRA, then AnimateDiff for motion, "
    "then an edit finish."
)
_DEFAULT_GRADE = (
    "Colour and tone in the brand register, restrained rather than saturated."
)


def _cfg():
    """Brand config, or None when unconfigured. Never raises."""
    try:
        return load_config()
    except BrandConfigError:
        return None


def pipeline_line() -> str:
    cfg = _cfg()
    return (cfg.generation.get("pipeline_line") if cfg else "") or _DEFAULT_PIPELINE_LINE


def grade_line() -> str:
    cfg = _cfg()
    return (cfg.generation.get("grade") if cfg else "") or _DEFAULT_GRADE


def subject_name() -> str:
    cfg = _cfg()
    return cfg.subject.name if cfg else "the brand"


def brief_summary() -> str:
    cfg = _cfg()
    return cfg.brief_summary() if cfg else ""


# ---------------------------------------------------------------------------
# Provenance + inputs from the signal
# ---------------------------------------------------------------------------

def _high_fit_traits(signal: Dict[str, Any]) -> List[str]:
    gap = signal.get("insight", {}).get("brand_gap", [])
    return [g["category_winning_trait"] for g in gap if g.get("brand_fit_score", 0) >= 0.8]


def _low_fit_notes(signal: Dict[str, Any]) -> List[str]:
    """The traits to avoid, drawn from the lowest brand fit gaps and the loser baseline."""
    avoid: List[str] = []
    gap = signal.get("insight", {}).get("brand_gap", [])
    # Only genuinely off brand traits (below 0.35) become "avoid" items. Mid fit
    # traits like trending sound are "use selectively", not banned, and adding
    # them here would contradict the AUDIO and GRADE directions.
    for g in sorted(gap, key=lambda x: x.get("brand_fit_score", 1)):
        if g.get("brand_fit_score", 1) < 0.35:
            avoid.append(g["category_winning_trait"].lower())
    # Always encode the canonical off brand tells (from the brand brief).
    canon = [
        "no announcer voiceover",
        "no spec sheet or numbers on screen",
        "no fast montage or rapid cuts",
        "no logo first wide beauty shot",
        "no loud beat drop",
    ]
    return canon + [a for a in avoid if a not in " ".join(canon)]


def _derived_from(signal: Dict[str, Any]) -> str:
    n_pat = len(signal.get("segnale", {}).get("winning_patterns", []))
    n_high = len(_high_fit_traits(signal))
    return f"{n_pat} winning patterns, {n_high} traits at brand fit 0.8 or higher, the owner point of view lane"


def _loop(signal: Dict[str, Any]) -> Dict[str, str]:
    meta = signal.get("meta", {})
    ovb = signal.get("insight", {}).get("owners_vs_brand", {})
    seg = signal.get("segnale", {})
    # Find the highest engagement winning pattern for the measure target.
    pats = sorted(seg.get("winning_patterns", []), key=lambda p: p.get("avg_engagement", 0), reverse=True)
    top_eng = pats[0]["avg_engagement"] if pats else 0
    return {
        "sense": (
            f"Signal read {meta.get('n_videos', 0)} videos across "
            f"{', '.join(meta.get('brands', []))}, decoded the craft, and scored every "
            f"winning trait against the {subject_name()} brand."
        ),
        "make": (
            "From that read, Signal writes the shot prompt below, grounded in the winning "
            "patterns and constrained by what scores off brand. This is generation ready "
            "direction, not a generated asset."
        ),
        "measure": (
            f"The target is set by the data: owner and creator content holds "
            f"{ovb.get('owner_share_pct', 0)} percent of category engagement, and the leading "
            f"pattern runs at {top_eng} percent. New work feeds back into Signal and the loop repeats."
        ),
    }


# ---------------------------------------------------------------------------
# Deterministic composer (fallback, no API)
# ---------------------------------------------------------------------------

def _authored_blocks(signal: Dict[str, Any]) -> List[Dict[str, str]]:
    brief = signal.get("direzione", {}).get("brief", {})
    avoid = _low_fit_notes(signal)
    return [
        {"label": "SCENE", "value": (
            "Open on a single made detail in extreme closeup, a stitch line or the spindle grille "
            "catching low light, a hand leaving the frame. Hold it, then let the full car arrive only at the end."
        )},
        {"label": "CAMERA", "value": "85mm, shallow focus, locked then one slow breath of a push in. One held shot, no cut for the first six seconds."},
        {"label": "LIGHT", "value": "Warm key from a low window, deep falloff into near black, one soft highlight rolling across the surface."},
        {"label": "AUDIO", "value": "Ambient cabin and a quiet pad. No voiceover. Let the sound design carry the feeling."},
        {"label": "GRADE", "value": grade_line()},
        {"label": "NEGATIVE", "value": ", ".join(avoid)},
        {"label": "PIPELINE", "value": pipeline_line()},
    ]


# ---------------------------------------------------------------------------
# Claude composer
# ---------------------------------------------------------------------------

def _claude_blocks(signal: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except Exception as exc:
        print(f"  anthropic unavailable ({exc}); using authored fallback.")
        return None

    seg = signal.get("segnale", {})
    brief = signal.get("direzione", {}).get("brief", {})
    patterns = [p.get("pattern", "") for p in seg.get("winning_patterns", [])][:5]
    high = _high_fit_traits(signal)
    avoid = _low_fit_notes(signal)

    prompt = f"""You are a senior AI film director writing a generation ready shot prompt for a {subject_name()} social film.
Return ONLY valid JSON, no markdown, no commentary.

The prompt must embody these winning patterns from real category data:
{json.dumps(patterns, indent=2)}

It must foreground these on brand traits:
{json.dumps(high, indent=2)}

Creative brief to honor:
  title: {brief.get('title','')}
  concept: {brief.get('concept','')}
  hook_formula: {brief.get('hook_formula','')}

The NEGATIVE block MUST be exactly this comma separated list of things to avoid, verbatim:
{', '.join(avoid)}

Write a cinematic, specific prompt in the {subject_name()} register as described in the brief below (
quiet luxury). Return this exact JSON shape:
{{
  "blocks": [
    {{"label": "SCENE", "value": "two sentences, the opening shot and the held reveal"}},
    {{"label": "CAMERA", "value": "lens, focus, movement"}},
    {{"label": "LIGHT", "value": "key, falloff, mood"}},
    {{"label": "AUDIO", "value": "sound design, no announcer"}},
    {{"label": "GRADE", "value": "color and tone"}},
    {{"label": "NEGATIVE", "value": "the verbatim avoid list above"}},
    {{"label": "PIPELINE", "value": "{pipeline_line()}"}}
  ]
}}

Rules:
- Brand voice: never use a hyphen or em dash as a generic connector. Compound modifiers are fine.
- Keep each value to one or two sentences. Be concrete and shootable.
- Return ONLY the JSON object.
"""
    try:
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        parsed = json.loads(text)
        blocks = parsed["blocks"]
        assert isinstance(blocks, list) and len(blocks) >= 6
        # Force the NEGATIVE and PIPELINE blocks to the grounded values (do not trust the model to drift).
        for b in blocks:
            if b.get("label") == "NEGATIVE":
                b["value"] = ", ".join(avoid)
            if b.get("label") == "PIPELINE":
                b["value"] = pipeline_line()
        print(f"  Claude authored the generation prompt (model {ANTHROPIC_MODEL}).")
        return [{"label": str(b["label"]), "value": str(b["value"])} for b in blocks]
    except Exception as exc:
        print(f"  Claude generation failed ({exc}); using authored fallback.")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_generation(signal: Dict[str, Any]) -> Dict[str, Any]:
    blocks = _claude_blocks(signal)
    source = "claude"
    if blocks is None:
        blocks = _authored_blocks(signal)
        source = "authored"
    return {
        "title": "Signal generated shot prompt",
        "derived_from": _derived_from(signal),
        "blocks": blocks,
        "loop": _loop(signal),
        "source": source,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Write the make step (generation prompt) into signal.json.")
    ap.add_argument("--signal", default="signal.json")
    args = ap.parse_args()

    path = Path(args.signal)
    signal = json.loads(path.read_text(encoding="utf-8"))

    gen = build_generation(signal)
    signal.setdefault("direzione", {})["generation"] = gen
    path.write_text(json.dumps(signal, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote direzione.generation to {path} (source={gen['source']}, {len(gen['blocks'])} blocks).")

    # Validate against the schema if available.
    if SCHEMA_PATH.exists():
        try:
            import jsonschema
            jsonschema.validate(signal, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
            print("Schema validation passed.")
        except Exception as exc:
            print(f"Schema validation FAILED: {exc}")
            raise


if __name__ == "__main__":
    main()
