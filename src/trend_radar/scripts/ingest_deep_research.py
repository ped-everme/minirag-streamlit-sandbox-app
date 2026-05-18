"""
Ingests deep-research CSV reports from Rafael into a terms.json ready for
the Mini-RAG pipeline collectors.

Reads the latest *merged_trends*.csv from:
  src/trend_radar/data/deep_research_reports/three_months/
  src/trend_radar/data/deep_research_reports/twelve_months/

Calls GPT-4.1-nano once (batch) to:
  - clean social_trend_name for searching
  - select 3-4 viral/branded related_terms (drops generic descriptions)
  - infer everme_category
  - deduplicate terms that appear in both horizons
  - assign a stable kebab-case id

Output: src/trend_radar/data/terms.json  (pass --terms <path> to each collector)

Usage:
  python src/trend_radar/scripts/ingest_deep_research.py
  python src/trend_radar/scripts/ingest_deep_research.py --output src/trend_radar/data/mock/terms.json
  python src/trend_radar/scripts/ingest_deep_research.py --three-months path/to/file.csv
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_BASE_DIR = Path(__file__).resolve().parent.parent
THREE_MONTHS_DIR = _BASE_DIR / "data" / "deep_research_reports" / "three_months"
TWELVE_MONTHS_DIR = _BASE_DIR / "data" / "deep_research_reports" / "twelve_months"
DEFAULT_OUTPUT = _BASE_DIR / "data" / "terms.json"
MODEL = "gpt-4.1-nano"

SYSTEM_PROMPT = """\
You are preparing health & wellness trend data for a social media monitoring pipeline.

I will give you a JSON array of trend rows from two research reports:
- horizon "3m"  = 3-month report (more viral, TikTok-driven trends)
- horizon "12m" = 12-month report (more structural, long-term trends)

Some trends may appear in both reports under slightly different names or phrasings.
When you recognise two rows as the same trend, merge them into a single entry.

Return a JSON object with key "terms" — an array where each element is:
{
  "id":                 "kebab-case id using the viral/branded name (e.g. fibremaxxing, chinamaxxing, glp1-stack-economy)",
  "social_trend_name":  "clean display name — no surrounding quotes, no parentheticals",
  "underlying_topic":   "2-5 word health concept label (e.g. Dietary Fiber, Peptide Therapy, Cold Thermogenesis)",
  "everme_category":    "one of: Supplements | Nutrition | Fitness | Wellness Therapies | Mental Health | Sleep & Recovery | Longevity | Biohacking | Weight Management | Prescription Medications",
  "search_query":       "best single search query for YouTube/TikTok/Google — short natural phrase WITH spaces, no quotes or parentheses, use the viral/branded form (e.g. 'silent walking', 'cold plunge', 'fibremaxxing')",
  "related_terms":      ["1-3 branded/viral/searchable terms — EXCLUDE generic descriptions like gut health, energy boost, weight loss, morning routine, mindful eating, portion control, better sleep. Return fewer than 3 if not enough specific terms exist."],
  "hashtags":           ["#hashtag1", "#hashtag2"],
  "horizon":            "3m | 12m | both",
  "source_rank_3m":     1,
  "source_rank_12m":    null
}

Rules for related_terms:
- KEEP: challenge names (50 jumps challenge), branded protocols (wolverine stack, wim hof method), viral coined terms (fibremaxxing, chinamaxxing), specific named products/compounds (BPC-157, CGM, NAD+)
- DROP: generic health descriptions (gut health, energy boost, bone health, weight loss, morning routine, mindful eating)
"""


def find_merged_csv(directory: Path) -> Path | None:
    matches = sorted(directory.glob("*merged*.csv"))
    return matches[-1] if matches else None


def read_csv_rows(path: Path, horizon: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rank_raw = row.get("Rank", "").strip()
            name = row.get("Social trend name (from input)", "").strip()
            if not name:
                continue
            rows.append({
                "horizon": horizon,
                "rank": int(rank_raw) if rank_raw.isdigit() else 0,
                "social_trend_name": name,
                "underlying_topic_raw": row.get("Underlying topic (from input)", "").strip(),
                "related_terms_raw": row.get("Related terms (clean)", "").strip(),
                "hashtags_raw": row.get("Related hashtags", "").strip(),
            })
    return rows


def _merge_ranks(existing: dict, incoming: dict) -> None:
    if existing.get("source_rank_3m") is None and incoming.get("source_rank_3m") is not None:
        existing["source_rank_3m"] = incoming["source_rank_3m"]
    if existing.get("source_rank_12m") is None and incoming.get("source_rank_12m") is not None:
        existing["source_rank_12m"] = incoming["source_rank_12m"]


def dedup_terms(terms: list[dict]) -> list[dict]:
    """
    Two-pass dedup:
      1. by id          — catches exact LLM duplicates
      2. by search_query — catches same-trend / different-id cases
    Then fixes horizon for any entry where both ranks are populated.
    """
    # Pass 1 — by id
    by_id: dict[str, dict] = {}
    for t in terms:
        tid = t["id"]
        if tid not in by_id:
            by_id[tid] = t
        else:
            _merge_ranks(by_id[tid], t)

    # Pass 2 — by search_query (normalised to lowercase, no punctuation)
    def norm(s: str) -> str:
        return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()

    by_query: dict[str, dict] = {}
    for t in by_id.values():
        key = norm(t.get("search_query", t["id"]))
        if key not in by_query:
            by_query[key] = t
        else:
            _merge_ranks(by_query[key], t)

    # Fix horizon and cap related_terms
    result = []
    for t in by_query.values():
        if t.get("source_rank_3m") and t.get("source_rank_12m"):
            t["horizon"] = "both"
        t["related_terms"] = t.get("related_terms", [])[:3]
        result.append(t)

    return result


CLEANUP_PROMPT = """\
You are cleaning related_terms arrays for a social media search pipeline.

For each entry, rewrite related_terms keeping only items that are specific, branded, or viral.
Return 1-3 items. If fewer than 3 specific terms exist, return only what's valid — never invent filler.

REMOVE (generic — will return useless search results):
  "energy boost", "bone and muscle health", "morning routine", "better sleep",
  "water intake", "weight loss", "gut health", "mindful eating", "portion control",
  "lifestyle reset", "short bursts of activity", "functional mobility",
  "balance over restriction", "realistic wellness habits", "flexible routine",
  "grind season", "wellness-driven hibernation period", "lazy girl summer",
  "dopamine", "earbuds", "stroll in silence"

KEEP (specific, branded, searchable):
  challenge names ("50 jumps challenge", "75 Hard"), branded protocols ("Wolverine stack",
  "Snake Diet", "Wim Hof method"), viral coined terms ("fibremaxxing", "chinamaxxing",
  "becomingChinese"), specific compounds/products ("BPC-157", "NMN", "Oura Ring",
  "Glucose Goddess"), platform-specific hashtag terms without the #

If removing leaves fewer than 3 items, return only what remains — do NOT invent filler terms.

Return JSON: {"terms": [{"id": "...", "related_terms": ["a", "b", "c"]}]}
"""


def cleanup_related_terms(terms: list[dict], client: OpenAI) -> list[dict]:
    payload = [{"id": t["id"], "related_terms": t.get("related_terms", [])} for t in terms]
    response = client.chat.completions.create(
        model=MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": CLEANUP_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.1,
    )
    cleaned = {
        entry["id"]: entry["related_terms"]
        for entry in json.loads(response.choices[0].message.content)["terms"]
    }
    for t in terms:
        if t["id"] in cleaned:
            t["related_terms"] = cleaned[t["id"]][:3]
    return terms


def call_openai(rows: list[dict], client: OpenAI) -> list[dict]:
    payload = [
        {
            "horizon": r["horizon"],
            "rank": r["rank"],
            "social_trend_name": r["social_trend_name"],
            "underlying_topic_raw": r["underlying_topic_raw"],
            "related_terms_raw": r["related_terms_raw"],
            "hashtags_raw": r["hashtags_raw"],
        }
        for r in rows
    ]

    response = client.chat.completions.create(
        model=MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.1,
    )

    result = json.loads(response.choices[0].message.content)
    return result["terms"]


def main():
    parser = argparse.ArgumentParser(
        description="Convert Rafael's deep-research CSVs → terms.json for Mini-RAG collectors"
    )
    parser.add_argument("--three-months", default=None, metavar="PATH",
                        help="Path to 3-month merged CSV (auto-discovered if omitted)")
    parser.add_argument("--twelve-months", default=None, metavar="PATH",
                        help="Path to 12-month merged CSV (auto-discovered if omitted)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), metavar="PATH",
                        help=f"Output path for terms.json (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    path_3m = Path(args.three_months) if args.three_months else find_merged_csv(THREE_MONTHS_DIR)
    path_12m = Path(args.twelve_months) if args.twelve_months else find_merged_csv(TWELVE_MONTHS_DIR)

    if not path_3m and not path_12m:
        sys.exit("No merged CSV files found. Pass --three-months and/or --twelve-months.")

    rows: list[dict] = []
    if path_3m:
        if not path_3m.exists():
            sys.exit(f"File not found: {path_3m}")
        print(f"  3m  {path_3m}")
        rows += read_csv_rows(path_3m, "3m")

    if path_12m:
        if not path_12m.exists():
            sys.exit(f"File not found: {path_12m}")
        print(f"  12m {path_12m}")
        rows += read_csv_rows(path_12m, "12m")

    if not rows:
        sys.exit("No rows parsed from CSV files.")

    print(f"\n{len(rows)} rows total → calling {MODEL}...")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY not set in environment.")

    client = OpenAI(api_key=api_key)
    terms = call_openai(rows, client)
    terms = dedup_terms(terms)

    print(f"Cleaning related_terms...")
    terms = cleanup_related_terms(terms, client)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(terms, f, ensure_ascii=False, indent=2)

    print(f"\nDone → {output_path}  ({len(terms)} terms)\n")
    for t in terms:
        h = t.get("horizon", "?")
        ranks = []
        if t.get("source_rank_3m"):
            ranks.append(f"3m#{t['source_rank_3m']}")
        if t.get("source_rank_12m"):
            ranks.append(f"12m#{t['source_rank_12m']}")
        rank_str = " ".join(ranks)
        print(f"  [{h:4}]  {t['id']:<40}  {rank_str}")

    print(f"\nRun collectors with:  --terms {output_path}")


if __name__ == "__main__":
    main()
