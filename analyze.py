#!/usr/bin/env python3
"""
analyze.py — Signal pipeline: per-video Gemini craft analysis + strategic layer.

Data flow:
    ingest.py  → data/raw/<brand>.json   (Contract A, our input)
    analyze.py → data/analysis.json      (Contract B, our output)

Per video:
  1. If local_video_path exists on disk, run TikTokAnalyzer.analyze_video_with_gemini
     and map its output into the craft block (see map_gemini_to_craft).
     If the file is missing, stubs are used and craft_from_video is set to False.
  2. Add the strategic layer (strategy, brand_fit, brief_seed) via Claude if
     ANTHROPIC_API_KEY is present, otherwise via the deterministic authored fallback.

who-derivation rule (documented):
  - author.is_brand_account == True                               → "brand"
  - author.is_brand_account == False AND author.verified == True  → "creator"
    (verified non-brand accounts are typically creators with significant following)
  - author.is_brand_account == False AND author.verified == False → "owner"
    (unverified, non-brand poster = everyday owner sharing their experience)

Brand voice rule: never use a hyphen or em dash as a generic connector in authored
strings that can render (rationale, strategy fields, brief_seed). Compound modifiers
(top-performing, AI-powered) are fine.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from brandconfig import BrandConfigError, load_config

load_dotenv()

GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# ── Brand identity, resolved from the config ─────────────────────────────────
# Nothing here names a brand. The config supplies the brand set, the display
# name used in authored prose, and the brief that is the scoring rubric.
# If no config is present the module still works against neutral defaults, so
# importing it (in tests, say) never requires a configured brand.

_CONFIG_CACHE: Any = None


def _cfg() -> Optional[Any]:
    """Load the brand config once. Returns None when there is no usable config."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        try:
            _CONFIG_CACHE = load_config()
        except BrandConfigError:
            _CONFIG_CACHE = False
    return _CONFIG_CACHE or None


def brand_keys() -> List[str]:
    """Brand keys to analyze, subject first. Empty config means read what exists."""
    cfg = _cfg()
    if cfg:
        return cfg.keys
    return sorted(p.stem for p in RAW_DIR.glob("*.json") if p.stem != "index")


def subject_name() -> str:
    """Display name of the brand being advised, for authored prose."""
    cfg = _cfg()
    return cfg.subject.name if cfg else "the brand"


_NEUTRAL_BRIEF: str = """
No brand brief is configured, so this scoring is a neutral placeholder rather than a
brand judgment. Score how well a category winning trait would serve a brand that values
craft, a consistent register, substance over noise, and the long customer relationship.

HIGH (0.85 to 1.0): the trait is a core brand truth the brand should own outright.
COMPATIBLE (0.55 to 0.84): on brand when executed in the brand's register.
TENSION (0.35 to 0.54): works in the category but pulls against the register.
OFF BRAND (0.0 to 0.34): wins in the feed but would cost the brand something.

Configure a brief (see config/TEMPLATE-brief.md) to replace this with the real rubric.
""".strip()


def brief_summary() -> str:
    """The scoring rubric injected into prompts. Falls back to a neutral rubric."""
    cfg = _cfg()
    return cfg.brief_summary() if cfg else _NEUTRAL_BRIEF


def brandify(value: Any) -> Any:
    """
    Fill the {brand} placeholder in authored prose with the configured brand.

    The authored fallback templates are written with a placeholder rather than a
    brand name so that the no-API path is not silently about someone else's
    brand. Applied recursively so a whole strategy or brief_seed block can be
    passed through in one call.
    """
    name = subject_name()
    if isinstance(value, str):
        return value.replace("{brand}", name)
    if isinstance(value, dict):
        return {k: brandify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [brandify(v) for v in value]
    return value
DATA_DIR: Path = Path(__file__).parent / "data"
RAW_DIR: Path = DATA_DIR / "raw"
OUTPUT_PATH: Path = DATA_DIR / "analysis.json"


# ---------------------------------------------------------------------------
# Reuse vendored analyzer (allow import to fail in test environments)
# ---------------------------------------------------------------------------
try:
    from analyzer import TikTokAnalyzer
    _ANALYZER_AVAILABLE = True
except ImportError:
    TikTokAnalyzer = None  # type: ignore[assignment,misc]
    _ANALYZER_AVAILABLE = False


# ===========================================================================
# Gemini → craft mapping
# ===========================================================================

def map_gemini_to_craft(gemini: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Map analyze_video_with_gemini output dict → (hook_type, craft).

    Gemini output shape (from analyzer.py prompt):
      VISUAL_HOOK         : {hook_type, description, effectiveness}
      NARRATIVE_STRUCTURE : {format, pacing, emotional_arc}
      AUDIO_STRATEGY      : {music_sound, is_trending_audio, voiceover, sound_effects_present}
      TEXT_OVERLAY        : {style, frequency, purpose}
      WHY_IT_WORKS        : {reasons_for_virality, psychological_triggers, shareability}
      REPLICATION_BLUEPRINT: {hook_formula, ...}

    Contract B mapping:
      VISUAL_HOOK.hook_type                           → hook_type  (top-level field)
      VISUAL_HOOK.description                         → craft.hook
      NARRATIVE_STRUCTURE.format + .emotional_arc     → craft.structure
      NARRATIVE_STRUCTURE.pacing                      → craft.pacing
      AUDIO_STRATEGY summarized                       → craft.audio
      TEXT_OVERLAY summarized                         → craft.text_overlay
      WHY_IT_WORKS.reasons_for_virality               → craft.why
    """
    visual: Dict[str, Any] = gemini.get("VISUAL_HOOK") or {}
    narrative: Dict[str, Any] = gemini.get("NARRATIVE_STRUCTURE") or {}
    audio_raw: Dict[str, Any] = gemini.get("AUDIO_STRATEGY") or {}
    text_raw: Dict[str, Any] = gemini.get("TEXT_OVERLAY") or {}
    why_raw: Dict[str, Any] = gemini.get("WHY_IT_WORKS") or {}

    hook_type: str = str(visual.get("hook_type") or "")
    hook: str = str(visual.get("description") or "")

    fmt: str = str(narrative.get("format") or "")
    arc: str = str(narrative.get("emotional_arc") or "")
    if fmt and arc:
        structure: str = f"{fmt} / {arc}"
    elif fmt:
        structure = fmt
    elif arc:
        structure = arc
    else:
        structure = ""

    pacing: str = str(narrative.get("pacing") or "")
    audio: str = _summarize_audio(audio_raw)
    text_overlay: str = _summarize_text_overlay(text_raw)

    reasons = why_raw.get("reasons_for_virality") or []
    why: List[str] = [str(r) for r in reasons if r]

    craft: Dict[str, Any] = {
        "hook": hook,
        "structure": structure,
        "pacing": pacing,
        "audio": audio,
        "text_overlay": text_overlay,
        "why": why,
    }
    return hook_type, craft


def _summarize_audio(audio: Dict[str, Any]) -> str:
    """Collapse AUDIO_STRATEGY into one descriptive line (semicolon-separated parts)."""
    parts: List[str] = []

    music: str = str(audio.get("music_sound") or "")
    if music and music.lower() not in ("none", "n/a", ""):
        parts.append(music)

    if audio.get("is_trending_audio"):
        parts.append("trending audio")

    voiceover = audio.get("voiceover")
    if isinstance(voiceover, dict):
        if voiceover.get("present"):
            tone: str = str(voiceover.get("tone") or "")
            vo_label = f"voiceover ({tone})" if tone else "voiceover"
            parts.append(vo_label)
    elif isinstance(voiceover, str) and voiceover.lower() not in ("none", "false", ""):
        parts.append(voiceover)

    if audio.get("sound_effects_present"):
        parts.append("sound effects")

    return "; ".join(parts)


def _summarize_text_overlay(text: Dict[str, Any]) -> str:
    """Collapse TEXT_OVERLAY into one descriptive line (semicolon-separated parts)."""
    style: str = str(text.get("style") or "")
    freq: str = str(text.get("frequency") or "")
    purpose: str = str(text.get("purpose") or "")
    parts = [p for p in [style, freq, purpose] if p]
    return "; ".join(parts)


# ===========================================================================
# who derivation
# ===========================================================================

def derive_who(author: Dict[str, Any]) -> str:
    """
    Derive the 'who' category (owner | brand | creator) from Contract A author fields.

    Rule:
      is_brand_account == True                          → "brand"
      is_brand_account == False AND verified == True    → "creator"
        (verified non-brand accounts are typically creators with significant following)
      is_brand_account == False AND verified == False   → "owner"
        (unverified, non-brand poster = everyday owner sharing their experience)
    """
    if author.get("is_brand_account"):
        return "brand"
    if author.get("verified"):
        return "creator"
    return "owner"


# ===========================================================================
# Brand-fit scoring (deterministic, applies docs/brand-brief.md rubric)
# ===========================================================================

# Keyword sets for the five heuristics. All lowercase; matched against combined craft text.
_CRAFT_ALIGNMENT_HIGH: frozenset = frozenset([
    "macro", "closeup", "close-up", "close up", "extreme detail", "detail",
    "texture", "stitching", "grain", "weave", "finish", "material",
    "dial", "turned", "handcraft", "handmade", "craftsmanship", "craft",
])
_CRAFT_ALIGNMENT_MED: frozenset = frozenset([
    "reveal", "single shot", "held shot", "stillness", "still", "single reveal",
    "clean reveal",
])
_REGISTER_CALM: frozenset = frozenset([
    "ambient", "quiet", "restrained", "calm", "confident", "intimate",
    "silent", "silence", "slow", "gentle", "subtle",
])
_REGISTER_LOUD: frozenset = frozenset([
    "frantic", "hype", "loud", "beat drop", "fast cut", "rapid cut",
    "quick cut", "montage", "energetic", "frenetic",
])
_EMOTION_HIGH: frozenset = frozenset([
    "emotion", "emotional", "memory", "feeling", "connection", "intimate",
    "loyalty", "love", "joy", "pride", "warmth",
])
_SPEC_SIGNALS: frozenset = frozenset([
    "specs", "specifications", "spec sheet", "performance figure",
    "available at", "available now", "in stock", "percent off",
    "limited time",
])
_OWNER_SIGNALS: frozenset = frozenset([
    "owner", "ownership", "customer", "user", "pov", "point of view",
    "loyal", "loyalty", "relationship", "handheld", "authentic",
    "real customer", "real owner",
])
_DIGNITY_VIOLATIONS: frozenset = frozenset([
    "comedy", "meme", "joke", "gag", "skit", "announcer", "salesperson",
    "on sale", "hard sell", "spec sheet", "stacked numbers", "stacked stats",
])

_HOOK_ADJUSTMENT: Dict[str, float] = {
    "Emotional": 0.08,
    "Curiosity": 0.04,
    "Transformation": 0.02,
    "Shock": -0.04,
}


def score_brand_fit_authored(craft: Dict[str, Any], hook_type: str) -> Dict[str, Any]:
    """
    Apply the five-heuristic rubric from docs/brand-brief.md deterministically.

    Heuristics:
      1. Craft alignment  — foregrounded made object or human craft?
      2. Register         — calm, confident, restrained vs loud, frantic, hard sell
      3. Emotion over spec — names a feeling vs lists numbers
      4. Loyalty/ownership — centers the owner or the long relationship
      5. Dignity          — caps score at 0.34 if the pattern would cheapen the brand

    Returns {score: float (0.0..1.0, rounded to 2 dp), rationale: str (one sentence, brand voice)}.
    """
    why_text: str = " ".join(craft.get("why") or [])
    combined: str = " ".join([
        str(craft.get("hook") or ""),
        str(craft.get("structure") or ""),
        str(craft.get("pacing") or ""),
        str(craft.get("audio") or ""),
        str(craft.get("text_overlay") or ""),
        why_text,
        hook_type,
    ]).lower()

    score: float = 0.50  # neutral baseline
    dignity_violated: bool = False

    # Heuristic 1: Craft alignment
    if any(kw in combined for kw in _CRAFT_ALIGNMENT_HIGH):
        score += 0.25
    elif any(kw in combined for kw in _CRAFT_ALIGNMENT_MED):
        score += 0.10

    # Heuristic 2: Register alignment
    if any(kw in combined for kw in _REGISTER_CALM):
        score += 0.12
    if any(kw in combined for kw in _REGISTER_LOUD):
        score -= 0.20

    # Heuristic 3: Emotion over spec
    if any(kw in combined for kw in _EMOTION_HIGH):
        score += 0.10
    if any(kw in combined for kw in _SPEC_SIGNALS):
        score -= 0.15
        dignity_violated = True  # spec sheets cheapen the register

    # Heuristic 4: Loyalty and ownership
    if any(kw in combined for kw in _OWNER_SIGNALS):
        score += 0.10

    # Hook type adjustment
    score += _HOOK_ADJUSTMENT.get(hook_type, 0.0)

    # Heuristic 5: Dignity
    if any(kw in combined for kw in _DIGNITY_VIOLATIONS):
        dignity_violated = True
    if dignity_violated:
        score = min(score, 0.34)

    score = round(max(0.0, min(1.0, score)), 2)
    rationale = _authored_rationale(score, hook_type)
    return {"score": score, "rationale": rationale}


def _authored_rationale(score: float, hook_type: str) -> str:  # noqa: ARG001
    """
    One-sentence brand-voice rationale for the brand fit score.
    No hyphen or em dash as a generic connector.
    """
    if score >= 0.85:
        return (
            "This pattern sits at the core of what {brand} does best: craft foregrounded, "
            "register preserved, and the feeling named without reciting a single specification."
        )
    if score >= 0.55:
        return (
            "The pattern is compatible with the {brand} register when executed with restraint; "
            "precision in execution determines whether it reads as premium or merely current."
        )
    if score >= 0.35:
        return (
            "There is real tension between this pattern and the {brand} register; it earns "
            "attention in the category but risks pulling the brand toward the ordinary."
        )
    return (
        "This pattern earns views in the feed but pulls directly against the {brand} register; "
        "the brand is better served by owning what no competitor can copy."
    )


# ===========================================================================
# Strategy templates (authored fallback)
# ===========================================================================

_STRATEGY_BY_HOOK: Dict[str, Dict[str, str]] = {
    "Emotional": {
        "cultural_trend": (
            "Audiences reward creators who surface real feeling over polished production. "
            "Authenticity and stillness outperform spectacle."
        ),
        "brand_replication_path": (
            "{brand} enters through the customer relationship: an unstaged moment with the "
            "product, the feeling of returning to something that has served you for years. "
            "No narration. No call to action."
        ),
        "risk": (
            "Emotion without craft reads as sentiment rather than substance; "
            "the execution must be restrained or the register slips to generic."
        ),
    },
    "Curiosity": {
        "cultural_trend": (
            "Incomplete reveals and held tension outperform instant disclosure; "
            "viewers finish videos that earn their patience."
        ),
        "brand_replication_path": (
            "Open on a detail the viewer cannot immediately name: a texture, an edge, "
            "a hand on a surface. Hold the reveal. Let the signature detail answer the question."
        ),
        "risk": (
            "Curiosity gaps that do not pay off with a satisfying reveal erode trust; "
            "the delivery must match the promise."
        ),
    },
    "Transformation": {
        "cultural_trend": (
            "Transformation frames have migrated out of beauty into every category; "
            "the format validates ownership as a meaningful change rather than a purchase."
        ),
        "brand_replication_path": (
            "Frame the transformation around feeling rather than feature: not what the product "
            "does but what it changes for the person using it."
        ),
        "risk": (
            "Transformation narratives risk reading as aspirational advertising rather than "
            "authentic experience; the customer's own voice protects against that slip."
        ),
    },
    "Shock": {
        "cultural_trend": (
            "Unexpected pattern interrupts generate the highest first-second watch time; "
            "the feed has trained viewers to scroll past anything predictable."
        ),
        "brand_replication_path": (
            "Use restraint as the surprise: silence where noise is expected, "
            "stillness where motion is assumed. The unexpected is precision, not spectacle."
        ),
        "risk": (
            "Shock tactics can cheapen the brand; any surprise must land as artful rather than "
            "gimmicky or {brand} trades dignity for attention at a net loss."
        ),
    },
}

_DEFAULT_STRATEGY: Dict[str, str] = {
    "cultural_trend": (
        "The feed rewards content that foregrounds craft and authenticity over polished broadcast "
        "production; the audience can feel the difference between a made thing and a marketed one."
    ),
    "brand_replication_path": (
        "{brand} leads with the made object: macro detail, ambient sound, the texture of the "
        "thing itself. Let the craft speak before the logo appears."
    ),
    "risk": (
        "Execution must honour the register; anything that reads as hard sell or hype "
        "undermines the signal the brand has spent years earning."
    ),
}

_BRIEF_SEED_BY_HOOK: Dict[str, Dict[str, str]] = {
    "Emotional": {
        "concept": (
            "A quiet moment between a {brand} customer and the product: no voiceover, no pitch, "
            "only the feeling of something that earns loyalty over years."
        ),
        "hook_formula": (
            "Open on the customer's face or hands, mid use. No context given. "
            "The feeling arrives first."
        ),
    },
    "Curiosity": {
        "concept": (
            "An extreme closeup of {brand} craft the viewer cannot immediately place. "
            "Hold the ambiguity. Let the object reveal itself."
        ),
        "hook_formula": (
            "Start on an unnamed detail. Three seconds of held tension. Then the reveal."
        ),
    },
    "Transformation": {
        "concept": (
            "Before and after: the same person, a visibly different state. "
            "The {brand} product as a ritual that changes the quality of the day."
        ),
        "hook_formula": (
            "State one truth before. State a changed truth after. "
            "Let the cut do the work."
        ),
    },
    "Shock": {
        "concept": (
            "Unexpected stillness in a feed built for motion. "
            "The product sits. The silence and the craft do the work."
        ),
        "hook_formula": (
            "Show stillness where the viewer expects speed. "
            "Hold three beats past comfortable."
        ),
    },
}

_DEFAULT_BRIEF_SEED: Dict[str, str] = {
    "concept": (
        "A single held shot of the made object: tactile, quiet, confident. "
        "The craft speaks before the logo appears."
    ),
    "hook_formula": (
        "Open on craft. Hold the stillness. "
        "Let the object earn attention before the brand is named."
    ),
}


# ===========================================================================
# Authored strategic layer (no ANTHROPIC_API_KEY)
# ===========================================================================

def author_strategic_layer(
    craft: Dict[str, Any],
    hook_type: str,
    raw_video: Dict[str, Any],
    gemini_raw: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Deterministic strategic layer produced without Claude.
    Used when ANTHROPIC_API_KEY is absent or Claude fails.

    Returns a dict with keys: strategy, brand_fit, brief_seed, source.
    source is always "gemini+authored".
    """
    strategy: Dict[str, str] = dict(
        _STRATEGY_BY_HOOK.get(hook_type) or _DEFAULT_STRATEGY
    )
    brand_fit: Dict[str, Any] = score_brand_fit_authored(craft, hook_type)

    # Prefer hook_formula from Gemini REPLICATION_BLUEPRINT when Gemini ran
    blueprint: Dict[str, Any] = (gemini_raw or {}).get("REPLICATION_BLUEPRINT") or {}
    hook_formula_raw: str = str(blueprint.get("hook_formula") or "")

    seed: Dict[str, str] = dict(
        _BRIEF_SEED_BY_HOOK.get(hook_type) or _DEFAULT_BRIEF_SEED
    )
    if hook_formula_raw:
        seed["hook_formula"] = hook_formula_raw

    return {
        "strategy": brandify(strategy),
        "brand_fit": brandify(brand_fit),
        "brief_seed": brandify(seed),
        "source": "gemini+authored",
    }


# ===========================================================================
# Claude strategic layer (ANTHROPIC_API_KEY present)
# ===========================================================================

# The brief is read from the configured markdown file at call time. It used to
# be a Lexus paragraph pasted here, which meant the strategist prompt silently
# analyzed every brand as if it were Lexus. See brandconfig.brief_summary().


def claude_strategic_layer(
    craft: Dict[str, Any],
    hook_type: str,
    raw_video: Dict[str, Any],
    client: Any,
    gemini_raw: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Strategic layer produced by Claude via the Anthropic SDK.
    Falls back to author_strategic_layer if Claude returns invalid output.

    Returns a dict with keys: strategy, brand_fit, brief_seed, source.
    source is "gemini+claude" on success, "gemini+authored" on fallback.
    """
    brand: str = str(raw_video.get("brand") or "")
    tier: str = str(raw_video.get("performance_tier") or "unknown")
    why_text: str = "; ".join(craft.get("why") or [])

    subject: str = subject_name()

    prompt: str = f"""You are a senior brand strategist analyzing TikTok content on behalf of {subject}.
Return ONLY valid JSON with no markdown fences and no commentary.

BRAND BRIEF for {subject}:
{brief_summary()}

VIDEO:
  brand: {brand}
  performance_tier: {tier}
  hook_type: {hook_type}
  craft.hook: {craft.get("hook") or ""}
  craft.structure: {craft.get("structure") or ""}
  craft.pacing: {craft.get("pacing") or ""}
  craft.audio: {craft.get("audio") or ""}
  craft.text_overlay: {craft.get("text_overlay") or ""}
  craft.why: {why_text}

Return this exact JSON structure with no other text:
{{
  "strategy": {{
    "cultural_trend": "one or two sentences on the cultural trend this taps into",
    "brand_replication_path": "one or two sentences on how {subject} should replicate this",
    "risk": "one sentence on the risk for {subject}"
  }},
  "brand_fit": {{
    "score": 0.0,
    "rationale": "one sentence in the {subject} brand voice"
  }},
  "brief_seed": {{
    "concept": "one or two sentences describing the creative concept",
    "hook_formula": "one sentence describing the hook formula"
  }}
}}

Rules:
- brand_fit.score is a float 0.0 to 1.0 rounded to 2 decimal places.
- Apply the five scoring heuristics from the brand brief above.
- Brand voice: never use a hyphen or em dash as a generic connector in any string value.
  Compound modifiers (top-performing, AI-powered) are fine.
- Return ONLY the JSON object. Nothing else.
"""

    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text: str = response.content[0].text.strip()

        # Clean markdown fences if model added them
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        parsed: Dict[str, Any] = json.loads(text)

        # Validate required structure
        assert "strategy" in parsed and "brand_fit" in parsed and "brief_seed" in parsed
        assert isinstance(parsed["brand_fit"].get("score"), (int, float))

        parsed["brand_fit"]["score"] = round(
            max(0.0, min(1.0, float(parsed["brand_fit"]["score"]))), 2
        )
        parsed["source"] = "gemini+claude"
        return parsed

    except Exception as exc:
        print(
            f"  ⚠  Claude strategic layer failed ({exc}); falling back to authored.",
            file=sys.stderr,
        )
        return author_strategic_layer(craft, hook_type, raw_video, gemini_raw)


# ===========================================================================
# Per-video orchestration
# ===========================================================================

def _empty_craft() -> Dict[str, Any]:
    """Return an empty craft block matching the Contract B shape."""
    return {
        "hook": "",
        "structure": "",
        "pacing": "",
        "audio": "",
        "text_overlay": "",
        "why": [],
    }


def analyze_video(
    raw_video: Dict[str, Any],
    analyzer: Optional[Any],
    claude_client: Optional[Any],
) -> Dict[str, Any]:
    """
    Produce one Contract B record from one Contract A video entry.

    Gemini is invoked only when local_video_path is non-empty and the file
    exists on disk. Missing or inaccessible video files are handled gracefully:
    an authored-only record is produced with craft_from_video set to False.
    Errors from Gemini or Claude are caught and degraded gracefully.

    thumb is forwarded from Contract A verbatim; signal.py owns normalization
    to the web-relative assets/thumbs/ path.
    """
    vid_id: str = str(raw_video.get("id") or "")
    brand: str = str(raw_video.get("brand") or "")
    url: str = str(raw_video.get("url") or "")
    thumb: str = str(raw_video.get("thumb") or "")
    perf_tier: str = str(raw_video.get("performance_tier") or "")
    author: Dict[str, Any] = raw_video.get("author") or {}
    metrics_raw: Dict[str, Any] = raw_video.get("metrics") or {}

    metrics: Dict[str, Any] = {
        "views": int(metrics_raw.get("views") or 0),
        "likes": int(metrics_raw.get("likes") or 0),
        "comments": int(metrics_raw.get("comments") or 0),
        "shares": int(metrics_raw.get("shares") or 0),
        "engagement_rate": float(metrics_raw.get("engagement_rate") or 0.0),
    }

    who: str = derive_who(author)

    # ── Gemini craft analysis ──────────────────────────────────────────────
    local_path_str: str = str(raw_video.get("local_video_path") or "")
    gemini_raw: Optional[Dict[str, Any]] = None
    craft: Dict[str, Any] = _empty_craft()
    hook_type: str = ""
    craft_from_video: bool = False

    if local_path_str and analyzer is not None:
        video_path = Path(local_path_str)
        if video_path.exists():
            try:
                print(f"  🎬 Gemini: {vid_id} ({video_path.name})")
                video_meta: Dict[str, Any] = {
                    "id": vid_id,
                    "webVideoUrl": url,
                    "videoMeta": {
                        "playCount": metrics["views"],
                        "diggCount": metrics["likes"],
                        "commentCount": metrics["comments"],
                        "shareCount": metrics["shares"],
                    },
                }
                gemini_raw = analyzer.analyze_video_with_gemini(video_path, video_meta)
                hook_type, craft = map_gemini_to_craft(gemini_raw)
                craft_from_video = True
            except Exception as exc:
                print(
                    f"  ⚠  Gemini failed for {vid_id}: {exc}",
                    file=sys.stderr,
                )
                gemini_raw = None
                craft = _empty_craft()
                hook_type = ""
                craft_from_video = False
        else:
            print(
                f"  ⚠  Video file not found: {local_path_str}",
                file=sys.stderr,
            )
    elif not local_path_str:
        print(
            f"  ℹ  No local_video_path for {vid_id}; using authored craft.",
            file=sys.stderr,
        )

    # ── Strategic layer ────────────────────────────────────────────────────
    if claude_client is not None:
        strategic = claude_strategic_layer(
            craft, hook_type, raw_video, claude_client, gemini_raw
        )
    else:
        strategic = author_strategic_layer(craft, hook_type, raw_video, gemini_raw)

    # ── Assemble Contract B record ─────────────────────────────────────────
    record: Dict[str, Any] = {
        "id": vid_id,
        "brand": brand,
        "performance_tier": perf_tier,
        "who": who,
        "url": url,
        "thumb": thumb,
        "metrics": metrics,
        "hook_type": hook_type,
        "craft": craft,
        "strategy": strategic["strategy"],
        "brand_fit": strategic["brand_fit"],
        "brief_seed": strategic["brief_seed"],
        "source": strategic["source"],
    }

    # Optional honesty field (additionalProperties is allowed in Contract B)
    if not craft_from_video:
        record["craft_from_video"] = False

    return record


# ===========================================================================
# Main
# ===========================================================================

def main() -> None:
    """
    Read data/raw/<brand>.json for all brands, run per-video analysis,
    and write data/analysis.json (Contract B).
    """
    # ── Resolve Claude client ────────────────────────────────────────────
    claude_client: Optional[Any] = None
    claude_available: bool = False

    if ANTHROPIC_API_KEY:
        try:
            import anthropic  # local import so module is usable without the SDK
            claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            claude_available = True
            print(f"✅ Claude enabled (model: {ANTHROPIC_MODEL})")
        except ImportError:
            print(
                "⚠  anthropic SDK not installed; using authored fallback.",
                file=sys.stderr,
            )
    else:
        print(
            "ℹ  ANTHROPIC_API_KEY not set; "
            "using authored fallback (source: gemini+authored)."
        )

    # ── Resolve Gemini analyzer ──────────────────────────────────────────
    analyzer_instance: Optional[Any] = None
    if _ANALYZER_AVAILABLE and GEMINI_API_KEY:
        analyzer_instance = TikTokAnalyzer()
        print("✅ Gemini analyzer ready.")
    elif not GEMINI_API_KEY:
        print(
            "⚠  GEMINI_API_KEY not set; Gemini analysis will be skipped.",
            file=sys.stderr,
        )

    # ── Process brands ───────────────────────────────────────────────────
    all_records: List[Dict[str, Any]] = []

    for brand in brand_keys():
        raw_path = RAW_DIR / f"{brand}.json"
        if not raw_path.exists():
            print(
                f"⚠  {raw_path} not found; skipping {brand}.",
                file=sys.stderr,
            )
            continue

        with open(raw_path, encoding="utf-8") as fh:
            brand_data: Dict[str, Any] = json.load(fh)

        videos: List[Dict[str, Any]] = brand_data.get("videos") or []
        print(f"\n── {brand.upper()} ({len(videos)} videos) ──────────────")

        for video in videos:
            print(
                f"  Processing {video.get('id') or '?'} "
                f"[{video.get('performance_tier') or '?'}]"
            )
            record = analyze_video(video, analyzer_instance, claude_client)
            all_records.append(record)

    # ── Write output ─────────────────────────────────────────────────────
    if not all_records:
        # Writing an empty analysis would overwrite the previous run's file
        # with nothing, which actually happened when an ingest against a wrong
        # handle produced zero raw records and this module shrugged. An empty
        # corpus is a failed run, and failing loudly here stops the pipeline
        # before build_signal turns it into a cryptic merge error.
        print(
            "❌  No videos to analyze: no raw records matched the configured "
            "brands. Not writing an empty analysis over the previous one. "
            "Check data/raw/ and the handles in the config.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    output: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claude_available": claude_available,
        "videos": all_records,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False)

    print(
        f"\n✅ data/analysis.json written "
        f"({len(all_records)} records, claude_available={claude_available})"
    )


if __name__ == "__main__":
    main()
