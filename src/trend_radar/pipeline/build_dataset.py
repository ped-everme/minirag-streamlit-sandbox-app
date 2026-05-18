"""
ETL pipeline — flattens raw JSON collector outputs into time-series datasets.

Per-source tables (one row per term × snapshot, kept at natural cadence):
  data/processed/terms.csv
  data/processed/twitter_ts.csv       weekly
  data/processed/youtube_ts.csv       monthly
  data/processed/google_trends_ts.csv ~6-weekly
  data/processed/tiktok_ts.csv        bi-weekly

Unified analysis table (one row per term × week, all sources joined):
  data/processed/dataset.csv

In dataset.csv, lower-cadence sources (YouTube, Google Trends, TikTok) are
forward-filled from their most recent snapshot up to each week. A *_collected
column per source records which snapshot date was used, making the fill
transparent. The dashboard uses this table for scoring, classification, and
threshold exploration.

Run:
  python src/trend_radar/pipeline/build_dataset.py
  python src/trend_radar/pipeline/build_dataset.py --sources src/trend_radar/data/mock src/trend_radar/data/raw
  python src/trend_radar/pipeline/build_dataset.py --output src/trend_radar/data/processed
"""

import argparse
import bisect
import csv
import json
from datetime import date, timedelta
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = [str(_BASE_DIR / "data" / "mock"), str(_BASE_DIR / "data" / "raw")]
DEFAULT_OUTPUT  = _BASE_DIR / "data" / "processed"


# ── File discovery ─────────────────────────────────────────────────────────────

def discover(source_dirs: list[str]) -> dict[str, list[tuple[Path, bool]]]:
    """Return {source_type: [(path, is_mock), ...]} sorted by filename."""
    buckets: dict[str, list[tuple[Path, bool]]] = {
        "twitter": [], "youtube": [], "google_trends": [], "tiktok": [],
    }
    prefixes = {
        "twitter_":       "twitter",
        "youtube_":       "youtube",
        "google_trends_": "google_trends",
        "tiktok_":        "tiktok",
    }
    for dir_str in source_dirs:
        d = Path(dir_str)
        if not d.exists():
            continue
        is_mock = "mock" in dir_str
        for f in sorted(d.glob("*.json")):
            for prefix, key in prefixes.items():
                if f.stem.startswith(prefix):
                    buckets[key].append((f, is_mock))
                    break
    return buckets


# ── Extractors ─────────────────────────────────────────────────────────────────

def extract_twitter(path: Path, is_mock: bool) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    collected_at = data.get("collected_at", "")[:10]
    rows = []
    for term in data.get("terms", []):
        rows.append({
            "term_id":           term["term_id"],
            "social_trend_name": term.get("social_trend_name", ""),
            "collected_at":      collected_at,
            "tweet_count":       term.get("tweet_count", 0),
            "avg_retweets":      term.get("avg_retweets", 0),
            "avg_likes":         term.get("avg_likes", 0),
            "top_retweets":      term.get("top_retweets", 0),
            "top_likes":         term.get("top_likes", 0),
            "is_mock":           is_mock,
        })
    return rows


def extract_youtube(path: Path, is_mock: bool) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    collected_at = data.get("collected_at", "")[:10]
    rows = []
    for term in data.get("terms", []):
        w90  = term.get("windows", {}).get("90d",  {})
        w365 = term.get("windows", {}).get("365d", {})
        rows.append({
            "term_id":          term["term_id"],
            "social_trend_name": term.get("social_trend_name", ""),
            "collected_at":     collected_at,
            "avg_vpd_90d":      w90.get("avg_views_per_day"),
            "top_vpd_90d":      w90.get("top_views_per_day"),
            "video_count_90d":  w90.get("video_count"),
            "avg_vpd_365d":     w365.get("avg_views_per_day"),
            "top_vpd_365d":     w365.get("top_views_per_day"),
            "video_count_365d": w365.get("video_count"),
            "is_mock":          is_mock,
        })
    return rows


def extract_google_trends(path: Path, is_mock: bool) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    collected_at = data.get("collected_at", "")[:10]
    rows = []
    for term in data.get("terms", []):
        w90  = term.get("windows", {}).get("90d",  {})
        w365 = term.get("windows", {}).get("365d", {})
        rows.append({
            "term_id":           term["term_id"],
            "social_trend_name": term.get("social_trend_name", ""),
            "collected_at":      collected_at,
            "low_data_90d":      w90.get("low_data", True),
            "velocity_90d":      w90.get("velocity"),
            "current_score_90d": w90.get("current_score"),
            "avg_score_90d":     w90.get("avg_score"),
            "peak_score_90d":    w90.get("peak_score"),
            "low_data_365d":     w365.get("low_data", True),
            "velocity_365d":     w365.get("velocity"),
            "avg_score_365d":    w365.get("avg_score"),
            "peak_score_365d":   w365.get("peak_score"),
            "is_mock":           is_mock,
        })
    return rows


def extract_tiktok(path: Path, is_mock: bool) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    collected_at = data.get("collected_at", "")[:10]
    rows = []
    for term in data.get("terms", []):
        rows.append({
            "term_id":           term["term_id"],
            "social_trend_name": term.get("social_trend_name", ""),
            "collected_at":      collected_at,
            "video_count":       term.get("video_count", 0),
            "avg_plays":         term.get("avg_plays", 0),
            "top_plays":         term.get("top_plays", 0),
            "avg_shares":        term.get("avg_shares", 0),
            "avg_diggs":         term.get("avg_diggs", 0),
            "avg_comments":      term.get("avg_comments", 0),
            "is_mock":           is_mock,
        })
    return rows


def extract_terms(source_dirs: list[str]) -> list[dict]:
    for d in source_dirs:
        p = Path(d) / "terms.json"
        if p.exists():
            with open(p, encoding="utf-8") as f:
                terms = json.load(f)
            return [
                {
                    "term_id":           t["id"],
                    "social_trend_name": t.get("social_trend_name", ""),
                    "underlying_topic":  t.get("underlying_topic", ""),
                    "everme_category":   t.get("everme_category", ""),
                }
                for t in terms
            ]
    return []


# ── As-of index ────────────────────────────────────────────────────────────────

def build_index(rows: list[dict]) -> dict[str, list[tuple[str, dict]]]:
    """
    Build {term_id: [(date_str, row), ...]} sorted by date for as-of lookup.
    Deduplicates (term_id, collected_at) — real data wins over mock.
    """
    # real data (is_mock=False) overwrites mock for same key
    best: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["term_id"], row["collected_at"])
        existing = best.get(key)
        if existing is None or (existing["is_mock"] and not row["is_mock"]):
            best[key] = row

    index: dict[str, list[tuple[str, dict]]] = {}
    for (term_id, collected_at), row in best.items():
        index.setdefault(term_id, []).append((collected_at, row))

    for term_id in index:
        index[term_id].sort(key=lambda x: x[0])

    return index


def as_of(index: dict[str, list[tuple[str, dict]]], term_id: str, on: str) -> tuple[dict | None, str | None]:
    """Return (row, collected_date) for the most recent snapshot with date <= on."""
    snaps = index.get(term_id)
    if not snaps:
        return None, None
    dates = [s[0] for s in snaps]
    idx = bisect.bisect_right(dates, on) - 1
    if idx < 0:
        return None, None
    return snaps[idx][1], snaps[idx][0]


# ── Unified dataset builder ────────────────────────────────────────────────────

def date_range(start: str, end: str, step_days: int = 7) -> list[str]:
    """Generate weekly date strings from start to end inclusive."""
    d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    dates = []
    while d <= end_d:
        dates.append(d.isoformat())
        d += timedelta(days=step_days)
    return dates


def build_unified(
    terms: list[dict],
    tw_index: dict,
    yt_index: dict,
    gt_index: dict,
    tt_index: dict,
    all_dates: list[str],
) -> list[dict]:
    rows = []
    terms_meta = {t["term_id"]: t for t in terms}

    for week in all_dates:
        for term_id, meta in terms_meta.items():
            tw_row, tw_date = as_of(tw_index, term_id, week)
            yt_row, yt_date = as_of(yt_index, term_id, week)
            gt_row, gt_date = as_of(gt_index, term_id, week)
            tt_row, tt_date = as_of(tt_index, term_id, week)

            # Skip weeks where no source has any data yet
            if tw_row is None and yt_row is None and gt_row is None and tt_row is None:
                continue

            is_mock = all(
                r is None or r["is_mock"]
                for r in [tw_row, yt_row, gt_row, tt_row]
            )

            row: dict = {
                "date":              week,
                "term_id":           term_id,
                "social_trend_name": meta["social_trend_name"],
                "underlying_topic":  meta["underlying_topic"],
                "everme_category":   meta["everme_category"],
                "is_mock":           is_mock,
                # Twitter
                "tw_collected":      tw_date,
                "tw_tweet_count":    tw_row.get("tweet_count")    if tw_row else None,
                "tw_avg_retweets":   tw_row.get("avg_retweets")   if tw_row else None,
                "tw_avg_likes":      tw_row.get("avg_likes")      if tw_row else None,
                "tw_top_retweets":   tw_row.get("top_retweets")   if tw_row else None,
                "tw_top_likes":      tw_row.get("top_likes")      if tw_row else None,
                # YouTube
                "yt_collected":      yt_date,
                "yt_avg_vpd_90d":    yt_row.get("avg_vpd_90d")   if yt_row else None,
                "yt_top_vpd_90d":    yt_row.get("top_vpd_90d")   if yt_row else None,
                "yt_avg_vpd_365d":   yt_row.get("avg_vpd_365d")  if yt_row else None,
                "yt_top_vpd_365d":   yt_row.get("top_vpd_365d")  if yt_row else None,
                # Google Trends
                "gt_collected":      gt_date,
                "gt_low_data_90d":   gt_row.get("low_data_90d")      if gt_row else None,
                "gt_velocity_90d":   gt_row.get("velocity_90d")      if gt_row else None,
                "gt_current_90d":    gt_row.get("current_score_90d") if gt_row else None,
                "gt_avg_90d":        gt_row.get("avg_score_90d")     if gt_row else None,
                "gt_low_data_365d":  gt_row.get("low_data_365d")     if gt_row else None,
                "gt_velocity_365d":  gt_row.get("velocity_365d")     if gt_row else None,
                "gt_avg_365d":       gt_row.get("avg_score_365d")    if gt_row else None,
                # TikTok
                "tt_collected":      tt_date,
                "tt_avg_plays":      tt_row.get("avg_plays")   if tt_row else None,
                "tt_top_plays":      tt_row.get("top_plays")   if tt_row else None,
                "tt_avg_shares":     tt_row.get("avg_shares")  if tt_row else None,
                "tt_avg_diggs":      tt_row.get("avg_diggs")   if tt_row else None,
                "tt_avg_comments":   tt_row.get("avg_comments") if tt_row else None,
            }
            rows.append(row)

    return rows


# ── CSV writer ─────────────────────────────────────────────────────────────────

def _normalize_row(row: dict) -> dict:
    """Convert bools to 0/1 so CSV consumers don't get string 'True'/'False'."""
    return {k: int(v) if isinstance(v, bool) else v for k, v in row.items()}


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        print(f"  skip {path.name} — no data")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [_normalize_row(r) for r in rows]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(normalized[0].keys()))
        writer.writeheader()
        writer.writerows(normalized)
    print(f"  {path.name:<30} {len(rows):>4} rows")


# ── Main ───────────────────────────────────────────────────────────────────────

EXTRACTORS = {
    "twitter":       extract_twitter,
    "youtube":       extract_youtube,
    "google_trends": extract_google_trends,
    "tiktok":        extract_tiktok,
}

OUTPUT_NAMES = {
    "twitter":       "twitter_ts.csv",
    "youtube":       "youtube_ts.csv",
    "google_trends": "google_trends_ts.csv",
    "tiktok":        "tiktok_ts.csv",
}


def main():
    parser = argparse.ArgumentParser(description="ETL — flatten collector JSONs to CSV datasets")
    parser.add_argument("--sources", nargs="+", default=DEFAULT_SOURCES)
    parser.add_argument("--output",  default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    output_dir = Path(args.output)
    files = discover(args.sources)

    print(f"Sources: {', '.join(args.sources)}")
    for src, paths in files.items():
        print(f"  {src:<15} {len(paths)} files")
    print(f"Output:  {output_dir}/\n")

    terms = extract_terms(args.sources)
    write_csv(output_dir / "terms.csv", terms)

    # ── Per-source tables + as-of indexes ─────────────────────────────────────
    all_rows: dict[str, list[dict]] = {}
    seen: dict[str, set[tuple]] = {k: set() for k in EXTRACTORS}

    for source, extractor in EXTRACTORS.items():
        rows = []
        for path, is_mock in files[source]:
            for row in extractor(path, is_mock):
                key = (row["term_id"], row["collected_at"])
                if key not in seen[source]:
                    seen[source].add(key)
                    rows.append(row)
        rows.sort(key=lambda r: (r["term_id"], r["collected_at"]))
        all_rows[source] = rows
        write_csv(output_dir / OUTPUT_NAMES[source], rows)

    # ── Unified dataset ────────────────────────────────────────────────────────
    tw_index = build_index(all_rows["twitter"])
    yt_index = build_index(all_rows["youtube"])
    gt_index = build_index(all_rows["google_trends"])
    tt_index = build_index(all_rows["tiktok"])

    # Weekly timeline spanning all available data
    all_dates_flat = [
        row["collected_at"]
        for source_rows in all_rows.values()
        for row in source_rows
    ]
    if all_dates_flat:
        start = min(all_dates_flat)
        end   = max(all_dates_flat)
        weeks = date_range(start, end, step_days=7)
        unified = build_unified(terms, tw_index, yt_index, gt_index, tt_index, weeks)
        unified.sort(key=lambda r: (r["date"], r["term_id"]))
        write_csv(output_dir / "dataset.csv", unified)

    print(f"\nDone → {output_dir}/")


if __name__ == "__main__":
    main()
