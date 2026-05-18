#!/usr/bin/env python3
"""
Generate mock historical data for trend-radar analysis.

Creates:
  - 26 weekly Twitter JSONs       (2025-11-02 through 2026-04-19)
  - 6 monthly YouTube JSONs       (2025-11-01 through 2026-04-01)
  - 4 Google Trends JSONs         (2025-11-15, 2026-01-01, 2026-02-15, 2026-03-25)
  - 12 bi-weekly TikTok JSONs     (2025-11-01 through 2026-04-15)

All files go to src/trend_radar/data/mock/ and are clearly marked as MOCK DATA.
Values are scaled from the real April 29/30 baselines using per-term
trajectory patterns, so the data tells a realistic story.
"""

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(42)

_BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = _BASE_DIR / "data" / "mock"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Trajectory patterns ────────────────────────────────────────────────────────
# Each term's history leading up to the April 29 "current" value.
# Factor = multiplier applied to the baseline (current) value.

TRAJECTORIES = {
    "wolverine-stack":   "explosive",         # flat then sudden spike
    "fiber-maxing":      "peaked_declining",  # peaked 2 months ago, fading
    "cortisol-face":     "peaked_declining",  # was bigger hype, settling
    "mouth-taping":      "declining",         # steadily losing steam
    "cold-plunge":       "stable",            # established, not trending
    "glp1-revolution":   "growing",           # medical legitimacy building
    "urolithin-a":       "growing",           # emerging supplement
    "akkermansia":       "growing",           # gut health niche growing
    "methylene-blue":    "declining",         # hype peak was earlier
    "peptide-therapy":   "growing",           # clinical interest rising
    "red-light-therapy": "growing",           # consistent long-term growth
    "spermidine":        "growing_slow",      # niche, slow build
}


def jitter(val, pct=0.12):
    """Add ±pct random noise to val."""
    if val is None:
        return None
    return val * (1 + random.uniform(-pct, pct))


def factor(traj: str, t: float) -> float:
    """
    Return a scale factor at fractional time t ∈ [0, 1].
    t=0 = oldest point, t=1 = present (April 2026 baseline).
    """
    if traj == "growing":
        return 0.30 + 0.70 * t
    elif traj == "growing_slow":
        return 0.55 + 0.45 * t
    elif traj == "declining":
        return 1.70 - 0.70 * t
    elif traj == "stable":
        return 0.92 + 0.16 * math.sin(t * math.pi)  # gentle wave ±8%
    elif traj == "peaked_declining":
        peak = 0.30
        if t < peak:
            return 0.90 + (1.90 - 0.90) * (t / peak)
        else:
            return 1.90 - (1.90 - 1.0) * ((t - peak) / (1.0 - peak))
    elif traj == "explosive":
        inflection = 0.70
        if t < inflection:
            return 0.10 + 0.15 * (t / inflection)
        else:
            k = (t - inflection) / (1.0 - inflection)
            return 0.25 + 0.75 * (k ** 1.5)
    return 1.0


def scaled(base, traj, t, pct=0.12, floor=0):
    """Apply trajectory factor + noise, floor at 'floor'."""
    if base is None or base == 0:
        return base
    v = base * factor(traj, t)
    v = jitter(v, pct)
    return max(floor, round(v))


def scaled_f(base, traj, t, pct=0.10, floor=None):
    """Float version of scaled()."""
    if base is None:
        return None
    v = base * factor(traj, t)
    v = jitter(v, pct)
    if floor is not None:
        v = max(floor, v)
    return round(v, 3)


# ── Terms metadata ─────────────────────────────────────────────────────────────

with open(_BASE_DIR / "data" / "mock" / "terms.json", encoding="utf-8") as f:
    TERMS = json.load(f)

TERM_META = {t["id"]: t for t in TERMS}

# ── Twitter baselines (from 2026-04-29 real file) ─────────────────────────────

TW_BASE = {
    "wolverine-stack":   {"tweet_count": 193, "avg_retweets": 0.44, "avg_likes": 5.4, "top_retweets": 41, "top_likes": 248},
    "fiber-maxing":      {"tweet_count":  31, "avg_retweets": 0.32, "avg_likes": 23.2, "top_retweets": 5, "top_likes": 420},
    "cortisol-face":     {"tweet_count":  98, "avg_retweets": 3.6,  "avg_likes": 37.1, "top_retweets": 92, "top_likes": 890},
    "mouth-taping":      {"tweet_count":  89, "avg_retweets": 0.45, "avg_likes": 1.3, "top_retweets": 11, "top_likes": 46},
    "cold-plunge":       {"tweet_count":  98, "avg_retweets": 0.38, "avg_likes": 5.2, "top_retweets": 14, "top_likes": 128},
    "glp1-revolution":   {"tweet_count":  11, "avg_retweets": 1.2,  "avg_likes": 2.1, "top_retweets": 8, "top_likes": 35},
    "urolithin-a":       {"tweet_count":  57, "avg_retweets": 0.18, "avg_likes": 1.4, "top_retweets": 4, "top_likes": 38},
    "akkermansia":       {"tweet_count":  72, "avg_retweets": 1.1,  "avg_likes": 4.1, "top_retweets": 22, "top_likes": 89},
    "methylene-blue":    {"tweet_count": 109, "avg_retweets": 2.8,  "avg_likes": 8.3, "top_retweets": 71, "top_likes": 310},
    "peptide-therapy":   {"tweet_count": 121, "avg_retweets": 0.35, "avg_likes": 2.8, "top_retweets": 18, "top_likes": 94},
    "red-light-therapy": {"tweet_count":  98, "avg_retweets": 1.1,  "avg_likes": 6.1, "top_retweets": 31, "top_likes": 182},
    "spermidine":        {"tweet_count":  30, "avg_retweets": 2.1,  "avg_likes": 21.4, "top_retweets": 28, "top_likes": 245},
}

# ── YouTube baselines ─────────────────────────────────────────────────────────

YT_BASE = {
    "wolverine-stack":   {"avg_vpd_90": 580,  "top_vpd_90": 50283, "avg_vpd_365": 659,  "top_vpd_365": 50283},
    "fiber-maxing":      {"avg_vpd_90": 239,  "top_vpd_90": 4875,  "avg_vpd_365": 643,  "top_vpd_365": 16400},
    "cortisol-face":     {"avg_vpd_90": 5237, "top_vpd_90": 232364,"avg_vpd_365": 3307, "top_vpd_365": 232364},
    "mouth-taping":      {"avg_vpd_90": 4775, "top_vpd_90": 661691,"avg_vpd_365": 9532, "top_vpd_365": 661691},
    "cold-plunge":       {"avg_vpd_90": 9372, "top_vpd_90": 585530,"avg_vpd_365": 9808, "top_vpd_365": 585530},
    "glp1-revolution":   {"avg_vpd_90": 428,  "top_vpd_90": 18676, "avg_vpd_365": 609,  "top_vpd_365": 18676},
    "urolithin-a":       {"avg_vpd_90": 505,  "top_vpd_90": 15387, "avg_vpd_365": 413,  "top_vpd_365": 15387},
    "akkermansia":       {"avg_vpd_90": 1379, "top_vpd_90": 103166,"avg_vpd_365": 1136, "top_vpd_365": 103166},
    "methylene-blue":    {"avg_vpd_90": 503,  "top_vpd_90": 14046, "avg_vpd_365": 388,  "top_vpd_365": 14046},
    "peptide-therapy":   {"avg_vpd_90": 596,  "top_vpd_90": 50283, "avg_vpd_365": 707,  "top_vpd_365": 50283},
    "red-light-therapy": {"avg_vpd_90": 1767, "top_vpd_90": 59271, "avg_vpd_365": 1326, "top_vpd_365": 59271},
    "spermidine":        {"avg_vpd_90": 327,  "top_vpd_90": 14531, "avg_vpd_365": 472,  "top_vpd_365": 14531},
}

# ── Google Trends baselines ───────────────────────────────────────────────────

GT_BASE = {
    "wolverine-stack":   {"vel_90": 0.216, "cur_90": 86, "avg_90": 64, "vel_365": 0.398, "avg_365": 33, "low_data": False},
    "fiber-maxing":      {"vel_90": 0.0,   "cur_90": 0,  "avg_90": 0,  "vel_365": 0.0,   "avg_365": 0,  "low_data": True},
    "cortisol-face":     {"vel_90":-0.015, "cur_90": 26, "avg_90": 48, "vel_365": 0.056, "avg_365": 68, "low_data": False},
    "mouth-taping":      {"vel_90":-0.003, "cur_90": 45, "avg_90": 52, "vel_365":-0.069, "avg_365": 57, "low_data": False},
    "cold-plunge":       {"vel_90": 0.112, "cur_90": 44, "avg_90": 38, "vel_365":-0.016, "avg_365": 68, "low_data": False},
    "glp1-revolution":   {"vel_90": 0.102, "cur_90": 51, "avg_90": 28, "vel_365": 0.153, "avg_365": 9,  "low_data": False},
    "urolithin-a":       {"vel_90": 0.245, "cur_90": 41, "avg_90": 24, "vel_365": 0.179, "avg_365": 38, "low_data": False},
    "akkermansia":       {"vel_90": 0.092, "cur_90": 42, "avg_90": 35, "vel_365": 0.078, "avg_365": 52, "low_data": False},
    "methylene-blue":    {"vel_90":-0.033, "cur_90": 56, "avg_90": 61, "vel_365":-0.224, "avg_365": 66, "low_data": False},
    "peptide-therapy":   {"vel_90": 0.031, "cur_90": 36, "avg_90": 30, "vel_365": 0.353, "avg_365": 49, "low_data": False},
    "red-light-therapy": {"vel_90": 0.105, "cur_90": 52, "avg_90": 44, "vel_365": 0.350, "avg_365": 49, "low_data": False},
    "spermidine":        {"vel_90": 0.144, "cur_90": 28, "avg_90": 20, "vel_365": 0.162, "avg_365": 42, "low_data": False},
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fake_tweet_id(base: int) -> str:
    return str(base + random.randint(0, 9_999_999))


TWEET_TEMPLATES = {
    "wolverine-stack":   ["trying the wolverine stack for my shoulder injury", "bpc157 tb500 combo is wild, week 3 update"],
    "fiber-maxing":      ["fiber-maxxing week 2, gut feeling better", "30g fiber challenge, sharing my results"],
    "cortisol-face":     ["is cortisol face real? my transformation", "cortisol face glow-up after fixing my sleep"],
    "mouth-taping":      ["mouth taping for 30 days — sleep quality update", "stopped snoring with mouth tape"],
    "cold-plunge":       ["cold plunge every day this week, sharing protocol", "ice bath benefits after 3 months"],
    "glp1-revolution":   ["glp1 is changing how we think about obesity", "semaglutide 6 month update"],
    "urolithin-a":       ["urolithin a supplement stack for mitochondria", "mitopure review after 60 days"],
    "akkermansia":       ["akkermansia probiotic results", "gut microbiome protocol featuring akkermansia"],
    "methylene-blue":    ["methylene blue cognitive boost protocol", "mb nootropic stack review"],
    "peptide-therapy":   ["peptide therapy overview from my anti-aging doc", "healing faster with peptides post-surgery"],
    "red-light-therapy": ["red light therapy panel setup guide", "30 day red light results skin and recovery"],
    "spermidine":        ["spermidine autophagy longevity protocol", "wheat germ extract spermidine dosing"],
}


def make_tweets(term_id: str, count: int, window_start: datetime, avg_likes: float, avg_rt: float) -> list[dict]:
    templates = TWEET_TEMPLATES.get(term_id, ["trending health topic discussion"])
    tweets = []
    base_id = random.randint(1_900_000_000_000_000_000, 2_000_000_000_000_000_000)
    for i in range(min(count, 3)):
        likes = max(0, round(jitter(avg_likes * random.uniform(0.5, 2.5), 0.2)))
        rts   = max(0, round(jitter(avg_rt   * random.uniform(0.5, 2.5), 0.2)))
        created = window_start + timedelta(hours=random.randint(1, 160))
        tweets.append({
            "tweet_id":      str(base_id + i * 1000 + random.randint(0, 999)),
            "text":          templates[i % len(templates)],
            "author_id":     str(random.randint(100_000_000, 999_999_999)),
            "created_at":    created.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "like_count":    likes,
            "retweet_count": rts,
            "reply_count":   max(0, round(likes * 0.15)),
            "quote_count":   max(0, round(rts * 0.1)),
            "engagement":    likes + rts,
            "matched_query": TERM_META[term_id]["social_trend_name"],
        })
    return tweets


def make_yt_videos(term_id: str, top_vpd: int, n: int = 2) -> list[dict]:
    meta = TERM_META[term_id]
    videos = []
    for i in range(n):
        vpd = round(top_vpd * random.uniform(0.1, 1.0))
        days = random.randint(5, 85)
        videos.append({
            "video_id": f"mock_{term_id[:6]}_{i}",
            "title": f"{meta['social_trend_name']} — video {i+1}",
            "channel_title": "Mock Channel",
            "days_since_publish": days,
            "view_count": vpd * days,
            "views_per_day": vpd,
            "matched_query": meta["social_trend_name"],
        })
    return videos


def make_gt_iot(start_date: datetime, n_weeks: int, avg_score: float) -> list[dict]:
    iot = []
    for w in range(n_weeks):
        week_date = start_date + timedelta(weeks=w)
        score = max(0, min(100, round(jitter(avg_score, 0.25))))
        iot.append({
            "date": week_date.strftime("%Y-%m-%d"),
            "score": score,
        })
    return iot


# ── TikTok baselines (from 2026-04-30 real file) ─────────────────────────────

TT_BASE = {
    "wolverine-stack":   {"avg_plays": 132733,  "top_plays": 514100,   "avg_shares": 1393, "avg_diggs": 4830,  "avg_comments": 130},
    "fiber-maxing":      {"avg_plays": 237901,  "top_plays": 1300000,  "avg_shares": 871,  "avg_diggs": 3100,  "avg_comments": 88},
    "cortisol-face":     {"avg_plays": 2577816, "top_plays": 15300000, "avg_shares": 6717, "avg_diggs": 85000, "avg_comments": 2100},
    "mouth-taping":      {"avg_plays": 770103,  "top_plays": 4700000,  "avg_shares": 3949, "avg_diggs": 28000, "avg_comments": 710},
    "cold-plunge":       {"avg_plays": 1717474, "top_plays": 8900000,  "avg_shares": 995,  "avg_diggs": 42000, "avg_comments": 1100},
    "glp1-revolution":   {"avg_plays": 493084,  "top_plays": 3200000,  "avg_shares": 2544, "avg_diggs": 19000, "avg_comments": 480},
    "urolithin-a":       {"avg_plays": 93702,   "top_plays": 467100,   "avg_shares": 168,  "avg_diggs": 3200,  "avg_comments": 72},
    "akkermansia":       {"avg_plays": 34582,   "top_plays": 119700,   "avg_shares": 414,  "avg_diggs": 1400,  "avg_comments": 55},
    "methylene-blue":    {"avg_plays": 149560,  "top_plays": 490600,   "avg_shares": 1716, "avg_diggs": 6100,  "avg_comments": 190},
    "peptide-therapy":   {"avg_plays": 217378,  "top_plays": 564400,   "avg_shares": 1831, "avg_diggs": 7800,  "avg_comments": 220},
    "red-light-therapy": {"avg_plays": 653190,  "top_plays": 4600000,  "avg_shares": 1776, "avg_diggs": 22000, "avg_comments": 560},
    "spermidine":        {"avg_plays": 34260,   "top_plays": 207100,   "avg_shares": 86,   "avg_diggs": 1100,  "avg_comments": 38},
}


# ── Twitter generator ─────────────────────────────────────────────────────────

def generate_twitter(date: datetime, t: float):
    """Generate one weekly Twitter snapshot at fractional time t."""
    window_start = date - timedelta(days=7)
    terms_out = []
    for tid, base in TW_BASE.items():
        traj = TRAJECTORIES[tid]
        meta = TERM_META[tid]

        tc   = max(1, scaled(base["tweet_count"],   traj, t))
        avg_rt = max(0.0, scaled_f(base["avg_retweets"], traj, t, floor=0.0))
        avg_lk = max(0.0, scaled_f(base["avg_likes"],    traj, t, floor=0.0))
        top_rt = max(0, scaled(base["top_retweets"], traj, t))
        top_lk = max(0, scaled(base["top_likes"],    traj, t))

        total_lk = round(avg_lk * tc)
        total_rt = round(avg_rt * tc)

        terms_out.append({
            "term_id":           tid,
            "social_trend_name": meta["social_trend_name"],
            "underlying_topic":  meta["underlying_topic"],
            "everme_category":   meta.get("everme_category", ""),
            "queries_used":      [meta["social_trend_name"]] + meta.get("related_terms", [])[:3],
            "window":            "7d",
            "tweet_count":       tc,
            "total_likes":       total_lk,
            "total_retweets":    total_rt,
            "avg_likes":         round(avg_lk, 1),
            "avg_retweets":      round(avg_rt, 2),
            "top_likes":         top_lk,
            "top_retweets":      top_rt,
            "tweets":            make_tweets(tid, tc, window_start, avg_lk, avg_rt),
        })

    return {
        "source":       "twitter",
        "collected_at": fmt(date),
        "window":       "7d",
        "term_count":   len(terms_out),
        "note":         "MOCK DATA — simulated for historical analysis",
        "terms":        terms_out,
    }


# ── YouTube generator ─────────────────────────────────────────────────────────

def generate_youtube(date: datetime, t: float):
    terms_out = []
    for tid, base in YT_BASE.items():
        traj = TRAJECTORIES[tid]
        meta = TERM_META[tid]

        # 365d window — smoother trajectory (slower to change)
        avg_365 = max(1, scaled(base["avg_vpd_365"], traj, t, pct=0.08))
        top_365 = max(avg_365, scaled(base["top_vpd_365"], traj, t, pct=0.12))
        vc_365  = round(avg_365 * 365 / random.uniform(800, 2500))  # proxy video count
        total_365 = avg_365 * 365

        # 90d window — more reactive (current state, uses 90d factor closer to t)
        t_90 = min(1.0, t + 0.05)  # 90d window is slightly "more current"
        avg_90 = max(1, scaled(base["avg_vpd_90"], traj, t_90, pct=0.14))
        top_90 = max(avg_90, scaled(base["top_vpd_90"], traj, t_90, pct=0.16))
        vc_90  = round(avg_90 * 90 / random.uniform(800, 2500))
        total_90 = avg_90 * 90

        terms_out.append({
            "term_id":           tid,
            "social_trend_name": meta["social_trend_name"],
            "underlying_topic":  meta["underlying_topic"],
            "everme_category":   meta.get("everme_category", ""),
            "queries_used":      [meta["social_trend_name"]] + meta.get("related_terms", [])[:3],
            "windows": {
                "90d": {
                    "video_count":        max(1, vc_90),
                    "total_views":        total_90,
                    "avg_views_per_day":  avg_90,
                    "top_views_per_day":  top_90,
                    "videos":             make_yt_videos(tid, top_90, n=2),
                },
                "365d": {
                    "video_count":        max(1, vc_365),
                    "total_views":        total_365,
                    "avg_views_per_day":  avg_365,
                    "top_views_per_day":  top_365,
                    "videos":             make_yt_videos(tid, top_365, n=2),
                },
            },
        })

    return {
        "source":       "youtube",
        "collected_at": fmt(date),
        "windows":      ["90d", "365d"],
        "term_count":   len(terms_out),
        "note":         "MOCK DATA — simulated for historical analysis",
        "terms":        terms_out,
    }


# ── Google Trends generator ───────────────────────────────────────────────────

def generate_google_trends(date: datetime, t: float):
    terms_out = []
    for tid, base in GT_BASE.items():
        meta = TERM_META[tid]
        traj = TRAJECTORIES[tid]

        if base["low_data"]:
            w90 = {
                "low_data":          True,
                "current_score":     0,
                "avg_score":         0,
                "peak_score":        0,
                "peak_date":         None,
                "velocity":          0.0,
                "interest_over_time": [],
                "rising_queries":    [],
            }
            w365 = dict(w90)
        else:
            # 90d window
            cur_90  = max(0, min(100, scaled(base["cur_90"], traj, t, pct=0.15)))
            avg_90  = max(0, min(100, scaled(base["avg_90"], traj, t, pct=0.10)))
            vel_90  = round(scaled_f(base["vel_90"],  traj, t, pct=0.30), 3)
            peak_90 = min(100, round(cur_90 * random.uniform(1.0, 1.6)))
            peak_date_90 = (date - timedelta(days=random.randint(5, 60))).strftime("%Y-%m-%d")
            w90 = {
                "low_data":          False,
                "current_score":     cur_90,
                "avg_score":         avg_90,
                "peak_score":        peak_90,
                "peak_date":         peak_date_90,
                "velocity":          vel_90,
                "interest_over_time": make_gt_iot(date - timedelta(days=90), 13, avg_90),
                "rising_queries":    [],
            }

            # 365d window
            avg_365 = max(0, min(100, scaled(base["avg_365"], traj, t, pct=0.10)))
            vel_365 = round(scaled_f(base["vel_365"], traj, t, pct=0.25), 3)
            peak_365 = min(100, round(avg_365 * random.uniform(1.3, 2.2)))
            # current_score for 365d = most recent weekly value, approximated as cur_90
            # capped to the 365d avg to avoid inflating a long-window average
            cur_365 = min(cur_90, round(avg_365 * random.uniform(0.8, 1.3)))
            w365 = {
                "low_data":          False,
                "current_score":     cur_365,
                "avg_score":         avg_365,
                "peak_score":        peak_365,
                "peak_date":         (date - timedelta(days=random.randint(10, 200))).strftime("%Y-%m-%d"),
                "velocity":          vel_365,
                "interest_over_time": make_gt_iot(date - timedelta(days=365), 52, avg_365),
                "rising_queries":    [],
            }

        terms_out.append({
            "term_id":           tid,
            "social_trend_name": meta["social_trend_name"],
            "underlying_topic":  meta["underlying_topic"],
            "everme_category":   meta.get("everme_category", ""),
            "search_term_used":  meta["social_trend_name"],
            "windows": {"90d": w90, "365d": w365},
        })

    return {
        "source":       "google_trends",
        "collected_at": fmt(date),
        "windows":      ["90d", "365d"],
        "term_count":   len(terms_out),
        "note":         "MOCK DATA — simulated for historical analysis",
        "terms":        terms_out,
    }


# ── TikTok generator ─────────────────────────────────────────────────────────

def make_tt_videos(term_id: str, top_plays: int, avg_plays: int, n: int = 3) -> list[dict]:
    meta = TERM_META[term_id]
    videos = []
    for i in range(n):
        plays = max(100, round(jitter(avg_plays * random.uniform(0.3, 1.8), 0.15)))
        shares = max(0, round(plays * random.uniform(0.005, 0.015)))
        diggs  = max(0, round(plays * random.uniform(0.02,  0.08)))
        videos.append({
            "video_id":        f"mock_tt_{term_id[:6]}_{i}",
            "url":             f"https://www.tiktok.com/@mock_user_{i}/video/mock_{i}",
            "author":          f"mock_creator_{i}",
            "author_fans":     random.randint(5000, 500000),
            "created_at":      "",
            "play_count":      plays,
            "digg_count":      diggs,
            "share_count":     shares,
            "comment_count":   max(0, round(plays * 0.002)),
            "collect_count":   max(0, round(plays * 0.003)),
            "duration_seconds": random.randint(30, 180),
            "hashtags":        [meta["social_trend_name"].lower().replace(" ", "")],
            "matched_query":   meta["social_trend_name"],
        })
    return videos


def generate_tiktok(date: datetime, t: float):
    terms_out = []
    for tid, base in TT_BASE.items():
        traj = TRAJECTORIES[tid]
        meta = TERM_META[tid]

        avg_plays  = max(100, scaled(base["avg_plays"],   traj, t, pct=0.18))
        top_plays  = max(avg_plays, scaled(base["top_plays"],   traj, t, pct=0.20))
        avg_shares = max(0, scaled(base["avg_shares"],  traj, t, pct=0.20))
        avg_diggs  = max(0, scaled(base["avg_diggs"],   traj, t, pct=0.18))
        avg_comments = max(0, scaled(base["avg_comments"], traj, t, pct=0.20))

        n = 10
        total_plays = avg_plays * n

        terms_out.append({
            "term_id":           tid,
            "social_trend_name": meta["social_trend_name"],
            "underlying_topic":  meta["underlying_topic"],
            "everme_category":   meta.get("everme_category", ""),
            "query_used":        meta["social_trend_name"],
            "video_count":       n,
            "total_plays":       total_plays,
            "avg_plays":         avg_plays,
            "top_plays":         top_plays,
            "avg_shares":        avg_shares,
            "avg_diggs":         avg_diggs,
            "avg_comments":      avg_comments,
            "videos":            make_tt_videos(tid, top_plays, avg_plays, n=3),
        })

    return {
        "source":           "tiktok",
        "actor":            "clockworks/tiktok-scraper",
        "collected_at":     fmt(date),
        "results_per_term": 10,
        "term_count":       len(terms_out),
        "note":             "MOCK DATA — simulated for historical analysis",
        "terms":            terms_out,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  wrote {path}  ({path.stat().st_size // 1024}KB)")


def main():
    # Reference point: April 29 2026 = t=1.0
    # History start: November 1 2025 = t=0.0  (~6 months)
    start = datetime(2025, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
    end   = datetime(2026,  4, 29, 12, 0, 0, tzinfo=timezone.utc)
    span  = (end - start).days

    def t_for(dt: datetime) -> float:
        return (dt - start).days / span

    # ── Twitter: 26 weekly snapshots ─────────────────────────────────────────
    print("Generating Twitter (26 weekly)...")
    tw_dates = [start + timedelta(weeks=w) for w in range(26)]
    for dt in tw_dates:
        t = t_for(dt)
        data = generate_twitter(dt, t)
        fname = f"twitter_{dt.strftime('%Y-%m-%d')}.json"
        write(OUT_DIR / fname, data)

    # ── YouTube: 6 monthly snapshots ─────────────────────────────────────────
    print("Generating YouTube (6 monthly)...")
    yt_dates = [
        datetime(2025, 11,  1, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2025, 12,  1, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026,  1,  1, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026,  2,  1, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026,  3,  1, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026,  4,  1, 12, 0, 0, tzinfo=timezone.utc),
    ]
    for dt in yt_dates:
        t = t_for(dt)
        data = generate_youtube(dt, t)
        fname = f"youtube_{dt.strftime('%Y-%m-%d')}.json"
        write(OUT_DIR / fname, data)

    # ── Google Trends: 4 snapshots (~every 6 weeks) ───────────────────────────
    print("Generating Google Trends (4 snapshots)...")
    gt_dates = [
        datetime(2025, 11, 15, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026,  1,  1, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026,  2, 15, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026,  3, 25, 12, 0, 0, tzinfo=timezone.utc),
    ]
    for dt in gt_dates:
        t = t_for(dt)
        data = generate_google_trends(dt, t)
        fname = f"google_trends_{dt.strftime('%Y-%m-%d')}.json"
        write(OUT_DIR / fname, data)

    # ── TikTok: 12 bi-weekly snapshots ───────────────────────────────────────
    print("Generating TikTok (12 bi-weekly)...")
    tt_dates = [start + timedelta(weeks=w * 2) for w in range(12)]
    for dt in tt_dates:
        t = t_for(dt)
        data = generate_tiktok(dt, t)
        fname = f"tiktok_{dt.strftime('%Y-%m-%d')}.json"
        write(OUT_DIR / fname, data)

    print(f"\nDone — {len(tw_dates)} Twitter + {len(yt_dates)} YouTube + {len(gt_dates)} Google Trends + {len(tt_dates)} TikTok files")
    print(f"Output: {OUT_DIR}/")
    print("\nTrajectory legend:")
    for tid, traj in TRAJECTORIES.items():
        name = TERM_META[tid]["social_trend_name"]
        print(f"  {name:<22} {traj}")


if __name__ == "__main__":
    main()
