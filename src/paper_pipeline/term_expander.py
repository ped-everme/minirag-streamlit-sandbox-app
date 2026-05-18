"""
Term Expander — Step 1 of the EMERGING track.

Reads EMERGING (and HYPED+EMERGING) terms from signal_DATE.json,
calls GPT to generate academically searchable queries per term,
and writes the expansion to data/expansions/expansion_DATE.json.

Usage:
  python src/paper_pipeline/term_expander.py
  python src/paper_pipeline/term_expander.py --signal src/trend_radar/data/output/signal_2026-05-13.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

ELIGIBLE = {"EMERGING", "HYPED + EMERGING"}
MODEL = "gpt-4.1-nano"

_SYSTEM = """You are an academic literature search specialist. Given a social media health trend, identify the underlying scientific concepts and generate search queries suitable for academic databases (OpenAlex, PubMed, Google Scholar).

Query types:
1. ATOMIC: one identifiable compound, ingredient, drug, biomarker, or named intervention — just the name alone (e.g. "BPC-157", "semaglutide", "cold water immersion").
2. COMPOUND: an atomic concept paired with a mechanism, outcome, or context (e.g. "BPC-157 tissue repair", "semaglutide cardiovascular outcomes").
3. CONCEPT: the broader scientific framing — either the established clinical condition (e.g. "Cushing's syndrome" for "cortisol face", "obstructive sleep apnea" for "mouth taping") or the underlying physiological mechanism (e.g. "thermogenic response to cold exposure", "HPA axis dysregulation"). Use the recognised scientific name, not the social label.

Rules:
- DROP anything that is purely a social media label with no scientific meaning (e.g. "Wolverine Stack", "cortisol face", "mouth tape at night").
- Use underlying_topic and everme_category as domain hints.
- Target 3–7 total queries, prioritising specificity and likelihood of returning peer-reviewed results.
- Respond ONLY with valid JSON — no markdown, no explanation.

Output schema:
{
  "term_id": "<same as input>",
  "queries": [
    {"query": "...", "type": "atomic|compound|concept", "confidence": "high|medium|low"}
  ],
  "dropped": [
    {"value": "...", "reason": "..."}
  ]
}"""


def _latest_signal() -> Path | None:
    out_dir = BASE_DIR.parent / "trend_radar" / "data" / "output"
    files = sorted(out_dir.glob("signal_*.json"))
    return files[-1] if files else None


def _expand_term(term: dict, client: OpenAI) -> dict:
    payload = {
        "term_id": term["term_id"],
        "social_trend_name": term["social_trend_name"],
        "underlying_topic": term["underlying_topic"],
        "everme_category": term["everme_category"],
        "related_terms": term.get("related_terms", []),
    }

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                response_format={"type": "json_object"},
                temperature=0.1,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            )
            result = json.loads(response.choices[0].message.content)
            result["term_id"] = term["term_id"]
            return result
        except (json.JSONDecodeError, KeyError) as exc:
            if attempt == 0:
                continue
            print(f"  WARNING: failed to expand {term['term_id']}: {exc}", file=sys.stderr)
            return {"term_id": term["term_id"], "queries": [], "dropped": [], "error": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Term Expander — step 1 of EMERGING track")
    parser.add_argument("--signal", default=None, metavar="PATH",
                        help="Path to signal_DATE.json (default: latest in trend_radar/data/output/)")
    parser.add_argument("--output", default=None, metavar="PATH",
                        help="Output path (default: data/expansions/expansion_DATE.json)")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY not set.")
    client = OpenAI(api_key=api_key)

    signal_path = Path(args.signal) if args.signal else _latest_signal()
    if not signal_path or not signal_path.exists():
        sys.exit(f"Signal file not found: {signal_path}")

    with open(signal_path, encoding="utf-8") as f:
        signal = json.load(f)

    eligible = [t for t in signal["terms"] if t["classification"] in ELIGIBLE]

    print("Term Expander")
    print(f"  signal        {signal_path.name}")
    print(f"  eligible      {len(eligible)} terms  ({' | '.join(ELIGIBLE)})")
    print()

    if not eligible:
        print("No eligible terms — nothing to expand.")
        return

    results = []
    for term in eligible:
        print(f"  {term['social_trend_name']:<35} ({term['classification']})")
        expansion = _expand_term(term, client)
        q_count = len(expansion.get("queries", []))
        d_count = len(expansion.get("dropped", []))
        for q in expansion.get("queries", []):
            tag = f"[{q['type'][:3]}]"
            print(f"    {tag} {q['query']}")
        if expansion.get("dropped"):
            print(f"    dropped: {', '.join(d['value'] for d in expansion['dropped'])}")
        print()
        results.append(expansion)

    run_date = datetime.now(timezone.utc)
    date_str = run_date.strftime("%Y-%m-%d")

    if args.output:
        out_path = Path(args.output)
    else:
        out_dir = BASE_DIR / "data" / "expansions"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"expansion_{date_str}.json"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "schema_version": "1.0",
        "generated_at": run_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "signal_file": signal_path.name,
        "terms_expanded": len(results),
        "expansions": results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Done → {out_path}")


if __name__ == "__main__":
    main()
