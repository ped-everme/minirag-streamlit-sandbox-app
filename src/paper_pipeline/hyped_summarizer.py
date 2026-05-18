"""
Hyped Summarizer — Phase H of the HYPED track.

For each HYPED term, generates a comprehensive Markdown document using
Gemini 2.5 Flash with Google Search grounding. The document covers what
the trend is, why it's going viral, what's behind the claims, who it's
for, and what a health-conscious person should know — grounded in current
web sources, not papers.

Authentication: Google service account via
  .secrets/longevity-league-dev-new.json (Vertex AI, project longevity-league-dev)

Input:
  signal_DATE.json  — term context (social_trend_name, underlying_topic,
                      everme_category, signal_drivers, related_terms, classification)

Output:
  data/documents/<term_id>_hyped_<DATE>.md  — one document per term

Document structure:
  # <social_trend_name>
  ## What is this trend
  ## Why it's going viral right now
  ## What's behind the claims
  ## Who is this for
  ## What to watch out for
  ## Bottom line

Idempotent: existing documents are not regenerated unless --regenerate is passed.

Usage:
  python src/paper_pipeline/hyped_summarizer.py --signal path/to/signal.json
  python src/paper_pipeline/hyped_summarizer.py --regenerate
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import vertexai
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

BASE_DIR    = Path(__file__).resolve().parent
DOCS_DIR    = BASE_DIR / "data" / "documents"
SA_PATH     = BASE_DIR.parent.parent / ".secrets" / "longevity-league-dev-new.json"
GCP_PROJECT = "longevity-league-dev"
GCP_REGION  = "us-central1"
MODEL       = "gemini-2.5-flash"

ELIGIBLE = {"HYPED", "HYPED + EMERGING"}


def _init_gemini() -> genai.Client:
    if SA_PATH.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(SA_PATH)
    vertexai.init(project=GCP_PROJECT, location=GCP_REGION)
    return genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_REGION)


# ── Signal helpers ────────────────────────────────────────────────────────────

def _latest_signal() -> Path | None:
    out_dir = BASE_DIR.parent / "trend_radar" / "data" / "output"
    files = sorted(out_dir.glob("signal_*.json"))
    return files[-1] if files else None


def _load_hyped_terms(signal_path: Path) -> list[dict]:
    sig = json.loads(signal_path.read_text(encoding="utf-8"))
    return [
        t for t in sig.get("terms", [])
        if t.get("classification") in ELIGIBLE
    ]


# ── Document generation ───────────────────────────────────────────────────────

_SYSTEM = """\
You are a health trend analyst and science communicator. You write for a
health-conscious consumer audience — people who are curious, smart, and
want to make informed decisions about wellness trends. You are not a
cheerleader for trends, but you are also not dismissive. You explain what
is real, what is hype, what the evidence actually says, and what someone
should realistically expect. You use current web sources to ground your
writing and you cite them naturally in the text.\
"""

def _build_prompt(term: dict) -> str:
    social_name    = term.get("social_trend_name", "")
    underlying     = term.get("underlying_topic", "")
    category       = term.get("everme_category", "")
    signal_drivers = term.get("signal_drivers", [])
    related_terms  = term.get("related_terms", [])

    drivers_str = "\n".join(f"- {d}" for d in signal_drivers) if signal_drivers else "n/a"
    related_str = ", ".join(related_terms) if related_terms else "n/a"

    return f"""\
Research and write a comprehensive health trend document about the following trend.
Use Google Search to find current information — recent news, social media context,
expert opinions, and any relevant studies or clinical context.

TREND INFORMATION:
  Social trend name : {social_name}
  Underlying topic  : {underlying}
  EverMe category   : {category}
  Related terms     : {related_str}

  Why it is trending right now (signal drivers):
{drivers_str}

Write a comprehensive, detailed Markdown document using EXACTLY this structure.
Each section should be thorough — this document will be used as a reference resource,
so depth and completeness matter. Do not be brief. Each section should have multiple
paragraphs where appropriate.

# {social_name}

## What is this trend
[3–5 paragraphs. Explain what the trend is in detail. What do people actually do?
What products, protocols, or practices are involved? How did it start and evolve?
Include context about related terms: {related_str}. Explain the underlying topic
({underlying}) accessibly.]

## Why it's going viral right now
[3–4 paragraphs. Use current search results to explain what specifically is driving
this trend NOW. Which influencers, celebrities, or platforms are amplifying it?
What recent events, products, or studies sparked the current wave? What is the
emotional or psychological appeal that makes people share it?]

## What's behind the claims
[4–6 paragraphs. What do proponents claim this trend does? For each major claim:
(a) explain the biological or physiological mechanism being invoked,
(b) assess whether the claim is plausible, overstated, or unsupported,
(c) mention any relevant research, expert commentary, or clinical context found online.
Be nuanced — some claims may have real merit while others are exaggerated.]

## Who is this for
[2–3 paragraphs. Which populations might genuinely benefit? Are there specific
conditions, goals, or contexts where this makes more sense? Who is already doing
this (athletes, biohackers, general public)? What level of commitment or cost
does it typically involve?]

## What to watch out for
[2–4 paragraphs. Risks, contraindications, or red flags. What do critics or
skeptics say? Are there safety concerns? Quality/regulation issues with products?
Common misconceptions that could lead people astray? Financial or time costs
that may not be worth it for everyone?]

## Bottom line
[2–3 paragraphs. An honest, balanced summary. Given everything above, what
should a health-conscious person take away? What is the realistic expectation
of benefit? What would you tell a friend asking "should I try this?" Be direct
and honest — neither dismissive nor promotional.]
"""


def _generate_document(client: genai.Client, term: dict) -> str:
    prompt = _build_prompt(term)
    resp = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM,
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.4,
        ),
    )
    return resp.text.strip()


# ── Pipeline phase ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Hyped Summarizer — phase H of HYPED track")
    parser.add_argument("--signal",     default=None, metavar="PATH")
    parser.add_argument("--docs-dir",   default=str(DOCS_DIR), metavar="PATH")
    parser.add_argument("--regenerate", action="store_true",
                        help="Regenerate documents even if they already exist")
    args = parser.parse_args()

    signal_path = Path(args.signal) if args.signal else _latest_signal()
    if not signal_path or not signal_path.exists():
        sys.exit(f"Signal file not found: {signal_path}")

    docs_dir = Path(args.docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)

    terms = _load_hyped_terms(signal_path)
    if not terms:
        print("No HYPED terms found in signal — nothing to summarize.")
        return

    client = _init_gemini()
    today  = date.today().isoformat()

    print("Hyped Summarizer")
    print(f"  signal      {signal_path.name}")
    print(f"  terms       {len(terms)} HYPED terms")
    print(f"  model       {MODEL} (Google Search grounding)")
    print(f"  output      {docs_dir}")
    print()

    counts = {"generated": 0, "skipped": 0, "failed": 0}

    for term in terms:
        term_id     = term["term_id"]
        social_name = term.get("social_trend_name", term_id)
        out_path    = docs_dir / f"{term_id}_hyped_{today}.md"

        if out_path.exists() and not args.regenerate:
            print(f"  · [exists]      {social_name}")
            counts["skipped"] += 1
            continue

        print(f"  ✓ [generating]  {social_name}")
        try:
            doc = _generate_document(client, term)
            out_path.write_text(doc, encoding="utf-8")
            counts["generated"] += 1
        except Exception as exc:
            print(f"    ✗ failed: {exc}")
            counts["failed"] += 1

    print()
    print(f"  generated   {counts['generated']}")
    print(f"  skipped     {counts['skipped']}  (already exist)")
    print(f"  failed      {counts['failed']}")
    print(f"\nDone → {docs_dir}")


if __name__ == "__main__":
    main()
