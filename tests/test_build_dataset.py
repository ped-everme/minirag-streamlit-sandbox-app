"""
Unit tests for pipeline/build_dataset.py — ETL extraction, as-of lookup, deduplication.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.trend_radar.pipeline.build_dataset import (
    _normalize_row,
    as_of,
    build_index,
    extract_google_trends,
    extract_tiktok,
    extract_twitter,
    extract_youtube,
)


# ── _normalize_row ─────────────────────────────────────────────────────────────

class TestNormalizeRow:
    def test_true_becomes_one(self):
        assert _normalize_row({"is_mock": True})["is_mock"] == 1

    def test_false_becomes_zero(self):
        assert _normalize_row({"is_mock": False})["is_mock"] == 0

    def test_non_bool_values_unchanged(self):
        row = {"x": 42, "y": "hello", "z": None, "w": 3.14}
        assert _normalize_row(row) == row

    def test_mixed_row(self):
        row = {"val": 100, "flag": True, "name": "test"}
        result = _normalize_row(row)
        assert result["val"] == 100
        assert result["flag"] == 1
        assert result["name"] == "test"


# ── build_index ────────────────────────────────────────────────────────────────

class TestBuildIndex:
    def test_sorts_by_date(self):
        rows = [
            {"term_id": "a", "collected_at": "2026-03-01", "is_mock": True, "x": 1},
            {"term_id": "a", "collected_at": "2026-01-01", "is_mock": True, "x": 2},
        ]
        idx = build_index(rows)
        dates = [d for d, _ in idx["a"]]
        assert dates == sorted(dates)

    def test_real_data_overwrites_mock(self):
        rows = [
            {"term_id": "a", "collected_at": "2026-03-01", "is_mock": True,  "x": 99},
            {"term_id": "a", "collected_at": "2026-03-01", "is_mock": False, "x": 42},
        ]
        idx = build_index(rows)
        assert len(idx["a"]) == 1
        assert idx["a"][0][1]["x"] == 42

    def test_mock_does_not_overwrite_real(self):
        rows = [
            {"term_id": "a", "collected_at": "2026-03-01", "is_mock": False, "x": 42},
            {"term_id": "a", "collected_at": "2026-03-01", "is_mock": True,  "x": 99},
        ]
        idx = build_index(rows)
        assert idx["a"][0][1]["x"] == 42

    def test_different_dates_both_kept(self):
        rows = [
            {"term_id": "a", "collected_at": "2026-02-01", "is_mock": True, "x": 1},
            {"term_id": "a", "collected_at": "2026-03-01", "is_mock": True, "x": 2},
        ]
        idx = build_index(rows)
        assert len(idx["a"]) == 2

    def test_multiple_terms_separated(self):
        rows = [
            {"term_id": "a", "collected_at": "2026-03-01", "is_mock": True, "x": 1},
            {"term_id": "b", "collected_at": "2026-03-01", "is_mock": True, "x": 2},
        ]
        idx = build_index(rows)
        assert "a" in idx
        assert "b" in idx


# ── as_of ──────────────────────────────────────────────────────────────────────

class TestAsOf:
    def _index(self, dates):
        rows = [
            {"term_id": "t", "collected_at": d, "is_mock": True, "val": i}
            for i, d in enumerate(dates)
        ]
        return build_index(rows)

    def test_exact_match(self):
        idx = self._index(["2026-01-01", "2026-02-01", "2026-03-01"])
        row, date = as_of(idx, "t", "2026-02-01")
        assert date == "2026-02-01"
        assert row["val"] == 1

    def test_returns_most_recent_before_date(self):
        idx = self._index(["2026-01-01", "2026-03-01"])
        row, date = as_of(idx, "t", "2026-02-15")
        assert date == "2026-01-01"

    def test_returns_none_when_all_dates_after(self):
        idx = self._index(["2026-03-01", "2026-04-01"])
        row, date = as_of(idx, "t", "2026-01-01")
        assert row is None
        assert date is None

    def test_returns_none_for_unknown_term(self):
        idx = self._index(["2026-01-01"])
        row, date = as_of(idx, "unknown", "2026-06-01")
        assert row is None
        assert date is None

    def test_returns_latest_when_on_is_after_all(self):
        idx = self._index(["2026-01-01", "2026-02-01"])
        row, date = as_of(idx, "t", "2026-12-31")
        assert date == "2026-02-01"


# ── Extractors ─────────────────────────────────────────────────────────────────

class TestExtractTwitter:
    def _file(self, tmp_path, terms):
        import json
        p = tmp_path / "twitter_2026-04-29.json"
        p.write_text(json.dumps({
            "source": "twitter",
            "collected_at": "2026-04-29T12:00:00Z",
            "terms": terms,
        }))
        return p

    def test_basic_extraction(self, tmp_path):
        terms = [{
            "term_id": "x", "social_trend_name": "X",
            "tweet_count": 50, "avg_retweets": 2.5,
            "avg_likes": 10.0, "top_retweets": 20, "top_likes": 100,
        }]
        rows = extract_twitter(self._file(tmp_path, terms), is_mock=True)
        assert len(rows) == 1
        assert rows[0]["term_id"] == "x"
        assert rows[0]["tweet_count"] == 50
        assert rows[0]["avg_retweets"] == 2.5
        assert rows[0]["collected_at"] == "2026-04-29"
        assert rows[0]["is_mock"] is True

    def test_missing_fields_default_to_zero(self, tmp_path):
        rows = extract_twitter(self._file(tmp_path, [{"term_id": "x"}]), is_mock=False)
        assert rows[0]["tweet_count"] == 0
        assert rows[0]["avg_likes"] == 0


class TestExtractYouTube:
    def _file(self, tmp_path, terms):
        import json
        p = tmp_path / "youtube_2026-04-29.json"
        p.write_text(json.dumps({
            "source": "youtube",
            "collected_at": "2026-04-29T12:00:00Z",
            "terms": terms,
        }))
        return p

    def test_basic_extraction(self, tmp_path):
        terms = [{
            "term_id": "y", "social_trend_name": "Y",
            "windows": {
                "90d":  {"avg_views_per_day": 500,  "top_views_per_day": 5000,  "video_count": 10},
                "365d": {"avg_views_per_day": 300,  "top_views_per_day": 5000,  "video_count": 40},
            },
        }]
        rows = extract_youtube(self._file(tmp_path, terms), is_mock=False)
        assert rows[0]["avg_vpd_90d"] == 500
        assert rows[0]["avg_vpd_365d"] == 300
        assert rows[0]["top_vpd_90d"] == 5000

    def test_missing_windows_return_none(self, tmp_path):
        rows = extract_youtube(self._file(tmp_path, [{"term_id": "y", "windows": {}}]), is_mock=False)
        assert rows[0]["avg_vpd_90d"] is None
        assert rows[0]["avg_vpd_365d"] is None


class TestExtractGoogleTrends:
    def _file(self, tmp_path, terms):
        import json
        p = tmp_path / "google_trends_2026-04-29.json"
        p.write_text(json.dumps({
            "source": "google_trends",
            "collected_at": "2026-04-29T12:00:00Z",
            "terms": terms,
        }))
        return p

    def test_basic_extraction(self, tmp_path):
        terms = [{
            "term_id": "g", "social_trend_name": "G",
            "windows": {
                "90d":  {"low_data": False, "velocity": 0.2, "current_score": 80,
                         "avg_score": 60, "peak_score": 100},
                "365d": {"low_data": False, "velocity": 0.15, "avg_score": 50,
                         "peak_score": 90},
            },
        }]
        rows = extract_google_trends(self._file(tmp_path, terms), is_mock=False)
        assert rows[0]["velocity_90d"] == 0.2
        assert rows[0]["low_data_90d"] is False
        assert rows[0]["velocity_365d"] == 0.15
        assert rows[0]["peak_score_365d"] == 90

    def test_low_data_defaults_to_true(self, tmp_path):
        rows = extract_google_trends(
            self._file(tmp_path, [{"term_id": "g", "windows": {"90d": {}, "365d": {}}}]),
            is_mock=False,
        )
        assert rows[0]["low_data_90d"] is True
        assert rows[0]["low_data_365d"] is True


class TestExtractTikTok:
    def _file(self, tmp_path, terms):
        import json
        p = tmp_path / "tiktok_2026-04-29.json"
        p.write_text(json.dumps({
            "source": "tiktok",
            "collected_at": "2026-04-29T12:00:00Z",
            "terms": terms,
        }))
        return p

    def test_basic_extraction(self, tmp_path):
        terms = [{
            "term_id": "tt", "social_trend_name": "TT",
            "video_count": 10, "avg_plays": 50000, "top_plays": 200000,
            "avg_shares": 500, "avg_diggs": 2000, "avg_comments": 80,
        }]
        rows = extract_tiktok(self._file(tmp_path, terms), is_mock=True)
        assert rows[0]["avg_plays"] == 50000
        assert rows[0]["avg_shares"] == 500
        assert rows[0]["is_mock"] is True

    def test_missing_fields_default_to_zero(self, tmp_path):
        rows = extract_tiktok(
            self._file(tmp_path, [{"term_id": "tt"}]), is_mock=False
        )
        assert rows[0]["avg_plays"] == 0
        assert rows[0]["video_count"] == 0
