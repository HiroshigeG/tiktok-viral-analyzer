"""
tests/test_ingest.py — Unit tests for ingest.py transform functions.

All tests are pure / in-memory:
  - No Apify actor calls
  - No disk I/O
  - No network requests
  - No APIFY_API_KEY required

Covered functions:
  calc_engagement_rate   — formula + edge cases
  is_brand_account       — handle normalisation
  split_top_bottom       — tier labels, middle dropped, ordering
  transform_video        — Contract A shape + field values
  make_index_record      — flat index fields
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

# Allow test runner to find ingest.py in the parent (worktree root) directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest import (
    calc_engagement_rate,
    is_brand_account,
    split_top_bottom,
    transform_video,
    make_index_record,
    _extract_signals,
    _extract_video_id_from_url,
    _normalise_comment,
    _group_comments_by_video,
)


# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════

def _make_pool(view_counts: List[int]) -> List[Dict]:
    """
    Build minimal Apify-style video dicts from a list of relative scores.

    split_top_bottom now tiers by ENGAGEMENT RATE with a views >= 1000 floor,
    so each video gets views well above the floor (1_000_000 + score) and
    engagement that increases with the score (diggCount = score). Both views
    and engagement are therefore monotonic in the input score, so a descending
    input still puts the first elements in the top tier.
    """
    return [
        {
            "id":        str(i),
            "videoMeta": {"playCount": 1_000_000 + v, "diggCount": v, "commentCount": 0, "shareCount": 0},
            "authorMeta": {"name": "testaccount", "verified": False},
            "webVideoUrl": f"https://www.tiktok.com/@testaccount/video/{i}",
            "text": "",
        }
        for i, v in enumerate(view_counts)
    ]


def _mock_raw_video() -> Dict:
    """
    Realistic Apify dataset item for clockworks~tiktok-scraper.

    REAL SHAPE (confirmed by probe): metrics are at the TOP LEVEL
    (playCount / diggCount / commentCount / shareCount), NOT nested under
    videoMeta.  This fixture was updated to match the real actor output;
    the previous nested-videoMeta shape is kept in _mock_raw_video_nested()
    to exercise the backward-compat fallback path.
    """
    return {
        "id":           "7123456789",
        "webVideoUrl":  "https://www.tiktok.com/@lexususa/video/7123456789",
        "text":         "The road is yours. #lexus",
        "createTime":   1700000000,
        "playCount":    500_000,
        "diggCount":    25_000,
        "commentCount": 800,
        "shareCount":   300,
        "authorMeta": {
            "name":     "lexususa",
            "verified": True,
        },
    }


def _mock_raw_video_nested() -> Dict:
    """
    Same video but with metrics under videoMeta (old / nested shape).
    Used to test the backward-compat fallback path in transform_video and
    split_top_bottom.
    """
    return {
        "id":          "7123456789",
        "webVideoUrl": "https://www.tiktok.com/@lexususa/video/7123456789",
        "text":        "The road is yours. #lexus",
        "createTime":  1700000000,
        "videoMeta": {
            "playCount":    500_000,
            "diggCount":    25_000,
            "commentCount": 800,
            "shareCount":   300,
        },
        "authorMeta": {
            "name":     "lexususa",
            "verified": True,
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# calc_engagement_rate
# ════════════════════════════════════════════════════════════════════════════

class TestCalcEngagementRate:
    def test_basic_formula(self):
        # (100 + 10 + 5) / 1000 * 100 = 11.5
        assert calc_engagement_rate(100, 10, 5, 1_000) == 11.5

    def test_zero_views_returns_zero(self):
        assert calc_engagement_rate(100, 50, 20, 0) == 0.0

    def test_negative_views_returns_zero(self):
        assert calc_engagement_rate(10, 5, 1, -1) == 0.0

    def test_rounds_to_two_decimal_places(self):
        # (1+1+1) / 7 * 100 = 42.857142… → 42.86
        expected = round(3 / 7 * 100, 2)
        assert calc_engagement_rate(1, 1, 1, 7) == expected

    def test_all_zero_metrics_with_views(self):
        assert calc_engagement_rate(0, 0, 0, 10_000) == 0.0

    def test_maximum_possible(self):
        # All views liked+commented+shared — unusual but valid
        assert calc_engagement_rate(100, 0, 0, 100) == 100.0

    def test_large_realistic_numbers(self):
        # 25000+800+300 = 26100; 26100/500000*100 = 5.22
        assert calc_engagement_rate(25_000, 800, 300, 500_000) == 5.22


# ════════════════════════════════════════════════════════════════════════════
# is_brand_account
# ════════════════════════════════════════════════════════════════════════════

class TestIsBrandAccount:
    def test_exact_match(self):
        assert is_brand_account("@lexususa", "@lexususa") is True

    def test_case_insensitive(self):
        assert is_brand_account("@LexusUSA", "@lexususa") is True
        assert is_brand_account("@LEXUSUSA", "@LEXUSUSA") is True

    def test_strips_at_from_author(self):
        assert is_brand_account("lexususa", "@lexususa") is True

    def test_strips_at_from_official(self):
        assert is_brand_account("@lexususa", "lexususa") is True

    def test_both_without_at(self):
        assert is_brand_account("lexususa", "lexususa") is True

    def test_mismatch_different_brand(self):
        assert is_brand_account("@genesis_usa", "@lexususa") is False

    def test_mismatch_owner(self):
        assert is_brand_account("@carguy99", "@lexususa") is False

    def test_empty_author(self):
        assert is_brand_account("", "@lexususa") is False

    def test_both_empty(self):
        # Both normalise to "" — technically equal
        assert is_brand_account("", "") is True


# ════════════════════════════════════════════════════════════════════════════
# split_top_bottom
# ════════════════════════════════════════════════════════════════════════════

class TestSplitTopBottom:
    def test_tier_labels_top(self):
        pool = _make_pool([100, 90, 80, 70, 60, 50, 40, 30, 20])
        top, _ = split_top_bottom(pool)
        assert all(v["performance_tier"] == "top" for v in top)

    def test_tier_labels_bottom(self):
        pool = _make_pool([100, 90, 80, 70, 60, 50, 40, 30, 20])
        _, bottom = split_top_bottom(pool)
        assert all(v["performance_tier"] == "bottom" for v in bottom)

    def test_top_has_highest_views(self):
        pool = _make_pool([100, 90, 80, 70, 60, 50, 40, 30, 20])
        top, bottom = split_top_bottom(pool)
        min_top_views    = min(v["videoMeta"]["playCount"] for v in top)
        max_bottom_views = max(v["videoMeta"]["playCount"] for v in bottom)
        assert min_top_views > max_bottom_views

    def test_middle_third_dropped(self):
        # Pool of 9: top=[0,1,2] (views 100,90,80), bottom=[6,7,8] (40,30,20)
        # Middle=[3,4,5] (70,60,50) must NOT appear in kept set
        pool = _make_pool([100, 90, 80, 70, 60, 50, 40, 30, 20])
        top, bottom = split_top_bottom(pool)
        kept_ids = {v["id"] for v in top + bottom}
        middle_ids = {"3", "4", "5"}
        assert not middle_ids.intersection(kept_ids), (
            f"Middle videos appeared in kept set: {middle_ids & kept_ids}"
        )

    def test_equal_thirds_nine_videos(self):
        pool = _make_pool([100, 90, 80, 70, 60, 50, 40, 30, 20])
        top, bottom = split_top_bottom(pool)
        assert len(top)    == 3
        assert len(bottom) == 3

    def test_equal_thirds_twelve_videos(self):
        pool = _make_pool(list(range(120, 0, -10)))  # 12 videos
        top, bottom = split_top_bottom(pool)
        assert len(top)    == 4
        assert len(bottom) == 4

    def test_empty_pool(self):
        top, bottom = split_top_bottom([])
        assert top    == []
        assert bottom == []

    def test_single_video(self):
        pool = _make_pool([100])
        top, bottom = split_top_bottom(pool)
        # With n=1, third=1; the single video goes to top, and bottom excludes
        # anything already in top, so bottom is empty (no overlap).
        assert len(top)    == 1
        assert len(bottom) == 0

    def test_no_duplicates_with_nine_videos(self):
        # Top and bottom should have no shared IDs in the standard case
        pool = _make_pool([100, 90, 80, 70, 60, 50, 40, 30, 20])
        top, bottom = split_top_bottom(pool)
        top_ids    = {v["id"] for v in top}
        bottom_ids = {v["id"] for v in bottom}
        assert top_ids.isdisjoint(bottom_ids)

    def test_mutates_performance_tier_in_place(self):
        pool = _make_pool([100, 90, 80, 70, 60, 50, 40, 30, 20])
        top, bottom = split_top_bottom(pool)
        # The original dicts in top/bottom should have the tier set
        for v in top:
            assert v.get("performance_tier") == "top"
        for v in bottom:
            assert v.get("performance_tier") == "bottom"

    # ── Regression: real top-level playCount shape ────────────────────────

    def test_sorts_correctly_with_top_level_play_count(self):
        """
        REGRESSION — split_top_bottom ranked by videoMeta.playCount which
        does not exist on real actor items, causing all videos to score 0
        and the sort to be undefined.  Must sort correctly on top-level
        playCount.
        """
        # Build pool using the REAL top-level shape (no videoMeta wrapper)
        pool = [
            {"id": "high",  "playCount": 900_000, "authorMeta": {}},
            {"id": "mid",   "playCount": 500_000, "authorMeta": {}},
            {"id": "mid2",  "playCount": 400_000, "authorMeta": {}},
            {"id": "low",   "playCount": 100_000, "authorMeta": {}},
            {"id": "lower", "playCount":  50_000, "authorMeta": {}},
            {"id": "low2",  "playCount":  30_000, "authorMeta": {}},
            {"id": "vlow",  "playCount":  10_000, "authorMeta": {}},
            {"id": "vlow2", "playCount":   5_000, "authorMeta": {}},
            {"id": "vlow3", "playCount":   1_000, "authorMeta": {}},
        ]
        top, bottom = split_top_bottom(pool)
        # Top tier must have the three highest-view videos
        top_ids = {v["id"] for v in top}
        assert "high" in top_ids, "highest-view video must be in top tier"
        # Bottom tier must have the three lowest-view videos
        bottom_ids = {v["id"] for v in bottom}
        assert "vlow3" in bottom_ids, "lowest-view video must be in bottom tier"
        # Top must beat bottom on views
        min_top_views    = min(v["playCount"] for v in top)
        max_bottom_views = max(v["playCount"] for v in bottom)
        assert min_top_views > max_bottom_views


# ════════════════════════════════════════════════════════════════════════════
# transform_video — Contract A shape
# ════════════════════════════════════════════════════════════════════════════

class TestTransformVideo:
    def test_all_contract_a_top_level_keys_present(self):
        raw    = _mock_raw_video()
        record = transform_video(raw, "lexus", "@lexususa", "top")
        required = [
            "id", "brand", "url", "thumb", "local_video_path",
            "text", "created_at", "author", "metrics",
            "performance_tier", "comments",
        ]
        for key in required:
            assert key in record, f"Contract A missing top-level key: '{key}'"

    def test_author_keys_present(self):
        record = transform_video(_mock_raw_video(), "lexus", "@lexususa", "top")
        for key in ("name", "verified", "is_brand_account"):
            assert key in record["author"], f"Contract A missing author key: '{key}'"

    def test_metrics_keys_present(self):
        record = transform_video(_mock_raw_video(), "lexus", "@lexususa", "top")
        for key in ("views", "likes", "comments", "shares", "engagement_rate"):
            assert key in record["metrics"], f"Contract A missing metrics key: '{key}'"

    def test_is_brand_account_true(self):
        record = transform_video(_mock_raw_video(), "lexus", "@lexususa", "top")
        assert record["author"]["is_brand_account"] is True

    def test_is_brand_account_false_for_owner(self):
        raw = _mock_raw_video()
        raw["authorMeta"]["name"] = "carguy99"
        record = transform_video(raw, "lexus", "@lexususa", "top")
        assert record["author"]["is_brand_account"] is False

    def test_engagement_rate_matches_formula(self):
        raw    = _mock_raw_video()
        record = transform_video(raw, "lexus", "@lexususa", "top")
        # views=500000, likes=25000, comments=800, shares=300
        expected = calc_engagement_rate(25_000, 800, 300, 500_000)
        assert record["metrics"]["engagement_rate"] == expected

    def test_performance_tier_top(self):
        record = transform_video(_mock_raw_video(), "lexus", "@lexususa", "top")
        assert record["performance_tier"] == "top"

    def test_performance_tier_bottom(self):
        record = transform_video(_mock_raw_video(), "lexus", "@lexususa", "bottom")
        assert record["performance_tier"] == "bottom"

    def test_brand_key_stored(self):
        record = transform_video(_mock_raw_video(), "lexus", "@lexususa", "top")
        assert record["brand"] == "lexus"

    def test_comments_default_empty_list(self):
        record = transform_video(_mock_raw_video(), "lexus", "@lexususa", "top")
        assert record["comments"] == []

    def test_comments_passed_through(self):
        coms   = [{"text": "Great car!", "likes": 5, "author": "@fan"}]
        record = transform_video(
            _mock_raw_video(), "lexus", "@lexususa", "top", comments=coms
        )
        assert record["comments"] == coms

    def test_thumb_path_stored(self):
        record = transform_video(
            _mock_raw_video(), "lexus", "@lexususa", "top",
            thumb_path="data/raw/thumbs/7123456789.jpg",
        )
        assert record["thumb"] == "data/raw/thumbs/7123456789.jpg"

    def test_video_path_stored(self):
        record = transform_video(
            _mock_raw_video(), "lexus", "@lexususa", "top",
            video_path="data/videos/7123456789.mp4",
        )
        assert record["local_video_path"] == "data/videos/7123456789.mp4"

    def test_empty_thumb_and_video_defaults(self):
        record = transform_video(_mock_raw_video(), "lexus", "@lexususa", "top")
        assert record["thumb"]            == ""
        assert record["local_video_path"] == ""

    def test_created_at_from_unix_epoch(self):
        raw    = _mock_raw_video()
        record = transform_video(raw, "lexus", "@lexususa", "top")
        # createTime=1700000000 should produce an ISO-8601 string
        assert record["created_at"].startswith("2023-")  # Nov 2023

    def test_author_handle_gets_at_prefix(self):
        raw = _mock_raw_video()
        raw["authorMeta"]["name"] = "lexususa"  # no @
        record = transform_video(raw, "lexus", "@lexususa", "top")
        assert record["author"]["name"] == "@lexususa"

    def test_verified_flag_passed(self):
        record = transform_video(_mock_raw_video(), "lexus", "@lexususa", "top")
        assert record["author"]["verified"] is True

    def test_video_id_stored(self):
        record = transform_video(_mock_raw_video(), "lexus", "@lexususa", "top")
        assert record["id"] == "7123456789"

    def test_url_stored(self):
        record = transform_video(_mock_raw_video(), "lexus", "@lexususa", "top")
        assert "lexususa" in record["url"]

    def test_metrics_values_correct(self):
        record = transform_video(_mock_raw_video(), "lexus", "@lexususa", "top")
        assert record["metrics"]["views"]    == 500_000
        assert record["metrics"]["likes"]    == 25_000
        assert record["metrics"]["comments"] == 800
        assert record["metrics"]["shares"]   == 300

    # ── Regression: real top-level metric shape (probe-confirmed) ────────

    def test_metrics_nonzero_from_top_level_real_shape(self):
        """
        REGRESSION — the probe found all metrics returned as 0 because the
        code read from videoMeta which does not exist on real actor items.
        Metrics must be non-zero when the real top-level shape is used.
        """
        record = transform_video(_mock_raw_video(), "lexus", "@lexususa", "top")
        assert record["metrics"]["views"]           > 0, "views should be non-zero"
        assert record["metrics"]["likes"]           > 0, "likes should be non-zero"
        assert record["metrics"]["engagement_rate"] > 0, "engagement_rate should be non-zero"

    def test_metrics_fallback_to_video_meta_nested_shape(self):
        """
        REGRESSION — backward-compat: items with metrics under videoMeta
        (old shape / unit-test fixtures) must still produce correct values.
        """
        record = transform_video(_mock_raw_video_nested(), "lexus", "@lexususa", "top")
        assert record["metrics"]["views"]           == 500_000
        assert record["metrics"]["likes"]           == 25_000
        assert record["metrics"]["engagement_rate"] == calc_engagement_rate(25_000, 800, 300, 500_000)

    def test_metrics_zero_play_count_not_skipped_by_falsy(self):
        """
        Edge case: a video with playCount=0 at top level must NOT fall through
        to a nested videoMeta that might have a non-zero value.  The key-in-dict
        check (not `or`) ensures a genuine 0 is respected.
        """
        raw = _mock_raw_video()
        raw["playCount"] = 0          # top-level 0
        raw["videoMeta"] = {"playCount": 999_999}  # should be ignored
        record = transform_video(raw, "lexus", "@lexususa", "top")
        assert record["metrics"]["views"] == 0


# ════════════════════════════════════════════════════════════════════════════
# make_index_record
# ════════════════════════════════════════════════════════════════════════════

class TestMakeIndexRecord:
    def _build_record(self, thumb: str = "data/raw/thumbs/abc.jpg") -> Dict:
        return transform_video(
            _mock_raw_video(), "lexus", "@lexususa", "top",
            thumb_path=thumb,
        )

    def test_all_required_index_keys_present(self):
        idx = make_index_record(self._build_record())
        required = [
            "id", "brand", "performance_tier", "is_brand_account",
            "views", "engagement_rate", "url", "thumb",
        ]
        for key in required:
            assert key in idx, f"Index missing key: '{key}'"

    def test_id_value(self):
        idx = make_index_record(self._build_record())
        assert idx["id"] == "7123456789"

    def test_brand_value(self):
        idx = make_index_record(self._build_record())
        assert idx["brand"] == "lexus"

    def test_performance_tier_value(self):
        idx = make_index_record(self._build_record())
        assert idx["performance_tier"] == "top"

    def test_is_brand_account_value(self):
        idx = make_index_record(self._build_record())
        assert idx["is_brand_account"] is True

    def test_views_value(self):
        idx = make_index_record(self._build_record())
        assert idx["views"] == 500_000

    def test_engagement_rate_value(self):
        idx = make_index_record(self._build_record())
        assert idx["engagement_rate"] == calc_engagement_rate(25_000, 800, 300, 500_000)

    def test_thumb_value(self):
        idx = make_index_record(self._build_record(thumb="data/raw/thumbs/abc.jpg"))
        assert idx["thumb"] == "data/raw/thumbs/abc.jpg"

    def test_url_present(self):
        idx = make_index_record(self._build_record())
        assert idx["url"] != ""

    def test_no_extra_keys_beyond_required(self):
        # Index should be a flat, compact record — no nested dicts
        idx = make_index_record(self._build_record())
        for v in idx.values():
            assert not isinstance(v, dict), f"Unexpected nested dict in index record: {v}"


# ════════════════════════════════════════════════════════════════════════════
# _extract_video_id_from_url
# ════════════════════════════════════════════════════════════════════════════

class TestExtractVideoIdFromUrl:
    def test_standard_tiktok_url(self):
        url = "https://www.tiktok.com/@lexususa/video/7123456789"
        assert _extract_video_id_from_url(url) == "7123456789"

    def test_trailing_slash(self):
        url = "https://www.tiktok.com/@lexususa/video/7123456789/"
        assert _extract_video_id_from_url(url) == "7123456789"

    def test_no_numeric_segment(self):
        url = "https://www.tiktok.com/@lexususa"
        assert _extract_video_id_from_url(url) == ""

    def test_empty_string(self):
        assert _extract_video_id_from_url("") == ""

    def test_different_brand(self):
        url = "https://www.tiktok.com/@bmw/video/9999000011112222"
        assert _extract_video_id_from_url(url) == "9999000011112222"


# ════════════════════════════════════════════════════════════════════════════
# _normalise_comment
# ════════════════════════════════════════════════════════════════════════════

class TestNormaliseComment:
    def test_basic_fields(self):
        item = {"text": "Great car!", "diggCount": 5, "uniqueId": "fan1"}
        c = _normalise_comment(item)
        assert c == {"text": "Great car!", "likes": 5, "author": "@fan1"}

    def test_adds_at_prefix_to_author(self):
        item = {"text": "Nice.", "diggCount": 0, "uniqueId": "noprefixuser"}
        c = _normalise_comment(item)
        assert c["author"] == "@noprefixuser"

    def test_keeps_existing_at_prefix(self):
        item = {"text": "👍", "diggCount": 1, "uniqueId": "@already"}
        c = _normalise_comment(item)
        assert c["author"] == "@already"

    def test_fallback_field_names(self):
        # commentText instead of text; likes instead of diggCount; author instead of uniqueId
        item = {"commentText": "Fallback text", "likes": 3, "author": "altuser"}
        c = _normalise_comment(item)
        assert c["text"]   == "Fallback text"
        assert c["likes"]  == 3
        assert c["author"] == "@altuser"

    def test_empty_item(self):
        c = _normalise_comment({})
        assert c == {"text": "", "likes": 0, "author": ""}


# ════════════════════════════════════════════════════════════════════════════
# _group_comments_by_video
# ════════════════════════════════════════════════════════════════════════════

URL_LEXUS = "https://www.tiktok.com/@lexususa/video/1111"
URL_BMW   = "https://www.tiktok.com/@bmw/video/2222"
URL_GEN   = "https://www.tiktok.com/@genesis_usa/video/3333"


def _comment_item(text: str, source_field: str, source_value: str,
                  author: str = "u1") -> Dict:
    """Build a minimal Apify comment item dict."""
    return {
        "text":      text,
        "diggCount": 0,
        "uniqueId":  author,
        source_field: source_value,
    }


class TestGroupCommentsByVideo:
    def test_groups_by_videoWebUrl(self):
        items = [
            _comment_item("A",  "videoWebUrl", URL_LEXUS),
            _comment_item("B",  "videoWebUrl", URL_BMW),
            _comment_item("C",  "videoWebUrl", URL_LEXUS),
        ]
        result = _group_comments_by_video(items, [URL_LEXUS, URL_BMW], cap=30)
        assert len(result[URL_LEXUS]) == 2
        assert len(result[URL_BMW])   == 1

    def test_groups_by_submittedVideoUrl(self):
        items = [_comment_item("X", "submittedVideoUrl", URL_GEN)]
        result = _group_comments_by_video(items, [URL_GEN], cap=30)
        assert len(result[URL_GEN]) == 1

    def test_groups_by_postUrl(self):
        items = [_comment_item("Y", "postUrl", URL_BMW)]
        result = _group_comments_by_video(items, [URL_BMW], cap=30)
        assert len(result[URL_BMW]) == 1

    def test_groups_by_numeric_video_id_via_aweme_id(self):
        # Item has no URL field — only awemeId matching the ID in URL_LEXUS ("1111")
        item = {"text": "by id", "diggCount": 0, "uniqueId": "fan", "awemeId": "1111"}
        result = _group_comments_by_video([item], [URL_LEXUS], cap=30)
        assert len(result[URL_LEXUS]) == 1
        assert result[URL_LEXUS][0]["text"] == "by id"

    def test_groups_by_videoId_field(self):
        item = {"text": "videoId field", "diggCount": 0, "uniqueId": "u",
                "videoId": "2222"}
        result = _group_comments_by_video([item], [URL_BMW], cap=30)
        assert len(result[URL_BMW]) == 1

    def test_ungroupable_items_discarded_without_crash(self):
        # No URL field, no awemeId — can't be matched
        item = {"text": "mystery", "diggCount": 0, "uniqueId": "ghost"}
        result = _group_comments_by_video([item], [URL_LEXUS], cap=30)
        # Should not raise; no comment attached to URL_LEXUS
        assert result[URL_LEXUS] == []

    def test_cap_applied(self):
        items = [
            _comment_item(f"c{i}", "videoWebUrl", URL_LEXUS, author=f"u{i}")
            for i in range(10)
        ]
        result = _group_comments_by_video(items, [URL_LEXUS], cap=3)
        assert len(result[URL_LEXUS]) == 3

    def test_empty_items_list(self):
        result = _group_comments_by_video([], [URL_LEXUS, URL_BMW], cap=30)
        assert result[URL_LEXUS] == []
        assert result[URL_BMW]   == []

    def test_empty_submitted_urls(self):
        items = [_comment_item("hi", "videoWebUrl", URL_LEXUS)]
        result = _group_comments_by_video(items, [], cap=30)
        assert result == {}

    def test_all_urls_present_in_result(self):
        # Every submitted URL appears as a key even if it has no comments
        result = _group_comments_by_video([], [URL_LEXUS, URL_BMW, URL_GEN], cap=30)
        assert set(result.keys()) == {URL_LEXUS, URL_BMW, URL_GEN}

    def test_blank_text_items_skipped(self):
        items = [
            {"text": "", "diggCount": 0, "uniqueId": "u", "videoWebUrl": URL_LEXUS},
            _comment_item("real comment", "videoWebUrl", URL_LEXUS),
        ]
        result = _group_comments_by_video(items, [URL_LEXUS], cap=30)
        assert len(result[URL_LEXUS]) == 1
        assert result[URL_LEXUS][0]["text"] == "real comment"

    def test_mixed_url_fields_across_items(self):
        items = [
            _comment_item("by webUrl",   "videoWebUrl",       URL_LEXUS),
            _comment_item("by subUrl",   "submittedVideoUrl", URL_BMW),
            _comment_item("by postUrl",  "postUrl",           URL_GEN),
        ]
        result = _group_comments_by_video(
            items, [URL_LEXUS, URL_BMW, URL_GEN], cap=30
        )
        assert len(result[URL_LEXUS]) == 1
        assert len(result[URL_BMW])   == 1
        assert len(result[URL_GEN])   == 1

    def test_normalised_comments_have_correct_shape(self):
        items = [_comment_item("Nice!", "videoWebUrl", URL_LEXUS, author="fan99")]
        result = _group_comments_by_video(items, [URL_LEXUS], cap=30)
        c = result[URL_LEXUS][0]
        assert set(c.keys()) == {"text", "likes", "author"}
        assert c["text"]   == "Nice!"
        assert c["author"] == "@fan99"


# ════════════════════════════════════════════════════════════════════════════
# _extract_signals
# ════════════════════════════════════════════════════════════════════════════

def _mock_raw_with_signals() -> Dict:
    """
    Raw Apify item with musicMeta, hashtags, and videoMeta.duration populated.
    Reflects the real clockworks~tiktok-scraper item structure.
    """
    return {
        "id":           "9988776655",
        "webVideoUrl":  "https://www.tiktok.com/@lexususa/video/9988776655",
        "playCount":    200_000,
        "diggCount":    8_000,
        "commentCount": 250,
        "shareCount":   100,
        "musicMeta": {
            "musicId":       "987654",
            "musicName":     "Midnight Drive",
            "musicAuthor":   "SoundLab",
            "musicOriginal": False,
        },
        "hashtags": [
            {"id": "1", "name": "lexus"},
            {"id": "2", "name": "luxury"},
            {"id": "3", "name": "cars"},
        ],
        "videoMeta": {
            "duration":  28,
            "width":     1080,
            "height":    1920,
            "ratio":     "720p",
        },
        "authorMeta": {"name": "lexususa", "verified": True},
        "text": "Feel the road. #lexus #luxury #cars",
    }


class TestExtractSignals:
    def test_sound_fields_from_music_meta(self):
        raw = _mock_raw_with_signals()
        s = _extract_signals(raw)
        assert s["sound"]["id"]          == "987654"
        assert s["sound"]["title"]       == "Midnight Drive"
        assert s["sound"]["author"]      == "SoundLab"
        assert s["sound"]["is_original"] is False

    def test_is_original_true(self):
        raw = _mock_raw_with_signals()
        raw["musicMeta"]["musicOriginal"] = True
        s = _extract_signals(raw)
        assert s["sound"]["is_original"] is True

    def test_hashtags_extracted_from_list_of_dicts(self):
        raw = _mock_raw_with_signals()
        s = _extract_signals(raw)
        assert s["hashtags"] == ["lexus", "luxury", "cars"]

    def test_duration_sec_from_video_meta(self):
        raw = _mock_raw_with_signals()
        s = _extract_signals(raw)
        assert s["duration_sec"] == 28

    def test_missing_music_meta_returns_empty_sound(self):
        raw = _mock_raw_with_signals()
        del raw["musicMeta"]
        s = _extract_signals(raw)
        assert s["sound"] == {"id": "", "title": "", "author": "", "is_original": False}

    def test_none_music_meta_returns_empty_sound(self):
        raw = _mock_raw_with_signals()
        raw["musicMeta"] = None
        s = _extract_signals(raw)
        assert s["sound"]["id"] == ""

    def test_missing_hashtags_returns_empty_list(self):
        raw = _mock_raw_with_signals()
        del raw["hashtags"]
        s = _extract_signals(raw)
        assert s["hashtags"] == []

    def test_malformed_hashtag_entries_skipped(self):
        raw = _mock_raw_with_signals()
        # Mix of valid dicts, missing "name" key, and non-dict entries
        raw["hashtags"] = [
            {"id": "1", "name": "lexus"},
            {"id": "2"},          # no "name" key — skipped
            "notadict",           # not a dict — skipped
            {"name": "cars"},
        ]
        s = _extract_signals(raw)
        assert s["hashtags"] == ["lexus", "cars"]

    def test_missing_video_meta_duration_returns_zero(self):
        raw = _mock_raw_with_signals()
        del raw["videoMeta"]
        s = _extract_signals(raw)
        assert s["duration_sec"] == 0

    def test_video_meta_without_duration_key_returns_zero(self):
        raw = _mock_raw_with_signals()
        raw["videoMeta"] = {"width": 1080, "height": 1920}  # no duration
        s = _extract_signals(raw)
        assert s["duration_sec"] == 0

    def test_signals_key_present_in_transform_video(self):
        """Integration: transform_video output must include a 'signals' key."""
        record = transform_video(
            _mock_raw_with_signals(), "lexus", "@lexususa", "top"
        )
        assert "signals" in record

    def test_signals_shape_in_transform_video(self):
        """Integration: signals sub-object has the correct three keys."""
        record = transform_video(
            _mock_raw_with_signals(), "lexus", "@lexususa", "top"
        )
        sig = record["signals"]
        assert set(sig.keys()) == {"sound", "hashtags", "duration_sec"}
        assert set(sig["sound"].keys()) == {"id", "title", "author", "is_original"}

    def test_signals_populated_correctly_via_transform_video(self):
        """Integration: end-to-end values flow from raw item through to signals."""
        record = transform_video(
            _mock_raw_with_signals(), "lexus", "@lexususa", "top"
        )
        sig = record["signals"]
        assert sig["sound"]["title"]  == "Midnight Drive"
        assert sig["hashtags"]        == ["lexus", "luxury", "cars"]
        assert sig["duration_sec"]    == 28

    def test_signals_defensive_on_empty_raw(self):
        """Signals must never crash on a completely empty item."""
        s = _extract_signals({})
        assert s["sound"]        == {"id": "", "title": "", "author": "", "is_original": False}
        assert s["hashtags"]     == []
        assert s["duration_sec"] == 0


# ════════════════════════════════════════════════════════════════════════════
# Engagement tiering floor + source field (combined corpus behavior)
# ════════════════════════════════════════════════════════════════════════════

class TestEngagementTieringFloor:
    def test_top_excludes_below_floor(self):
        """A high-engagement video below the views floor must NOT be a top performer."""
        pool = [
            # Tiny reach but huge engagement rate: must be excluded from top.
            {"id": "micro", "playCount": 200, "diggCount": 100, "commentCount": 50,
             "shareCount": 50, "authorMeta": {}},
            {"id": "big1",  "playCount": 500_000, "diggCount": 40_000, "commentCount": 500,
             "shareCount": 300, "authorMeta": {}},
            {"id": "big2",  "playCount": 800_000, "diggCount": 20_000, "commentCount": 200,
             "shareCount": 100, "authorMeta": {}},
            {"id": "big3",  "playCount": 300_000, "diggCount":  3_000, "commentCount": 100,
             "shareCount":  50, "authorMeta": {}},
        ]
        top, _ = split_top_bottom(pool)
        top_ids = {v["id"] for v in top}
        assert "micro" not in top_ids, "below-floor micro video must not be a top performer"


class TestSourceField:
    def test_transform_default_source_is_profile(self):
        raw = _mock_raw_video()
        record = transform_video(raw, "lexus", "@lexususa", "top")
        assert record["source"] == "profile"

    def test_transform_reads_hashtag_source(self):
        raw = dict(_mock_raw_video())
        raw["source"] = "hashtag"
        record = transform_video(raw, "lexus", "@lexususa", "top")
        assert record["source"] == "hashtag"

    def test_index_record_carries_source(self):
        raw = dict(_mock_raw_video())
        raw["source"] = "hashtag"
        record = transform_video(raw, "lexus", "@lexususa", "bottom")
        idx = make_index_record(record)
        assert idx["source"] == "hashtag"
