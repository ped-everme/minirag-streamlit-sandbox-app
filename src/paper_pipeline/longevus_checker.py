"""
Longevus Checker — Phase 3 of the EMERGING track.

For each pending term in the catalog, checks whether its underlying_topic
is already covered in the Longevus knowledge base. If covered, all queries
for that term are marked longevus_covered and skipped by paper_search.

Check mechanism: reads data/longevus_topics.json (a snapshot of Longevus'
src/util/info.json — the source-of-truth manifest of indexed topics).
No API calls, no GCP credentials required.

Match strategy (two tiers):
  covered        — normalized underlying_topic matches a code_topic exactly
                   → mark_longevus_covered; paper_search skips the term
  possible_match — one string is a substring of the other
                   → logged for review but NOT marked as covered (safe default)

The name mismatch problem: Longevus topics use compound names like
"cryotherapy_and_thermogenesis" or "stress_management_and_cortisol_regulation"
that don't align with our underlying_topic values ("Cold Thermogenesis",
"Cortisol / Stress Hormones"). Exact match handles clear cases; fuzzy/LLM
matching is noted as a future improvement in plan.md.

Usage:
  python src/paper_pipeline/longevus_checker.py --signal signal_DATE.json
  python src/paper_pipeline/longevus_checker.py  # auto-discovers latest signal
"""

import argparse
import json
import re
import sys
from pathlib import Path

from catalog import Catalog

BASE_DIR = Path(__file__).resolve().parent
LONGEVUS_TOPICS_PATH = BASE_DIR / "data" / "longevus_topics.json"


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """
    "Cortisol / Stress Hormones" → "cortisol_stress_hormones"
    "GLP-1 Receptor Agonists"   → "glp_1_receptor_agonists"
    """
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


# ── Topic index ────────────────────────────────────────────────────────────────

def _load_longevus_topics(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["topics"]


def _build_topic_index(topics: list[dict]) -> set[str]:
    """Set of all code_topic strings for O(1) exact lookup."""
    return {t["code_topic"] for t in topics}


def _find_match(normalized: str, topic_index: set[str]) -> tuple[str, str | None]:
    """
    Returns (match_type, matched_code_topic).
    match_type: "covered" | "possible_match" | "not_found"
    """
    if normalized in topic_index:
        return "covered", normalized

    # Substring check — "cold_thermogenesis" ⊂ "cryotherapy_and_thermogenesis"
    for code_topic in topic_index:
        if normalized in code_topic or code_topic in normalized:
            return "possible_match", code_topic

    return "not_found", None


# ── Signal helpers ────────────────────────────────────────────────────────────

def _latest_signal() -> Path | None:
    out_dir = BASE_DIR.parent / "trend_radar" / "data" / "output"
    files = sorted(out_dir.glob("signal_*.json"))
    return files[-1] if files else None


def _load_signal_topics(signal_path: Path) -> dict[str, dict]:
    """Returns {term_id: {underlying_topic, social_trend_name, everme_category}}."""
    with open(signal_path, encoding="utf-8") as f:
        signal = json.load(f)
    return {
        t["term_id"]: {
            "underlying_topic": t.get("underlying_topic", ""),
            "social_trend_name": t.get("social_trend_name", ""),
            "everme_category": t.get("everme_category", ""),
        }
        for t in signal.get("terms", [])
    }


# ── Pipeline phase ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Longevus Checker — phase 3 of EMERGING track")
    parser.add_argument("--signal", default=None, metavar="PATH",
                        help="Path to signal_DATE.json (default: latest in trend_radar/data/output/)")
    parser.add_argument("--catalog", default=None, metavar="PATH",
                        help="Path to catalog.json (default: data/catalog.json)")
    args = parser.parse_args()

    # Load resources
    if not LONGEVUS_TOPICS_PATH.exists():
        sys.exit(f"longevus_topics.json not found: {LONGEVUS_TOPICS_PATH}")
    topics = _load_longevus_topics(LONGEVUS_TOPICS_PATH)
    topic_index = _build_topic_index(topics)

    signal_path = Path(args.signal) if args.signal else _latest_signal()
    if not signal_path or not signal_path.exists():
        sys.exit(f"Signal file not found: {signal_path}")
    signal_topics = _load_signal_topics(signal_path)

    catalog_path = Path(args.catalog) if args.catalog else None
    cat = Catalog.load(catalog_path) if catalog_path else Catalog.load()

    pending = cat.pending_longevus_check()

    print("Longevus Checker")
    print(f"  signal          {signal_path.name}")
    print(f"  longevus topics {len(topic_index)} indexed topics")
    print(f"  pending check   {len(pending)} catalog entries")
    print()

    if not pending:
        print("Nothing to check.")
        return

    # Group entries by parent_term_id
    by_term: dict[str, list[dict]] = {}
    for entry in pending:
        tid = entry["parent_term_id"]
        by_term.setdefault(tid, []).append(entry)

    covered_count = possible_count = not_found_count = 0

    for term_id, entries in by_term.items():
        term_info = signal_topics.get(term_id, {})
        underlying = term_info.get("underlying_topic", term_id)
        social_name = term_info.get("social_trend_name", term_id)
        normalized = _normalize(underlying)
        match_type, matched = _find_match(normalized, topic_index)

        if match_type == "covered":
            print(f"  ✓ COVERED       {social_name}")
            print(f"                  '{underlying}' → '{matched}'")
            for entry in entries:
                cat.mark_longevus_covered(entry["query"])
            covered_count += 1

        elif match_type == "possible_match":
            print(f"  ~ POSSIBLE      {social_name}")
            print(f"                  '{underlying}' ~ '{matched}'  (not marked covered — review manually)")
            for entry in entries:
                cat.mark_longevus_checked(entry["query"])
            possible_count += 1

        else:
            print(f"  · NOT FOUND     {social_name}")
            print(f"                  '{underlying}' → no match in Longevus")
            for entry in entries:
                cat.mark_longevus_checked(entry["query"])
            not_found_count += 1

    cat.save()

    print()
    print(f"  covered         {covered_count} terms ({covered_count * (len(pending) // max(len(by_term), 1))} entries skipped)")
    print(f"  possible match  {possible_count} terms  ← review longevus_topics.json")
    print(f"  not found       {not_found_count} terms  → will proceed to paper_search")
    print(f"\nDone → {cat.path}")


if __name__ == "__main__":
    main()
