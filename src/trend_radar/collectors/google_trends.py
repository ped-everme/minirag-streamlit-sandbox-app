"""
Google Trends validator for Mini-RAG.

Searches Google Trends using social_trend_name as query.
Runs both 90d and 365d windows per term in a single pass.

Run:
  python src/trend_radar/collectors/google_trends.py
  python src/trend_radar/collectors/google_trends.py --terms src/trend_radar/data/mock/terms.json
  python src/trend_radar/collectors/google_trends.py --sleep 5   # safer after rate limiting
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pytrends.request import TrendReq

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TERMS_PATH = str(BASE_DIR / "data" / "mock" / "terms.json")

PYTRENDS_TIMEFRAME = {
    "90d": "today 3-m",
    "365d": "today 12-m",
}

SLEEP_BETWEEN_REQUESTS = 2


def build_interest_series(df, term_col: str) -> list[dict]:
    series = []
    for date, row in df.iterrows():
        if row.get("isPartial", False):
            continue
        series.append({"date": date.strftime("%Y-%m-%d"), "score": int(row[term_col])})
    return series


def calculate_velocity(series: list[dict]) -> float:
    scores = [s["score"] for s in series]
    if len(scores) < 4:
        return 0.0
    mid = len(scores) // 2
    avg_first = sum(scores[:mid]) / mid
    avg_second = sum(scores[mid:]) / (len(scores) - mid)
    return round((avg_second - avg_first) / 100, 3)


def get_rising_queries(pytrends_obj, term: str) -> list[str]:
    try:
        related = pytrends_obj.related_queries()
        rising_df = related.get(term, {}).get("rising")
        if rising_df is None or rising_df.empty:
            return []
        return rising_df["query"].head(5).tolist()
    except Exception:
        return []


def collect_window(pytrends_obj, search_term: str, window: str) -> dict:
    result = {
        "low_data": False,
        "current_score": 0,
        "avg_score": 0,
        "peak_score": 0,
        "peak_date": None,
        "velocity": 0.0,
        "interest_over_time": [],
        "rising_queries": [],
    }
    try:
        pytrends_obj.build_payload([search_term], timeframe=PYTRENDS_TIMEFRAME[window], geo="US")
        df = pytrends_obj.interest_over_time()

        if df.empty or search_term not in df.columns:
            result["low_data"] = True
            return result

        series = build_interest_series(df, search_term)
        if not series:
            result["low_data"] = True
            return result

        scores = [s["score"] for s in series]
        peak_val = max(scores)
        peak_idx = len(scores) - 1 - scores[::-1].index(peak_val)

        result["interest_over_time"] = series
        result["current_score"] = scores[-1]
        result["avg_score"] = round(sum(scores) / len(scores))
        result["peak_score"] = max(scores)
        result["peak_date"] = series[peak_idx]["date"]
        result["velocity"] = calculate_velocity(series)
        result["rising_queries"] = get_rising_queries(pytrends_obj, search_term)

    except Exception as e:
        result["low_data"] = True
        result["error"] = str(e)

    return result


def collect_term(pytrends_obj, term: dict, sleep: float) -> dict:
    search_term = term["social_trend_name"]
    windows = {}
    for window in ["90d", "365d"]:
        windows[window] = collect_window(pytrends_obj, search_term, window)
        time.sleep(sleep)
    return {
        "term_id": term["id"],
        "social_trend_name": term["social_trend_name"],
        "underlying_topic": term["underlying_topic"],
        "everme_category": term.get("everme_category", ""),
        "search_term_used": search_term,
        "windows": windows,
    }


def main():
    parser = argparse.ArgumentParser(description="Google Trends validator for Mini-RAG")
    parser.add_argument("--terms", default=DEFAULT_TERMS_PATH)
    parser.add_argument("--output", default=None)
    parser.add_argument("--sleep", type=float, default=SLEEP_BETWEEN_REQUESTS,
                        help="Seconds between requests (default: 2, increase to 5 if rate limited)")
    args = parser.parse_args()

    with open(args.terms, encoding="utf-8") as f:
        terms = json.load(f)

    now = datetime.now(timezone.utc)
    pytrends_obj = TrendReq(hl="en-US", tz=0)

    print(f"Google Trends validator — 90d + 365d windows per term")
    print(f"Terms: {len(terms)} | geo=US | Sleep: {args.sleep}s between requests")
    print()

    results = []
    for i, term in enumerate(terms, 1):
        search_term = term["social_trend_name"]
        print(f"[{i:>2}/{len(terms)}] {term['social_trend_name']:<30} → '{search_term}' ...", end=" ", flush=True)

        result = collect_term(pytrends_obj, term, args.sleep)
        results.append(result)

        w90 = result["windows"]["90d"]
        w365 = result["windows"]["365d"]
        parts = []
        for label, w in [("90d", w90), ("365d", w365)]:
            if w["low_data"]:
                parts.append(f"{label}:LOW")
            else:
                arrow = "↑" if w["velocity"] > 0.05 else "↓" if w["velocity"] < -0.05 else "→"
                parts.append(f"{label}:{w['current_score']}{arrow}")
        print("  ".join(parts))

    output = {
        "source": "google_trends",
        "collected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "windows": ["90d", "365d"],
        "term_count": len(results),
        "terms": results,
    }

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = BASE_DIR / "data" / "raw" / f"google_trends_{now.strftime('%Y-%m-%d')}.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    valid = [r for r in results if not r["windows"]["365d"]["low_data"]]
    low = [r for r in results if r["windows"]["365d"]["low_data"]]

    print(f"\nDone → {output_path}")

    if len(low) > len(terms) * 0.7 and len(valid) == 0:
        print("\n⚠  All or most terms returned LOW DATA.")
        print("   Likely cause: Google rate limiting from running the script too frequently.")
        print("   Fix: wait 15–30 minutes and run again, or use --sleep 5")

    print(f"\nSummary ({len(valid)} with 365d data, {len(low)} low/no data):")
    for r in sorted(valid, key=lambda x: x["windows"]["365d"]["current_score"], reverse=True):
        w90 = r["windows"]["90d"]
        w365 = r["windows"]["365d"]
        arrow = "↑" if w365["velocity"] > 0.05 else "↓" if w365["velocity"] < -0.05 else "→"
        bar = "█" * min(w365["current_score"] // 5, 20)
        s90 = f"{w90['current_score']}" if not w90["low_data"] else "LOW"
        print(f"  365d:{w365['current_score']:>3} 90d:{s90:>3}  {bar:<20}  {arrow}  {r['social_trend_name']}")
    for r in low:
        print(f"    — (low data)  {r['social_trend_name']}")


if __name__ == "__main__":
    main()
