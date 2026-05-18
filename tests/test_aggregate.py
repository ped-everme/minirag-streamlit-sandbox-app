"""
Unit tests for pipeline/aggregate.py — scoring, classification, normalisation.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.trend_radar.pipeline.aggregate import (
    classify,
    compute_derived,
    count_platforms,
    normalise_batch,
    weighted_score,
)

RUN_DATE = datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)


# ── normalise_batch ────────────────────────────────────────────────────────────

class TestNormaliseBatch:
    def test_basic_range(self):
        metrics = [{"x": 0}, {"x": 5}, {"x": 10}]
        result = normalise_batch(metrics, ["x"])
        assert result[0]["x"] == 0.0
        assert result[1]["x"] == 0.5
        assert result[2]["x"] == 1.0

    def test_all_same_value_returns_half(self):
        metrics = [{"x": 7}, {"x": 7}, {"x": 7}]
        result = normalise_batch(metrics, ["x"])
        assert all(r["x"] == 0.5 for r in result)

    def test_nulls_excluded_from_min_max(self):
        metrics = [{"x": None}, {"x": 0}, {"x": 10}]
        result = normalise_batch(metrics, ["x"])
        assert result[0]["x"] is None
        assert result[1]["x"] == 0.0
        assert result[2]["x"] == 1.0

    def test_all_null_produces_no_output(self):
        metrics = [{"x": None}, {"x": None}]
        result = normalise_batch(metrics, ["x"])
        assert result[0] == {}
        assert result[1] == {}

    def test_single_non_null_gets_half(self):
        metrics = [{"x": None}, {"x": 42}]
        result = normalise_batch(metrics, ["x"])
        assert result[0]["x"] is None
        assert result[1]["x"] == 0.5

    def test_multiple_keys_independent(self):
        metrics = [{"x": 0, "y": 10}, {"x": 10, "y": 0}]
        result = normalise_batch(metrics, ["x", "y"])
        assert result[0]["x"] == 0.0
        assert result[0]["y"] == 1.0
        assert result[1]["x"] == 1.0
        assert result[1]["y"] == 0.0


# ── weighted_score ─────────────────────────────────────────────────────────────

class TestWeightedScore:
    def test_all_inputs_present(self):
        norm = {"a": 1.0, "b": 0.0}
        weights = {"a": 0.6, "b": 0.4}
        assert weighted_score(norm, weights) == 0.6

    def test_null_inputs_redistribute_weight(self):
        # "b" is null — weight should go fully to "a"
        norm = {"a": 1.0, "b": None}
        weights = {"a": 0.5, "b": 0.5}
        assert weighted_score(norm, weights) == 1.0

    def test_all_null_returns_none(self):
        norm = {"a": None, "b": None}
        weights = {"a": 0.5, "b": 0.5}
        assert weighted_score(norm, weights) is None

    def test_partial_null_correct_average(self):
        norm = {"a": 0.8, "b": None, "c": 0.4}
        weights = {"a": 0.4, "b": 0.2, "c": 0.4}
        # effective weights: a=0.4/0.8=0.5, c=0.4/0.8=0.5
        expected = round((0.8 * 0.4 + 0.4 * 0.4) / (0.4 + 0.4), 4)
        assert weighted_score(norm, weights) == expected

    def test_zero_value_is_not_null(self):
        norm = {"a": 0.0, "b": 1.0}
        weights = {"a": 0.5, "b": 0.5}
        assert weighted_score(norm, weights) == 0.5


# ── count_platforms ────────────────────────────────────────────────────────────

class TestCountPlatforms:
    def _raw(self, yt=None, gt_vel=None, gt_low=True, tw=None, tt=None):
        return {
            "yt_top_vpd_365d":  yt,
            "gt_velocity_90d":  gt_vel,
            "gt_velocity_365d": None,
            "gt_low_data":      gt_low,
            "tw_avg_retweets":  tw,
            "tt_avg_plays":     tt,
        }

    def test_all_four_platforms(self):
        raw = self._raw(yt=1000, gt_vel=0.2, gt_low=False, tw=5.0, tt=50000)
        assert count_platforms(raw) == 4

    def test_no_data_returns_zero(self):
        raw = self._raw()
        assert count_platforms(raw) == 0

    def test_gt_low_data_not_counted(self):
        raw = self._raw(yt=1000, gt_low=True, tw=5.0, tt=50000)
        assert count_platforms(raw) == 3

    def test_gt_counted_when_has_velocity(self):
        raw = self._raw(yt=1000, gt_vel=0.1, gt_low=False, tw=5.0, tt=50000)
        assert count_platforms(raw) == 4

    def test_twitter_null_not_counted(self):
        raw = self._raw(yt=1000, gt_vel=0.1, gt_low=False, tt=50000)
        assert count_platforms(raw) == 3


# ── compute_derived ────────────────────────────────────────────────────────────

class TestComputeDerived:
    def _raw(self, top90=None, top365=None, avg90=None, avg365=None,
             cur90=None, avg365_gt=None, peak_date=None):
        return {
            "yt_top_vpd_90d":  top90,
            "yt_top_vpd_365d": top365,
            "yt_avg_vpd_90d":  avg90,
            "yt_avg_vpd_365d": avg365,
            "gt_current_90d":  cur90,
            "gt_avg_365d":     avg365_gt,
            "gt_peak_date_90d": peak_date,
        }

    def test_yt_peak_ratio(self):
        raw = self._raw(top90=500, top365=1000)
        d = compute_derived(raw, RUN_DATE)
        assert d["yt_peak_ratio"] == 0.5

    def test_yt_peak_ratio_null_when_365d_missing(self):
        raw = self._raw(top90=500, top365=None)
        d = compute_derived(raw, RUN_DATE)
        assert d["yt_peak_ratio"] is None

    def test_yt_momentum(self):
        raw = self._raw(avg90=200, avg365=100)
        d = compute_derived(raw, RUN_DATE)
        assert d["yt_momentum"] == 2.0

    def test_gt_above_baseline(self):
        raw = self._raw(cur90=80, avg365_gt=40)
        d = compute_derived(raw, RUN_DATE)
        assert d["gt_above_baseline"] == 2.0

    def test_gt_above_baseline_null_when_avg_zero(self):
        raw = self._raw(cur90=80, avg365_gt=0)
        d = compute_derived(raw, RUN_DATE)
        assert d["gt_above_baseline"] is None

    def test_gt_days_since_peak(self):
        raw = self._raw(peak_date="2026-04-19")
        d = compute_derived(raw, RUN_DATE)
        assert d["gt_days_since_peak"] == 10

    def test_gt_days_since_peak_null_when_no_date(self):
        raw = self._raw()
        d = compute_derived(raw, RUN_DATE)
        assert d["gt_days_since_peak"] is None


# ── classify ───────────────────────────────────────────────────────────────────

class TestClassify:
    def _raw_full(self, gt_low=False, gt_vel_365=-0.0, yt_top90=500, yt_top365=1000):
        return {
            "gt_low_data":      gt_low,
            "gt_velocity_365d": gt_vel_365,
            "yt_top_vpd_90d":   yt_top90,
            "yt_top_vpd_365d":  yt_top365,
        }

    def test_unclassified_when_fewer_than_two_platforms(self):
        # Only one platform has data (Twitter only — no YouTube, GT all low, no TikTok)
        raw = {
            "gt_low_data": True, "gt_velocity_90d": None, "gt_velocity_365d": None,
            "yt_top_vpd_365d": None,
            "tw_avg_retweets": 5.0,
            "tt_avg_plays": None,
        }
        assert classify(0.9, 0.9, raw, {}) == "UNCLASSIFIED"

    def test_platform_native_hyped(self):
        raw = self._raw_full(gt_low=True)
        raw["platforms_with_data"] = 2
        norm = {"tt_avg_shares": 0.8}
        result = classify(0.3, 0.3, raw, norm)
        # Need to patch count_platforms indirectly — use raw with 2+ platforms
        # count_platforms checks yt_top_vpd_365d and tt_avg_plays
        raw2 = {
            "gt_low_data":      True,
            "gt_velocity_90d":  None,
            "gt_velocity_365d": None,
            "yt_top_vpd_365d":  1000,
            "tw_avg_retweets":  None,
            "tt_avg_plays":     50000,
        }
        assert classify(0.3, 0.3, raw2, {"tt_avg_shares": 0.8}) == "HYPED (platform-native)"

    def test_hyped_and_emerging(self):
        raw = {
            "gt_low_data": False, "gt_velocity_365d": 0.1,
            "yt_top_vpd_365d": 1000, "yt_top_vpd_90d": 500,
            "tw_avg_retweets": 5.0, "tt_avg_plays": 50000,
        }
        assert classify(0.8, 0.7, raw, {}) == "HYPED + EMERGING"

    def test_hyped_only(self):
        raw = {
            "gt_low_data": False, "gt_velocity_365d": 0.1,
            "yt_top_vpd_365d": 1000, "yt_top_vpd_90d": 500,
            "tw_avg_retweets": 5.0, "tt_avg_plays": 50000,
        }
        assert classify(0.8, 0.3, raw, {}) == "HYPED"

    def test_emerging_only(self):
        raw = {
            "gt_low_data": False, "gt_velocity_365d": 0.1,
            "yt_top_vpd_365d": 1000, "yt_top_vpd_90d": 500,
            "tw_avg_retweets": 5.0, "tt_avg_plays": 50000,
        }
        assert classify(0.3, 0.8, raw, {}) == "EMERGING"

    def test_declining(self):
        raw = {
            "gt_low_data": False, "gt_velocity_365d": -0.20,
            "yt_top_vpd_365d": 1000, "yt_top_vpd_90d": 200,
            "tw_avg_retweets": 5.0, "tt_avg_plays": 50000,
        }
        assert classify(0.3, 0.3, raw, {}) == "DECLINING"

    def test_declining_requires_both_conditions(self):
        # velocity bad but peak ratio ok — not DECLINING
        raw = {
            "gt_low_data": False, "gt_velocity_365d": -0.20,
            "yt_top_vpd_365d": 1000, "yt_top_vpd_90d": 800,
            "tw_avg_retweets": 5.0, "tt_avg_plays": 50000,
        }
        assert classify(0.3, 0.3, raw, {}) == "ESTABLISHED"

    def test_established_is_fallback(self):
        raw = {
            "gt_low_data": False, "gt_velocity_365d": 0.0,
            "yt_top_vpd_365d": 1000, "yt_top_vpd_90d": 800,
            "tw_avg_retweets": 5.0, "tt_avg_plays": 50000,
        }
        assert classify(0.3, 0.3, raw, {}) == "ESTABLISHED"

    def test_threshold_boundary_is_exclusive(self):
        raw = {
            "gt_low_data": False, "gt_velocity_365d": 0.0,
            "yt_top_vpd_365d": 1000, "yt_top_vpd_90d": 800,
            "tw_avg_retweets": 5.0, "tt_avg_plays": 50000,
        }
        # exactly 0.5 is NOT above threshold
        assert classify(0.5, 0.5, raw, {}) == "ESTABLISHED"
        # just above → HYPED + EMERGING
        assert classify(0.51, 0.51, raw, {}) == "HYPED + EMERGING"
