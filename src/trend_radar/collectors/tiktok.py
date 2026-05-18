"""
TikTok validator for Mini-RAG.

Uses Apify clockworks/tiktok-scraper (actor GdWCkxBtKWOsKjdch).
Runs all terms in a single Apify job — actor handles search per query
and tags each result with the originating searchQuery.

Cost: ~$0.005/result. Default 10 results per term = ~$0.06 per run of 12 terms.

Run:
  python src/trend_radar/collectors/tiktok.py
  python src/trend_radar/collectors/tiktok.py --terms src/trend_radar/data/mock/terms.json
  python src/trend_radar/collectors/tiktok.py --results-per-term 20
"""

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

APIFY_TOKEN = os.getenv("APIFY_API_TOKEN")
ACTOR_ID    = "GdWCkxBtKWOsKjdch"   # clockworks/tiktok-scraper
BASE_URL    = "https://api.apify.com/v2"

_BASE_DIR            = Path(__file__).resolve().parent.parent
DEFAULT_TERMS_PATH   = str(_BASE_DIR / "data" / "mock" / "terms.json")
DEFAULT_RESULTS      = 10
POLL_INTERVAL        = 15   # seconds between status checks
COST_PER_RESULT      = 0.005  # USD


def start_run(queries: list[str], results_per_term: int) -> tuple[str, str]:
    payload = {
        "searchQueries": queries,
        "searchSection": "/video",
        "resultsPerPage": results_per_term,
        "searchSorting": "0",
        "commentsPerPost": 0,
        "topLevelCommentsPerPost": 0,
        "maxRepliesPerComment": 0,
        "excludePinnedPosts": False,
        "scrapeRelatedVideos": False,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadSlideshowImages": False,
        "shouldDownloadAvatars": False,
        "shouldDownloadMusicCovers": False,
        "proxyCountryCode": "None",
    }
    resp = requests.post(
        f"{BASE_URL}/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}",
        json=payload,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return data["id"], data["defaultDatasetId"]


def wait_for_run(run_id: str) -> str:
    print("Waiting", end="", flush=True)
    while True:
        resp = requests.get(f"{BASE_URL}/acts/{ACTOR_ID}/runs/{run_id}?token={APIFY_TOKEN}")
        resp.raise_for_status()
        status = resp.json()["data"]["status"]
        print(".", end="", flush=True)
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            print(f" {status}")
            return status
        time.sleep(POLL_INTERVAL)


def fetch_items(dataset_id: str) -> list[dict]:
    resp = requests.get(
        f"{BASE_URL}/datasets/{dataset_id}/items?token={APIFY_TOKEN}&limit=100000"
    )
    resp.raise_for_status()
    return resp.json()


def build_video_item(item: dict) -> dict:
    author = item.get("authorMeta", {})
    video  = item.get("videoMeta", {})
    return {
        "video_id":        item.get("id", ""),
        "url":             item.get("webVideoUrl", ""),
        "author":          author.get("name", ""),
        "author_fans":     author.get("fans", 0),
        "created_at":      item.get("createTimeISO", ""),
        "play_count":      item.get("playCount", 0),
        "digg_count":      item.get("diggCount", 0),
        "share_count":     item.get("shareCount", 0),
        "comment_count":   item.get("commentCount", 0),
        "collect_count":   item.get("collectCount", 0),
        "duration_seconds": video.get("duration", 0),
        "hashtags":        [h.get("name", "") for h in item.get("hashtags", [])],
        "matched_query":   item.get("searchQuery", ""),
    }


def aggregate_term(term: dict, videos: list[dict]) -> dict:
    n = len(videos)
    videos.sort(key=lambda v: v["play_count"], reverse=True)
    total_plays = sum(v["play_count"] for v in videos)
    return {
        "term_id":           term["id"],
        "social_trend_name": term["social_trend_name"],
        "underlying_topic":  term["underlying_topic"],
        "everme_category":   term.get("everme_category", ""),
        "query_used":        term["social_trend_name"],
        "video_count":       n,
        "total_plays":       total_plays,
        "avg_plays":         round(total_plays / n) if n else 0,
        "top_plays":         videos[0]["play_count"] if videos else 0,
        "avg_shares":        round(sum(v["share_count"] for v in videos) / n) if n else 0,
        "avg_diggs":         round(sum(v["digg_count"] for v in videos) / n) if n else 0,
        "avg_comments":      round(sum(v["comment_count"] for v in videos) / n) if n else 0,
        "videos":            videos,
    }


def main():
    parser = argparse.ArgumentParser(description="TikTok validator for Mini-RAG")
    parser.add_argument("--terms",           default=DEFAULT_TERMS_PATH)
    parser.add_argument("--output",          default=None)
    parser.add_argument("--results-per-term", type=int, default=DEFAULT_RESULTS,
                        help=f"Results per search term (default: {DEFAULT_RESULTS}, ~${DEFAULT_RESULTS * COST_PER_RESULT:.3f}/term)")
    args = parser.parse_args()

    if not APIFY_TOKEN:
        raise SystemExit("Error: APIFY_API_TOKEN not set in .env")

    with open(args.terms, encoding="utf-8") as f:
        terms = json.load(f)

    queries   = [t["social_trend_name"] for t in terms]
    est_total = len(queries) * args.results_per_term
    est_cost  = est_total * COST_PER_RESULT

    now = datetime.now(timezone.utc)
    print(f"TikTok validator — clockworks/tiktok-scraper")
    print(f"Terms: {len(terms)} | {args.results_per_term} results/term | Est. {est_total} results | Est. cost: ~${est_cost:.2f}")
    print()

    print(f"Starting Apify run ({len(queries)} search queries)...")
    run_id, dataset_id = start_run(queries, args.results_per_term)
    print(f"Run ID: {run_id}")

    status = wait_for_run(run_id)
    if status != "SUCCEEDED":
        raise SystemExit(f"Run ended with status: {status}")

    items = fetch_items(dataset_id)

    by_query: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        q = item.get("searchQuery", "")
        if q:
            by_query[q].append(build_video_item(item))

    results = []
    for term in terms:
        videos = by_query.get(term["social_trend_name"], [])
        result = aggregate_term(term, videos)
        results.append(result)
        n = result["video_count"]
        if n == 0:
            print(f"  {term['social_trend_name']:<32} no results")
        else:
            print(f"  {term['social_trend_name']:<32} {n:>2} videos | avg plays: {result['avg_plays']:>8,} | top: {result['top_plays']:>10,}")

    output = {
        "source":           "tiktok",
        "actor":            "clockworks/tiktok-scraper",
        "collected_at":     now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "results_per_term": args.results_per_term,
        "term_count":       len(results),
        "terms":            results,
    }

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = _BASE_DIR / "data" / "raw" / f"tiktok_{now.strftime('%Y-%m-%d')}.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nDone → {output_path}")
    print("\nSummary (sorted by top plays):")
    for r in sorted(results, key=lambda x: x["top_plays"], reverse=True):
        bar = "█" * min(int(r["top_plays"] / 100000), 20)
        print(f"  {r['top_plays']:>12,} plays  {bar:<20}  avg {r['avg_plays']:>9,}  {r['social_trend_name']}")


if __name__ == "__main__":
    main()
