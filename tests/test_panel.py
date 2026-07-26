"""
Tests for the panel-side modules: gui_server helpers, derive's pure functions,
runstore archiving, and the timeline transforms.

Every bug fixed on 26 July 2026 lived in this layer, which had zero coverage
while the pipeline transforms had 150 tests. Nothing here touches the network:
the scraper and model calls stay mocked out by simply not being called.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import compare_runs
import derive
import gui_server
import runstore
import timeline


# ── gui_server helpers ───────────────────────────────────────────────────────

class TestPanelHelpers:
    def test_slug_strips_everything_but_alphanumerics(self) -> None:
        assert gui_server.slug("The North Face!") == "thenorthface"
        assert gui_server.slug("Arc'teryx") == "arcteryx"
        assert gui_server.slug("") == ""

    def test_norm_handle_adds_at_once(self) -> None:
        assert gui_server.norm_handle("nike") == "@nike"
        assert gui_server.norm_handle("@nike") == "@nike"
        assert gui_server.norm_handle("") == ""

    def test_template_brief_is_detected(self) -> None:
        template = (gui_server.CONFIG_DIR / "TEMPLATE-brief.md").read_text(encoding="utf-8")
        assert gui_server.brief_is_template(template)

    def test_written_brief_is_not_flagged(self) -> None:
        real = (gui_server.CONFIG_DIR / "lexus-brief.md").read_text(encoding="utf-8")
        assert not gui_server.brief_is_template(real)

    def test_log_drops_openssl_noise(self) -> None:
        with gui_server.JOB_LOCK:
            gui_server.JOB["log"] = []
        gui_server.log("something real")
        gui_server.log("  warnings.warn(")
        gui_server.log("urllib3/__init__.py:35: NotOpenSSLWarning: blah")
        assert gui_server.JOB["log"] == ["something real"]


# ── derive pure functions ────────────────────────────────────────────────────

class TestDerive:
    def test_looks_like_url(self) -> None:
        assert derive.looks_like_url("nike.com")
        assert derive.looks_like_url("https://patagonia.com/eu")
        assert not derive.looks_like_url("Nike")
        assert not derive.looks_like_url("the north face")

    def test_norm_handle(self) -> None:
        assert derive._norm_handle("rapha") == "@rapha"
        assert derive._norm_handle("") == ""


# ── runstore ─────────────────────────────────────────────────────────────────

class TestRunstore:
    def test_archives_analysis_signal_and_raw(self, tmp_path, monkeypatch) -> None:
        data = tmp_path / "data"
        (data / "raw").mkdir(parents=True)
        (data / "analysis.json").write_text(json.dumps({
            "videos": [{"id": "1", "brand": "acme"}],
        }))
        (tmp_path / "signal.json").write_text(json.dumps({
            "meta": {"subject": "Acme Corp"},
        }))
        (data / "raw" / "acme.json").write_text(json.dumps({"brand": "acme", "videos": []}))

        monkeypatch.setattr(runstore, "ROOT", tmp_path)
        monkeypatch.setattr(runstore, "DATA", data)
        monkeypatch.setattr(runstore, "ARCHIVE", tmp_path / "archive")

        dest = runstore.archive_previous_run()
        assert dest is not None
        assert (dest / "analysis.json").is_file()
        assert (dest / "signal.json").is_file()
        assert (dest / "acme.json").is_file()
        # the directory carries the subject slug, so the archive is browsable
        assert "acme-corp" in dest.name

    def test_restore_round_trip(self, tmp_path, monkeypatch) -> None:
        """Restore swaps an archived run in, after archiving the current one."""
        data = tmp_path / "data"
        (data / "raw").mkdir(parents=True)
        (tmp_path / "web").mkdir()
        monkeypatch.setattr(runstore, "ROOT", tmp_path)
        monkeypatch.setattr(runstore, "DATA", data)
        monkeypatch.setattr(runstore, "ARCHIVE", tmp_path / "archive")

        def write_state(subject: str) -> None:
            (data / "analysis.json").write_text(json.dumps(
                {"videos": [{"id": "1", "brand": subject}]}))
            sig = {"meta": {"subject": subject.title(), "brands": [subject], "n_videos": 1}}
            (tmp_path / "signal.json").write_text(json.dumps(sig))
            (tmp_path / "web" / "signal.json").write_text(json.dumps(sig))
            (tmp_path / "web" / "videos.json").write_text('{"videos": [{"id": "1"}]}')
            (data / "raw" / f"{subject}.json").write_text(json.dumps(
                {"brand": subject, "videos": []}))

        write_state("acme")
        archived = runstore.archive_previous_run()
        assert archived is not None and (archived / "videos.json").is_file()

        write_state("globex")                       # a different live run
        message = runstore.restore_run(archived.name)
        assert "Acme" in message
        live = json.loads((tmp_path / "web" / "signal.json").read_text())
        assert live["meta"]["subject"] == "Acme"
        # the globex state was archived by the restore, so nothing was lost
        snapshots = list((tmp_path / "archive").iterdir())
        assert any("globex" in d.name for d in snapshots)

    def test_restore_rejects_traversal_and_unknown(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(runstore, "ROOT", tmp_path)
        monkeypatch.setattr(runstore, "DATA", tmp_path / "data")
        monkeypatch.setattr(runstore, "ARCHIVE", tmp_path / "archive")
        with pytest.raises(ValueError):
            runstore.restore_run("../outside")
        with pytest.raises(ValueError):
            runstore.restore_run("nope")

    def test_nothing_to_archive_returns_none(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(runstore, "ROOT", tmp_path)
        monkeypatch.setattr(runstore, "DATA", tmp_path / "data")
        monkeypatch.setattr(runstore, "ARCHIVE", tmp_path / "archive")
        assert runstore.archive_previous_run() is None


# ── timeline transforms ──────────────────────────────────────────────────────

def _item(iso: str, views=1000, likes=100, comments=10, shares=5, vid="v1") -> dict:
    return {
        "id": vid, "createTimeISO": iso, "webVideoUrl": f"https://t/{vid}",
        "playCount": views, "diggCount": likes, "commentCount": comments,
        "shareCount": shares,
    }


class TestTimeline:
    def test_sentiment_lexicon_reads_the_obvious(self) -> None:
        assert timeline.score_comment("this is amazing, love it ❤") == 1
        assert timeline.score_comment("worst drop ever, overpriced trash") == -1
        assert timeline.score_comment("interesting choice") == 0
        assert timeline.score_comment("") == 0

    def test_bucket_key_month_and_quarter(self) -> None:
        dt = datetime(2026, 5, 12, tzinfo=timezone.utc)
        assert timeline.bucket_key(dt, quarterly=False) == "2026-05"
        assert timeline.bucket_key(dt, quarterly=True) == "2026-Q2"

    def test_created_at_prefers_iso_and_survives_garbage(self) -> None:
        assert timeline._created_at({"createTimeISO": "2026-01-02T03:04:05Z"}).year == 2026
        assert timeline._created_at({"createTime": "not a number"}) is None
        assert timeline._created_at({}) is None

    def test_build_timeline_buckets_and_math(self, monkeypatch) -> None:
        # no comments scraped: the sentiment block must degrade, not crash
        monkeypatch.setattr(timeline, "scrape_comments_batch", lambda *a, **k: {})
        items = [
            _item("2026-01-10T00:00:00Z", views=1000, likes=80, comments=15, shares=5, vid="a"),
            _item("2026-01-20T00:00:00Z", views=2000, likes=100, comments=50, shares=50, vid="b"),
            _item("2026-02-05T00:00:00Z", views=500, likes=50, comments=0, shares=0, vid="c"),
        ]
        out = timeline.build_timeline(items, comment_videos=2, comment_cap=5)
        assert out["granularity"] == "month"
        assert [b["period"] for b in out["buckets"]] == ["2026-01", "2026-02"]
        jan = out["buckets"][0]
        # (80+15+5)/1000*100 = 10.0 and (100+50+50)/2000*100 = 10.0 → avg 10.0
        assert jan["avg_engagement_rate"] == pytest.approx(10.0)
        assert jan["n_posts"] == 2
        assert jan["sentiment"]["score"] is None    # nothing sampled, no fake zero

    def test_stratified_sampling_covers_every_bucket(self, monkeypatch) -> None:
        captured: dict = {}

        def fake_comments(key, urls, cap=12):
            captured["urls"] = urls
            return {u: [] for u in urls}

        monkeypatch.setattr(timeline, "scrape_comments_batch", fake_comments)
        # three months, very uneven cadence: 1 + 1 + 10 posts
        items = [_item("2026-01-15T00:00:00Z", vid="jan")] + \
                [_item("2026-02-15T00:00:00Z", vid="feb")] + \
                [_item(f"2026-03-{d:02d}T00:00:00Z", vid=f"mar{d}") for d in range(1, 11)]
        timeline.build_timeline(items, comment_videos=4, comment_cap=5)
        sampled = captured["urls"]
        # the quiet months must still be sampled: that was the uniform-stride bug
        assert any("jan" in u for u in sampled)
        assert any("feb" in u for u in sampled)


# ── compare_runs ─────────────────────────────────────────────────────────────

def _signal(subject, n, owner, traits, patterns):
    return {
        "meta": {"subject": subject, "n_videos": n, "generated_at": "2026-07-26T00:00:00Z"},
        "insight": {
            "owners_vs_brand": {"owner_share_pct": owner},
            "brand_gap": [
                {"category_winning_trait": k, "brand_fit_score": v, "brand_today": ""}
                for k, v in traits.items()
            ],
        },
        "segnale": {"winning_patterns": [{"pattern": k, "avg_engagement": v} for k, v in patterns.items()]},
    }


class TestCompareRuns:
    def test_deltas_and_rotation(self) -> None:
        a = _signal("Acme", 100, 60.0, {"owner pov": 0.5, "held pacing": 0.7}, {"macro": 8.0})
        b = _signal("Acme", 110, 72.5, {"owner pov": 0.8, "trending sound": 0.4}, {"silent build": 9.0})
        d = compare_runs.compare(a, b)
        assert d["owner_share"]["delta"] == 12.5
        common = {c["trait"]: c for c in d["gap"]["common"]}
        assert common["owner pov"]["fit_delta"] == 0.3
        assert d["gap"]["appeared"] == ["trending sound"]
        assert d["gap"]["disappeared"] == ["held pacing"]
        assert d["patterns"]["appeared"] == ["silent build"]
        assert d["patterns"]["disappeared"] == ["macro"]
        assert d["size_caveat"] == ""

    def test_size_caveat_fires_on_mismatched_corpora(self) -> None:
        a = _signal("Acme", 6, 10.0, {}, {})
        b = _signal("Acme", 126, 80.0, {}, {})
        d = compare_runs.compare(a, b)
        assert "direction, not measurement" in d["size_caveat"]

    def test_different_subjects_refuse(self) -> None:
        with pytest.raises(ValueError):
            compare_runs.compare(_signal("Acme", 5, 0, {}, {}), _signal("Globex", 5, 0, {}, {}))

    def test_render_text_is_complete(self) -> None:
        a = _signal("Acme", 100, 60.0, {"owner pov": 0.5}, {})
        b = _signal("Acme", 100, 70.0, {"owner pov": 0.9}, {})
        text = compare_runs.render_text(compare_runs.compare(a, b))
        assert "owner share of engagement: 60.0% → 70.0%" in text
        assert "fit +0.40" in text
