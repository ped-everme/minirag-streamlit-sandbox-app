"""
Twitter/X validator for Mini-RAG.

Uses Twitter API v2 Basic tier (Bearer token, no tweepy needed).
Searches using social_trend_name + related_terms as queries.
Always runs with 7-day window (Basic tier hard limit).

Run:
  python src/trend_radar/collectors/twitter.py
  python src/trend_radar/collectors/twitter.py --terms src/trend_radar/data/mock/terms.json
  python src/trend_radar/collectors/twitter.py --output src/trend_radar/data/raw/twitter_test.json
"""

import os
import requests
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BEARER_TOKEN       = os.getenv("TWITTER_BEARER_TOKEN")
BASE_URL           = "https://api.twitter.com/2"
_BASE_DIR          = Path(__file__).resolve().parent.parent
DEFAULT_TERMS_PATH = str(_BASE_DIR / "data" / "mock" / "terms.json")
WINDOW_HOURS       = 167  # 6d 23h — 1h buffer so start_time never drifts past the 7d API limit
MAX_RESULTS        = 100   # per request, API max for recent search
SLEEP_BETWEEN      = 1.5   # seconds — Basic tier: 180 req/15min


def headers():
    return {"Authorization": f"Bearer {BEARER_TOKEN}"}


def search_tweets(query: str, start_time: str, max_results: int, _retries: int = 0) -> list[dict]:
    params = {
        "query": f"{query} -is:retweet lang:en",
        "max_results": max_results,
        "start_time": start_time,
        "tweet.fields": "created_at,public_metrics,author_id",
        "sort_order": "relevancy",
    }
    resp = requests.get(f"{BASE_URL}/tweets/search/recent", headers=headers(), params=params)

    if resp.status_code == 429:
        if _retries >= 3:
            print(f" [429 — gave up after {_retries} retries]", end=" ", flush=True)
            return []
        reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 60))
        wait = max(reset - int(time.time()), 1)
        print(f" [429 — waiting {wait}s]", end=" ", flush=True)
        time.sleep(wait)
        return search_tweets(query, start_time, max_results, _retries + 1)

    if resp.status_code != 200:
        print(f" [HTTP {resp.status_code}: {resp.text[:120]}]", end=" ", flush=True)
        return []

    # Proactively pause when the rate-limit window is nearly exhausted
    remaining = int(resp.headers.get("x-rate-limit-remaining", 999))
    if remaining <= 1:
        reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 60))
        wait = max(reset - int(time.time()), 1)
        print(f" [limit almost gone ({remaining} left) — waiting {wait}s]", end=" ", flush=True)
        time.sleep(wait)

    return resp.json().get("data") or []


def build_tweet_item(tweet: dict) -> dict:
    m = tweet.get("public_metrics", {})
    return {
        "tweet_id": tweet["id"],
        "text": tweet.get("text", ""),
        "author_id": tweet.get("author_id", ""),
        "created_at": tweet.get("created_at", ""),
        "like_count": m.get("like_count", 0),
        "retweet_count": m.get("retweet_count", 0),
        "reply_count": m.get("reply_count", 0),
        "quote_count": m.get("quote_count", 0),
        "engagement": m.get("like_count", 0) + m.get("retweet_count", 0) + m.get("reply_count", 0) + m.get("quote_count", 0),
    }


def collect_term(term: dict) -> dict:
    queries = [term["social_trend_name"]] + term.get("related_terms", [])
    start_time = (datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_tweets = []
    seen_ids: set[str] = set()

    for query in queries:
        tweets = search_tweets(query, start_time, MAX_RESULTS)
        for t in tweets:
            if t["id"] not in seen_ids:
                seen_ids.add(t["id"])
                item = build_tweet_item(t)
                item["matched_query"] = query
                all_tweets.append(item)
        time.sleep(SLEEP_BETWEEN)

    all_tweets.sort(key=lambda x: x["engagement"], reverse=True)

    total_likes    = sum(t["like_count"] for t in all_tweets)
    total_retweets = sum(t["retweet_count"] for t in all_tweets)
    n = len(all_tweets)

    return {
        "term_id":           term["id"],
        "social_trend_name": term["social_trend_name"],
        "underlying_topic":  term["underlying_topic"],
        "everme_category":   term.get("everme_category", ""),
        "queries_used":      queries,
        "window":            "7d",
        "tweet_count":       n,
        "total_likes":       total_likes,
        "total_retweets":    total_retweets,
        "avg_likes":         round(total_likes / n, 2) if n else 0,
        "avg_retweets":      round(total_retweets / n, 2) if n else 0,
        "top_likes":         max((t["like_count"] for t in all_tweets), default=0),
        "top_retweets":      max((t["retweet_count"] for t in all_tweets), default=0),
        "tweets":            all_tweets,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Twitter/X validator for Mini-RAG (7-day window)")
    parser.add_argument("--terms",  default=DEFAULT_TERMS_PATH)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    if not BEARER_TOKEN:
        raise SystemExit("Error: TWITTER_BEARER_TOKEN not set in .env")

    with open(args.terms, encoding="utf-8") as f:
        all_terms = json.load(f)

    now = datetime.now(timezone.utc)

    print(f"Twitter/X validator — Basic tier (7-day window)")
    print(f"Terms: {len(all_terms)} | Queries per term: social_trend_name + related_terms")
    print()

    results = []
    for i, term in enumerate(all_terms, 1):
        n_queries = 1 + len(term.get("related_terms", []))
        print(f"[{i:>2}/{len(all_terms)}] {term['social_trend_name']:<30} ({n_queries} queries) ...", end=" ", flush=True)
        result = collect_term(term)
        results.append(result)
        n = result["tweet_count"]
        if n == 0:
            print("no tweets found")
        else:
            print(f"{n} tweets | avg likes: {result['avg_likes']:,} | top RT: {result['top_retweets']:,}")

    output = {
        "source":       "twitter",
        "collected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window":       "7d",
        "term_count":   len(results),
        "note":         "Basic tier — last 7 days only.",
        "terms":        results,
    }

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = _BASE_DIR / "data" / "raw" / f"twitter_{now.strftime('%Y-%m-%d')}.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nDone → {output_path}")
    print("\nSummary (sorted by tweet count):")
    for r in sorted(results, key=lambda x: x["tweet_count"], reverse=True):
        bar = "█" * min(r["tweet_count"] // 5, 20)
        print(f"  {r['tweet_count']:>4} tweets  {bar:<20}  avg ♥{r['avg_likes']:>5}  top RT {r['top_retweets']:>5}  {r['social_trend_name']}")


if __name__ == "__main__":
    main()
