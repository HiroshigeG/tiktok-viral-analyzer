"""
tests/test_signal.py

Contract gate for build_signal.py.

Two test classes:
  TestSchemaValidation  --  the produced signal.json must pass docs/signal.schema.json
  TestBrandVoiceLint    --  no connector hyphen or em/en dash in any string value

Run:  pytest tests/test_signal.py -v
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator, List

import pytest

# ---------------------------------------------------------------------------
# Load build_signal.py by file path (avoids any potential stdlib name collision).
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = Path(__file__).parent.parent.resolve()
_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_SCHEMA_PATH = _WORKTREE_ROOT / "docs" / "signal.schema.json"

_spec = importlib.util.spec_from_file_location(
    "signal_pipeline",
    _WORKTREE_ROOT / "build_signal.py",
)
signal_pipeline = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(signal_pipeline)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_strings(obj: Any) -> Iterator[str]:
    """Recursively yield all string values in a JSON-like structure."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for item in obj:
            yield from _collect_strings(item)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _collect_strings(v)


def _build(fixtures_dir: Path = _FIXTURES_DIR) -> Any:
    """Build signal from the synthetic fixtures. Cached per fixtures_dir."""
    return signal_pipeline.build_signal(str(fixtures_dir))


# ---------------------------------------------------------------------------
# TestSchemaValidation
# ---------------------------------------------------------------------------

class TestSchemaValidation:
    """The produced signal.json must be valid against docs/signal.schema.json."""

    def test_schema_valid(self) -> None:
        """Primary contract gate: zero schema errors."""
        try:
            import jsonschema  # type: ignore
        except ImportError:
            pytest.skip("jsonschema not installed")

        result = _build()

        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema = json.load(f)

        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors(result))
        assert not errors, (
            "signal.json failed schema validation:\n"
            + "\n".join(f"  {list(e.absolute_path)}: {e.message}" for e in errors)
        )

    def test_top_level_keys(self) -> None:
        result = _build()
        for key in ("meta", "segnale", "insight", "direzione"):
            assert key in result, f"Missing top-level key: {key}"

    def test_meta_required_fields(self) -> None:
        meta = _build()["meta"]
        for field in ("generated_at", "brands", "n_videos", "method_note"):
            assert field in meta, f"meta missing required field: {field}"
        assert isinstance(meta["brands"], list)
        assert len(meta["brands"]) >= 1
        assert isinstance(meta["n_videos"], int)
        assert meta["n_videos"] > 0

    def test_segnale_required_fields(self) -> None:
        seg = _build()["segnale"]
        for field in ("winning_patterns", "baseline_delta", "top_videos"):
            assert field in seg, f"segnale missing required field: {field}"
        assert len(seg["winning_patterns"]) >= 1
        assert len(seg["baseline_delta"]) >= 1
        assert len(seg["top_videos"]) >= 1

    def test_winning_patterns_fields(self) -> None:
        for p in _build()["segnale"]["winning_patterns"]:
            for field in ("pattern", "frequency", "avg_engagement", "evidence_video_ids"):
                assert field in p, f"winning_pattern missing field: {field}"
            assert isinstance(p["avg_engagement"], (int, float))
            assert isinstance(p["evidence_video_ids"], list)

    def test_baseline_delta_dimensions(self) -> None:
        delta = _build()["segnale"]["baseline_delta"]
        dims = [d["dimension"] for d in delta]
        for expected in ("hook_type", "pacing", "audio", "who", "text_overlay"):
            assert expected in dims, f"baseline_delta missing dimension: {expected}"
        for d in delta:
            for field in ("dimension", "winners", "losers", "delta_note"):
                assert field in d, f"baseline_delta entry missing field: {field}"

    def test_top_videos_required_fields(self) -> None:
        for v in _build()["segnale"]["top_videos"]:
            for field in ("id", "brand", "url", "views", "engagement", "who", "hook_type", "craft"):
                assert field in v, f"top_video missing field: {field}"
            assert v["who"] in ("owner", "brand", "creator"), (
                f"top_video who must be owner|brand|creator, got: {v['who']}"
            )
            craft = v["craft"]
            for cfield in ("hook", "structure", "audio", "why"):
                assert cfield in craft, f"craft missing field: {cfield}"
            assert isinstance(craft["why"], list)
            assert isinstance(v["views"], int)
            assert isinstance(v["engagement"], (int, float))

    def test_owners_vs_brand_fields(self) -> None:
        ovb = _build()["insight"]["owners_vs_brand"]
        for field in ("owner_share_pct", "brand_share_pct", "evidence"):
            assert field in ovb, f"owners_vs_brand missing field: {field}"
        assert 0.0 <= ovb["owner_share_pct"] <= 100.0
        assert 0.0 <= ovb["brand_share_pct"] <= 100.0
        assert isinstance(ovb["evidence"], list)
        assert len(ovb["evidence"]) >= 1

    def test_brand_gap_fields(self) -> None:
        for gap in _build()["insight"]["brand_gap"]:
            for field in ("category_winning_trait", "brand_today", "gap_note", "brand_fit_score"):
                assert field in gap, f"brand_gap entry missing field: {field}"
            score = gap["brand_fit_score"]
            assert 0.0 <= score <= 1.0, f"brand_fit_score {score} out of [0, 1]"

    def test_audience_voice_fields(self) -> None:
        for cluster in _build()["insight"]["audience_voice"]:
            for field in ("cluster", "sentiment", "volume", "sample_comments"):
                assert field in cluster, f"audience_voice entry missing field: {field}"
            assert cluster["sentiment"] in ("pos", "neg", "mixed"), (
                f"Invalid sentiment: {cluster['sentiment']}"
            )
            assert isinstance(cluster["volume"], int)
            assert cluster["volume"] >= 0
            assert isinstance(cluster["sample_comments"], list)

    def test_direzione_brief_fields(self) -> None:
        brief = _build()["direzione"]["brief"]
        for field in ("title", "thesis", "concept", "format", "hook_formula", "ties_to"):
            assert field in brief, f"brief missing field: {field}"
        assert isinstance(brief["ties_to"], list)
        # ties_to used to assert two fixed campaign platform names, which meant
        # the test only passed for one brand. It now checks the contract that
        # actually matters: the ties are drawn from the winning traits the data
        # produced, so they change with the corpus rather than being stored.
        gap_traits = {
            g["category_winning_trait"] for g in _build()["insight"]["brand_gap"]
        }
        for tie in brief["ties_to"]:
            assert isinstance(tie, str) and tie.strip()
            assert tie in gap_traits, (
                "ties_to entry {!r} is not one of the measured winning traits".format(tie)
            )

    def test_direzione_next_moves_present(self) -> None:
        next_moves = _build()["direzione"]["next_moves"]
        assert isinstance(next_moves, list)
        assert len(next_moves) >= 1
        for move in next_moves:
            assert isinstance(move, str)
            assert move.strip(), "next_moves entry must not be empty"

    def test_thumb_paths_web_relative(self) -> None:
        """All non-empty thumb paths must use the web-relative assets/thumbs/ form."""
        result = _build()
        for v in result["segnale"]["top_videos"]:
            thumb = v.get("thumb", "")
            if thumb:
                assert thumb.startswith("assets/thumbs/"), (
                    f"thumb path not web-relative: {thumb!r}"
                )

    def test_brands_from_fixtures(self) -> None:
        meta = _build()["meta"]
        for brand in ("bmw", "genesis", "lexus"):
            assert brand in meta["brands"]

    def test_n_videos_matches_fixture(self) -> None:
        """All 18 fixture videos (inner join on id) should be present."""
        meta = _build()["meta"]
        assert meta["n_videos"] == 18


# ---------------------------------------------------------------------------
# TestBrandVoiceLint
# ---------------------------------------------------------------------------

class TestBrandVoiceLint:
    """
    No hyphen or em dash as a generic connector in any string value of signal.json.
    Rule: ' - ' (space-hyphen-space) is forbidden; compound modifiers (brand-fit,
    text-driven) are fine because they have no surrounding spaces.
    Em dashes (—) and en dash connectors ( – ) are also forbidden.
    """

    # Patterns that are FORBIDDEN
    _CONNECTOR_HYPHEN = re.compile(r"\s-\s")      # word [space] - [space] word
    _EM_DASH = re.compile(r"[—]")                  # em dash in any position
    _EN_DASH_CONNECTOR = re.compile(r"\s–\s")      # en dash as a connector

    def _violations(
        self,
        pattern: re.Pattern,  # type: ignore[type-arg]
        result: Any,
        label: str,
    ) -> List[str]:
        found: List[str] = []
        for s in _collect_strings(result):
            if pattern.search(s):
                found.append(f"[{label}] {s!r}")
        return found

    def test_no_connector_hyphens(self) -> None:
        result = _build()
        violations = self._violations(self._CONNECTOR_HYPHEN, result, "connector-hyphen")
        assert not violations, (
            "Brand voice violation: space-hyphen-space connector found in output strings:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_no_em_dashes(self) -> None:
        result = _build()
        violations = self._violations(self._EM_DASH, result, "em-dash")
        assert not violations, (
            "Brand voice violation: em dash (—) found in output strings:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_no_en_dash_connectors(self) -> None:
        result = _build()
        violations = self._violations(self._EN_DASH_CONNECTOR, result, "en-dash-connector")
        assert not violations, (
            "Brand voice violation: en dash connector ( – ) found in output strings:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_compound_modifiers_allowed(self) -> None:
        """
        Compound modifiers (no surrounding spaces) must NOT be flagged.
        This test confirms the lint rule is scoped correctly.
        """
        test_strings = [
            "text-driven build",
            "beat-drop variant",
            "brand-fit score",
            "top-performing content",
        ]
        for s in test_strings:
            assert not self._CONNECTOR_HYPHEN.search(s), (
                f"Compound modifier {s!r} incorrectly flagged as a connector hyphen"
            )
