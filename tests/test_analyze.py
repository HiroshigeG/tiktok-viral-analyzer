#!/usr/bin/env python3
"""
tests/test_analyze.py — Unit tests for analyze.py.

All network calls are mocked. No live Gemini or Claude spend.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure the parent directory is on sys.path before importing analyze
sys.path.insert(0, str(Path(__file__).parent.parent))

# Stub the analyzer module so analyze.py can import without it installed
sys.modules.setdefault("analyzer", MagicMock())

import analyze  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

MOCK_GEMINI_FULL = {
    "VISUAL_HOOK": {
        "hook_type": "Emotional",
        "description": (
            "An extreme closeup of the driver's hands resting on the leather steering wheel"
        ),
        "effectiveness": "Creates intimate connection immediately",
    },
    "NARRATIVE_STRUCTURE": {
        "format": "owner POV",
        "pacing": "slow and deliberate with long held shots",
        "emotional_arc": "quiet anticipation to calm arrival",
    },
    "AUDIO_STRATEGY": {
        "music_sound": "ambient piano with street ambience",
        "is_trending_audio": False,
        "voiceover": {"present": False, "tone": ""},
        "sound_effects_present": True,
    },
    "TEXT_OVERLAY": {
        "style": "minimal white serif on dark background",
        "frequency": "one line at the end only",
        "purpose": "names the feeling rather than the car",
    },
    "WHY_IT_WORKS": {
        "reasons_for_virality": [
            "Viewers recognize the intimacy of the moment",
            "Restraint signals luxury without stating it",
            "The silence creates genuine emotion",
        ],
        "psychological_triggers": ["emotional resonance", "aspiration"],
        "shareability": "people share what they wish they had felt",
    },
    "REPLICATION_BLUEPRINT": {
        "hook_formula": "Start in the cabin. Never explain. Let the object speak.",
    },
}

MOCK_GEMINI_SPARSE: dict = {
    "VISUAL_HOOK": {},
    "NARRATIVE_STRUCTURE": {},
    "AUDIO_STRATEGY": {},
    "TEXT_OVERLAY": {},
    "WHY_IT_WORKS": {},
}

_BASE_VIDEO = {
    "id": "vid001",
    "brand": "lexus",
    "url": "https://tiktok.com/@lexususa/video/vid001",
    "thumb": "data/raw/thumbs/vid001.jpg",
    "local_video_path": "",
    "performance_tier": "top",
    "author": {
        "name": "@lexususa",
        "verified": True,
        "is_brand_account": True,
    },
    "metrics": {
        "views": 500000,
        "likes": 45000,
        "comments": 1200,
        "shares": 3000,
        "engagement_rate": 9.84,
    },
}

MOCK_RAW_VIDEO_BRAND = {**_BASE_VIDEO}

MOCK_RAW_VIDEO_OWNER = {
    **_BASE_VIDEO,
    "id": "vid002",
    "author": {"name": "@johndoe", "verified": False, "is_brand_account": False},
}

MOCK_RAW_VIDEO_CREATOR = {
    **_BASE_VIDEO,
    "id": "vid003",
    "author": {"name": "@influencer", "verified": True, "is_brand_account": False},
}


# ===========================================================================
# Tests: map_gemini_to_craft
# ===========================================================================

class TestMapGeminiToCraft(unittest.TestCase):

    def test_full_mapping_populates_all_fields(self):
        """Complete Gemini dict → all craft fields populated, correct hook_type."""
        hook_type, craft = analyze.map_gemini_to_craft(MOCK_GEMINI_FULL)

        self.assertEqual(hook_type, "Emotional")
        self.assertIn("closeup", craft["hook"].lower())
        # structure: format + " / " + emotional_arc
        self.assertIn("owner POV", craft["structure"])
        self.assertIn("quiet anticipation", craft["structure"])
        self.assertIn("/", craft["structure"])
        # pacing from NARRATIVE_STRUCTURE.pacing
        self.assertIn("slow", craft["pacing"])
        # audio: ambient piano in, sound effects in, no trending, no voiceover
        self.assertIn("ambient piano", craft["audio"])
        self.assertIn("sound effects", craft["audio"])
        self.assertNotIn("trending audio", craft["audio"])
        # text_overlay: three parts semicolon-joined
        self.assertIn("minimal white serif", craft["text_overlay"])
        # why: all three reasons as strings
        self.assertEqual(len(craft["why"]), 3)
        self.assertIsInstance(craft["why"][0], str)

    def test_sparse_mapping_returns_safe_defaults(self):
        """Sparse Gemini dict → all fields empty or empty list, no KeyError."""
        hook_type, craft = analyze.map_gemini_to_craft(MOCK_GEMINI_SPARSE)

        self.assertEqual(hook_type, "")
        self.assertEqual(craft["hook"], "")
        self.assertEqual(craft["structure"], "")
        self.assertEqual(craft["pacing"], "")
        self.assertEqual(craft["audio"], "")
        self.assertEqual(craft["text_overlay"], "")
        self.assertEqual(craft["why"], [])

    def test_structure_format_only(self):
        """If only format is present, structure equals format without trailing slash."""
        gemini = {
            **MOCK_GEMINI_SPARSE,
            "NARRATIVE_STRUCTURE": {
                "format": "transformation",
                "pacing": "",
                "emotional_arc": "",
            },
        }
        _, craft = analyze.map_gemini_to_craft(gemini)
        self.assertEqual(craft["structure"], "transformation")

    def test_audio_trending_flag_included(self):
        """is_trending_audio=True → 'trending audio' appears in audio summary."""
        gemini = {
            **MOCK_GEMINI_SPARSE,
            "AUDIO_STRATEGY": {
                "music_sound": "upbeat pop",
                "is_trending_audio": True,
                "voiceover": {"present": False},
                "sound_effects_present": False,
            },
        }
        _, craft = analyze.map_gemini_to_craft(gemini)
        self.assertIn("trending audio", craft["audio"])
        self.assertIn("upbeat pop", craft["audio"])

    def test_why_filters_none_and_coerces_types(self):
        """None entries are filtered from why; numeric entries are coerced to str."""
        gemini = {
            **MOCK_GEMINI_SPARSE,
            "WHY_IT_WORKS": {
                "reasons_for_virality": ["reason one", 42, None, "reason two"]
            },
        }
        _, craft = analyze.map_gemini_to_craft(gemini)
        self.assertNotIn(None, craft["why"])
        self.assertIn("42", craft["why"])
        self.assertEqual(len(craft["why"]), 3)  # None filtered, 42 kept


# ===========================================================================
# Tests: derive_who
# ===========================================================================

class TestDeriveWho(unittest.TestCase):

    def test_brand_account(self):
        author = {"name": "@lexususa", "verified": True, "is_brand_account": True}
        self.assertEqual(analyze.derive_who(author), "brand")

    def test_verified_non_brand_is_creator(self):
        author = {"name": "@influencer", "verified": True, "is_brand_account": False}
        self.assertEqual(analyze.derive_who(author), "creator")

    def test_unverified_non_brand_is_owner(self):
        author = {"name": "@johndoe", "verified": False, "is_brand_account": False}
        self.assertEqual(analyze.derive_who(author), "owner")

    def test_empty_author_defaults_to_owner(self):
        """Missing fields should not raise; falsy values resolve to 'owner'."""
        self.assertEqual(analyze.derive_who({}), "owner")


# ===========================================================================
# Tests: score_brand_fit_authored
# ===========================================================================

class TestScoreBrandFitAuthored(unittest.TestCase):

    def _high_craft(self) -> dict:
        return {
            "hook": "extreme macro closeup of leather stitching on the steering wheel",
            "structure": "owner POV / quiet emotion from anticipation to arrival",
            "pacing": "slow and deliberate with held shots in silence",
            "audio": "ambient sound design; no voiceover",
            "text_overlay": "single line naming a feeling",
            "why": [
                "craft foregrounded",
                "emotion over specification",
                "loyalty and memory",
            ],
        }

    def _low_craft(self) -> dict:
        return {
            "hook": "announcer voiceover listing horsepower and torque specs",
            "structure": "spec sheet montage with hype energy",
            "pacing": "frantic fast cuts every second",
            "audio": "loud beat drop trending audio",
            "text_overlay": "stacked numbers and hard sell available now",
            "why": ["comedy skit", "meme format"],
        }

    def test_high_score_core_brand_truth(self):
        result = analyze.score_brand_fit_authored(self._high_craft(), "Emotional")
        self.assertGreaterEqual(result["score"], 0.85)
        self.assertLessEqual(result["score"], 1.0)
        self.assertIsInstance(result["rationale"], str)
        self.assertGreater(len(result["rationale"]), 20)

    def test_low_score_off_brand(self):
        result = analyze.score_brand_fit_authored(self._low_craft(), "Shock")
        self.assertLessEqual(result["score"], 0.34)
        self.assertGreaterEqual(result["score"], 0.0)

    def test_score_always_in_valid_range(self):
        """Any craft input must produce score in [0.0, 1.0] rounded to 2 dp."""
        cases = [
            (self._high_craft(), "Emotional"),
            (self._low_craft(), "Shock"),
            ({"hook": "", "structure": "", "pacing": "", "audio": "",
              "text_overlay": "", "why": []}, ""),
        ]
        for craft, hook in cases:
            result = analyze.score_brand_fit_authored(craft, hook)
            self.assertGreaterEqual(result["score"], 0.0)
            self.assertLessEqual(result["score"], 1.0)
            self.assertEqual(result["score"], round(result["score"], 2))

    def test_rationale_no_generic_hyphen_or_em_dash(self):
        """All rationale strings must obey the brand voice rule."""
        for hook in ["Emotional", "Curiosity", "Shock", "Transformation", "Unknown"]:
            result = analyze.score_brand_fit_authored(self._high_craft(), hook)
            self.assertNotIn(
                " — ", result["rationale"],
                f"spaced em dash found in rationale for hook_type={hook!r}",
            )
            self.assertNotIn(
                " - ", result["rationale"],
                f"spaced hyphen found as connector in rationale for hook_type={hook!r}",
            )
            # bare em dash (no spaces) is also disallowed as generic connector
            self.assertNotIn(
                "—", result["rationale"],
                f"em dash found in rationale for hook_type={hook!r}",
            )


# ===========================================================================
# Tests: author_strategic_layer
# ===========================================================================

class TestAuthorStrategicLayer(unittest.TestCase):

    def _craft(self) -> dict:
        return {
            "hook": "owner sitting in the cabin at dawn",
            "structure": "owner POV / quiet emotion",
            "pacing": "slow",
            "audio": "ambient",
            "text_overlay": "one feeling",
            "why": ["authentic", "emotional"],
        }

    def test_produces_all_required_fields(self):
        result = analyze.author_strategic_layer(
            self._craft(), "Emotional", MOCK_RAW_VIDEO_BRAND
        )
        self.assertIn("strategy", result)
        self.assertIn("brand_fit", result)
        self.assertIn("brief_seed", result)
        self.assertEqual(result["source"], "gemini+authored")

        for key in ["cultural_trend", "brand_replication_path", "risk"]:
            self.assertIn(key, result["strategy"])

        self.assertIn("score", result["brand_fit"])
        self.assertIn("rationale", result["brand_fit"])
        self.assertGreaterEqual(result["brand_fit"]["score"], 0.0)
        self.assertLessEqual(result["brand_fit"]["score"], 1.0)

        self.assertIn("concept", result["brief_seed"])
        self.assertIn("hook_formula", result["brief_seed"])

    def test_blueprint_hook_formula_overrides_template(self):
        """hook_formula from Gemini REPLICATION_BLUEPRINT is preferred over template."""
        gemini_raw = {
            "REPLICATION_BLUEPRINT": {
                "hook_formula": "Open on the owner's hands. Never explain. Trust the moment."
            }
        }
        result = analyze.author_strategic_layer(
            self._craft(), "Emotional", MOCK_RAW_VIDEO_BRAND, gemini_raw
        )
        self.assertIn("Trust the moment", result["brief_seed"]["hook_formula"])

    def test_unknown_hook_type_uses_default(self):
        """Unrecognized hook_type should not raise and should use default strategy."""
        result = analyze.author_strategic_layer(
            self._craft(), "UNKNOWN_HOOK_TYPE", MOCK_RAW_VIDEO_BRAND
        )
        self.assertIn("cultural_trend", result["strategy"])
        self.assertEqual(result["source"], "gemini+authored")


# ===========================================================================
# Tests: analyze_video (graceful degradation + field contracts)
# ===========================================================================

class TestAnalyzeVideo(unittest.TestCase):

    def _required_fields(self) -> list:
        return [
            "id", "brand", "performance_tier", "who", "url", "thumb",
            "metrics", "hook_type", "craft", "strategy", "brand_fit",
            "brief_seed", "source",
        ]

    def _required_craft_fields(self) -> list:
        return ["hook", "structure", "pacing", "audio", "text_overlay", "why"]

    def test_missing_video_path_produces_complete_record(self):
        """Empty local_video_path → full Contract B record with craft_from_video=False."""
        video = {**MOCK_RAW_VIDEO_BRAND, "local_video_path": ""}
        record = analyze.analyze_video(video, analyzer=None, claude_client=None)

        for field in self._required_fields():
            self.assertIn(field, record, f"Missing top-level field: {field}")
        for sub in self._required_craft_fields():
            self.assertIn(sub, record["craft"], f"Missing craft.{sub}")

        self.assertEqual(record["craft_from_video"], False)
        self.assertEqual(record["source"], "gemini+authored")

    def test_nonexistent_file_produces_complete_record(self):
        """Non-existent local_video_path → complete record, analyzer never called."""
        video = {**MOCK_RAW_VIDEO_BRAND, "local_video_path": "/tmp/does_not_exist.mp4"}
        mock_analyzer = MagicMock()

        record = analyze.analyze_video(video, analyzer=mock_analyzer, claude_client=None)

        self.assertEqual(record["craft_from_video"], False)
        mock_analyzer.analyze_video_with_gemini.assert_not_called()
        for field in self._required_fields():
            self.assertIn(field, record, f"Missing field: {field}")

    def test_who_derivation_reflected_in_record(self):
        """who field should match derive_who output for each author type."""
        self.assertEqual(
            analyze.analyze_video(MOCK_RAW_VIDEO_BRAND, None, None)["who"], "brand"
        )
        self.assertEqual(
            analyze.analyze_video(MOCK_RAW_VIDEO_OWNER, None, None)["who"], "owner"
        )
        self.assertEqual(
            analyze.analyze_video(MOCK_RAW_VIDEO_CREATOR, None, None)["who"], "creator"
        )

    def test_thumb_forwarded_verbatim_from_contract_a(self):
        """thumb must be forwarded as-is; signal.py owns path normalization."""
        video = {**MOCK_RAW_VIDEO_BRAND, "thumb": "data/raw/thumbs/vid001.jpg"}
        record = analyze.analyze_video(video, None, None)
        self.assertEqual(record["thumb"], "data/raw/thumbs/vid001.jpg")

    def test_claude_path_invoked_when_client_provided(self):
        """claude_strategic_layer is called when a claude_client is passed."""
        mock_strategic = {
            "strategy": {
                "cultural_trend": "a trend",
                "brand_replication_path": "a path",
                "risk": "a risk",
            },
            "brand_fit": {"score": 0.90, "rationale": "On brand."},
            "brief_seed": {"concept": "A concept.", "hook_formula": "A formula."},
            "source": "gemini+claude",
        }
        with patch.object(analyze, "claude_strategic_layer", return_value=mock_strategic) as mock_fn:
            video = {**MOCK_RAW_VIDEO_BRAND, "local_video_path": ""}
            record = analyze.analyze_video(video, analyzer=None, claude_client=MagicMock())
            mock_fn.assert_called_once()
            self.assertEqual(record["source"], "gemini+claude")
            self.assertAlmostEqual(record["brand_fit"]["score"], 0.90)

    def test_gemini_called_when_video_file_exists(self):
        """When local_video_path exists on disk, Gemini is invoked and craft is populated."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            mock_analyzer = MagicMock()
            mock_analyzer.analyze_video_with_gemini.return_value = MOCK_GEMINI_FULL

            video = {**MOCK_RAW_VIDEO_BRAND, "local_video_path": tmp_path}
            record = analyze.analyze_video(
                video, analyzer=mock_analyzer, claude_client=None
            )

            mock_analyzer.analyze_video_with_gemini.assert_called_once()
            self.assertEqual(record["hook_type"], "Emotional")
            self.assertIn("closeup", record["craft"]["hook"].lower())
            # craft_from_video should NOT be False when Gemini succeeded
            self.assertNotEqual(record.get("craft_from_video"), False)
        finally:
            os.unlink(tmp_path)

    def test_gemini_error_degrades_to_authored(self):
        """If Gemini raises, fall through to authored with craft_from_video=False."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            mock_analyzer = MagicMock()
            mock_analyzer.analyze_video_with_gemini.side_effect = RuntimeError("API timeout")

            video = {**MOCK_RAW_VIDEO_BRAND, "local_video_path": tmp_path}
            record = analyze.analyze_video(
                video, analyzer=mock_analyzer, claude_client=None
            )

            self.assertEqual(record["craft_from_video"], False)
            self.assertEqual(record["source"], "gemini+authored")
            # craft fields must still be present (empty stubs)
            for sub in ["hook", "structure", "pacing", "audio", "text_overlay", "why"]:
                self.assertIn(sub, record["craft"])
        finally:
            os.unlink(tmp_path)


# ===========================================================================
# Tests: _summarize_audio helper
# ===========================================================================

class TestSummarizeAudio(unittest.TestCase):

    def test_trending_audio_label_present(self):
        audio = {
            "music_sound": "calming piano",
            "is_trending_audio": True,
            "voiceover": {"present": False},
            "sound_effects_present": False,
        }
        result = analyze._summarize_audio(audio)
        self.assertIn("trending audio", result)
        self.assertIn("calming piano", result)

    def test_voiceover_tone_included(self):
        audio = {
            "music_sound": "None",
            "is_trending_audio": False,
            "voiceover": {"present": True, "tone": "warm and intimate"},
            "sound_effects_present": False,
        }
        result = analyze._summarize_audio(audio)
        self.assertIn("voiceover", result)
        self.assertIn("warm and intimate", result)

    def test_empty_audio_strategy_returns_empty_string(self):
        self.assertEqual(analyze._summarize_audio({}), "")

    def test_none_music_excluded(self):
        audio = {
            "music_sound": "None",
            "is_trending_audio": False,
            "voiceover": {"present": False},
            "sound_effects_present": False,
        }
        result = analyze._summarize_audio(audio)
        self.assertNotIn("None", result)
        self.assertEqual(result, "")


# ===========================================================================
# Tests: authored string brand voice compliance
# ===========================================================================

class TestBrandVoiceCompliance(unittest.TestCase):
    """
    Authored strings that render must never use a hyphen or em dash as a
    generic connector. Compound modifiers are fine.
    """

    EM_DASH = "—"

    def _assert_no_generic_connector(self, text: str, label: str) -> None:
        self.assertNotIn(" — ", text, f"Spaced em dash in {label}")
        self.assertNotIn(self.EM_DASH, text, f"Bare em dash in {label}")
        self.assertNotIn(" - ", text, f"Spaced hyphen connector in {label}")

    def test_all_strategy_templates(self) -> None:
        for hook, strategy in analyze._STRATEGY_BY_HOOK.items():
            for key, val in strategy.items():
                self._assert_no_generic_connector(val, f"_STRATEGY_BY_HOOK[{hook!r}][{key!r}]")
        for key, val in analyze._DEFAULT_STRATEGY.items():
            self._assert_no_generic_connector(val, f"_DEFAULT_STRATEGY[{key!r}]")

    def test_all_brief_seed_templates(self) -> None:
        for hook, seed in analyze._BRIEF_SEED_BY_HOOK.items():
            for key, val in seed.items():
                self._assert_no_generic_connector(val, f"_BRIEF_SEED_BY_HOOK[{hook!r}][{key!r}]")
        for key, val in analyze._DEFAULT_BRIEF_SEED.items():
            self._assert_no_generic_connector(val, f"_DEFAULT_BRIEF_SEED[{key!r}]")

    def test_all_rationale_bands(self) -> None:
        for score in [0.90, 0.70, 0.40, 0.20]:
            rationale = analyze._authored_rationale(score, "Emotional")
            self._assert_no_generic_connector(rationale, f"_authored_rationale(score={score})")


if __name__ == "__main__":
    unittest.main(verbosity=2)
