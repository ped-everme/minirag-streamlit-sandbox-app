"""
YouTube validator for Mini-RAG.

Searches YouTube using social_trend_name + related_terms as queries.
Runs a single 365d search pass and derives both 90d and 365d window metrics
from the same result set — conserving API quota.

Run:
  python src/trend_radar/collectors/youtube.py
  python src/trend_radar/collectors/youtube.py --terms src/trend_radar/data/mock/terms.json
  python src/trend_radar/collectors/youtube.py --output src/trend_radar/data/raw/youtube_test.json
"""

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from googleapiclient.discovery import build

load_dotenv()

API_KEY = os.getenv("YOUTUBE_DATA_API_KEY")

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TERMS_PATH = str(BASE_DIR / "data" / "mock" / "terms.json")
SORT_ORDERS = ["viewCount", "date"]
WINDOWS = {"90d": 90, "365d": 365}


def published_after_dt(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def search_videos(youtube, query: str, published_after: datetime, max_results: int, order: str) -> list[str]:
    response = youtube.search().list(
        q=query,
        type="video",
        order=order,
        publishedAfter=published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        maxResults=max_results,
        relevanceLanguage="en",
        part="snippet",
    ).execute()
    return [item["id"]["videoId"] for item in response.get("items", [])]


def fetch_video_details(youtube, video_ids: list[str]) -> list[dict]:
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        response = youtube.videos().list(
            id=",".join(batch),
            part="snippet,statistics,contentDetails",
        ).execute()
        videos.extend(response.get("items", []))
    return videos


def parse_duration(duration_str: str) -> int:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or "")
    if not match:
        return 0
    h, m, s = (int(x or 0) for x in match.groups())
    return h * 3600 + m * 60 + s


def build_video_item(video: dict, matched_query: str, now: datetime) -> dict:
    snippet = video.get("snippet", {})
    stats = video.get("statistics", {})
    content = video.get("contentDetails", {})

    published_at_str = snippet.get("publishedAt", "")
    published_at = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
    days_since_publish = max((now - published_at).days, 1)
    view_count = int(stats.get("viewCount", 0))

    return {
        "video_id": video["id"],
        "title": snippet.get("title", ""),
        "channel_id": snippet.get("channelId", ""),
        "channel_title": snippet.get("channelTitle", ""),
        "published_at": published_at_str,
        "days_since_publish": days_since_publish,
        "matched_query": matched_query,
        "view_count": view_count,
        "like_count": int(stats.get("likeCount", 0)),
        "comment_count": int(stats.get("commentCount", 0)),
        "views_per_day": round(view_count / days_since_publish),
        "duration_seconds": parse_duration(content.get("duration", "")),
        "tags": snippet.get("tags", []),
    }


def window_metrics(videos: list[dict]) -> dict:
    if not videos:
        return {"video_count": 0, "total_views": 0, "avg_views_per_day": 0, "top_views_per_day": 0, "videos": []}
    total_views = sum(v["view_count"] for v in videos)
    vpd_values = [v["views_per_day"] for v in videos]
    return {
        "video_count": len(videos),
        "total_views": total_views,
        "avg_views_per_day": round(sum(vpd_values) / len(vpd_values)),
        "top_views_per_day": max(vpd_values),
        "videos": videos,
    }


def collect_term(youtube, term: dict, max_results: int, now: datetime) -> dict:
    queries = [term["social_trend_name"]] + term.get("related_terms", [])
    after_365 = published_after_dt(365)
    after_90 = published_after_dt(90)

    # Single search pass using 365d window — 90d subset derived by filtering
    seen: dict[str, str] = {}
    for query in queries:
        for order in SORT_ORDERS:
            ids = search_videos(youtube, query, after_365, max_results, order)
            for vid_id in ids:
                if vid_id not in seen:
                    seen[vid_id] = query

    raw_videos = fetch_video_details(youtube, list(seen.keys()))

    videos_365d = []
    for video in raw_videos:
        published_at_str = video.get("snippet", {}).get("publishedAt", "")
        if not published_at_str:
            continue
        pub_dt = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
        if pub_dt < after_365:
            continue
        videos_365d.append(build_video_item(video, seen[video["id"]], now))

    videos_365d.sort(key=lambda v: v["views_per_day"], reverse=True)
    videos_90d = [v for v in videos_365d
                  if datetime.fromisoformat(v["published_at"].replace("Z", "+00:00")) >= after_90]
    videos_90d.sort(key=lambda v: v["views_per_day"], reverse=True)

    return {
        "term_id": term["id"],
        "social_trend_name": term["social_trend_name"],
        "underlying_topic": term["underlying_topic"],
        "everme_category": term.get("everme_category", ""),
        "queries_used": queries,
        "windows": {
            "90d": window_metrics(videos_90d),
            "365d": window_metrics(videos_365d),
        },
    }


def estimate_quota(terms: list[dict]) -> int:
    total_queries = sum(1 + len(t.get("related_terms", [])) for t in terms)
    return total_queries * len(SORT_ORDERS) * 100  # search.list = 100 units each


def main():
    parser = argparse.ArgumentParser(description="YouTube trend validator for Mini-RAG")
    parser.add_argument("--terms", default=DEFAULT_TERMS_PATH)
    parser.add_argument("--output", default=None)
    parser.add_argument("--max-results", type=int, default=50)
    args = parser.parse_args()

    if not API_KEY:
        raise SystemExit("Error: YOUTUBE_DATA_API_KEY not set in .env")

    with open(args.terms, encoding="utf-8") as f:
        terms = json.load(f)

    now = datetime.now(timezone.utc)
    youtube = build("youtube", "v3", developerKey=API_KEY)

    est_quota = estimate_quota(terms)
    print(f"YouTube validator — 90d + 365d windows per term")
    print(f"Terms: {len(terms)} | Est. quota: {est_quota:,} units (limit: 10,000/day)")
    print()

    results = []
    for i, term in enumerate(terms, 1):
        n_queries = 1 + len(term.get("related_terms", []))
        print(f"[{i:>2}/{len(terms)}] {term['social_trend_name']:<30} ({n_queries} queries) ...", end=" ", flush=True)
        result = collect_term(youtube, term, args.max_results, now)
        results.append(result)
        w90 = result["windows"]["90d"]
        w365 = result["windows"]["365d"]
        print(f"90d: {w90['video_count']} videos | 365d: {w365['video_count']} videos | top vpd: {w365['top_views_per_day']:,}")

    output = {
        "source": "youtube",
        "collected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "windows": ["90d", "365d"],
        "term_count": len(results),
        "terms": results,
    }

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = BASE_DIR / "data" / "raw" / f"youtube_{now.strftime('%Y-%m-%d')}.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nDone → {output_path}")
    print("\nSummary (sorted by 365d top views/day):")
    for r in sorted(results, key=lambda x: x["windows"]["365d"]["top_views_per_day"], reverse=True):
        w90 = r["windows"]["90d"]
        w365 = r["windows"]["365d"]
        bar = "█" * min(int(w365["top_views_per_day"] / 5000), 20)
        print(f"  {w365['top_views_per_day']:>8,} vpd  {bar:<20}  90d:{w90['video_count']:>3}v  365d:{w365['video_count']:>3}v  {r['social_trend_name']}")


if __name__ == "__main__":
    main()
