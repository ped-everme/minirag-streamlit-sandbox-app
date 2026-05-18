"""
Document Generator — Phase 8 of the EMERGING track.

For each EMERGING term, generates a structured Markdown document that explains
the trend and its scientific backing, based on the top-scored downloaded papers.

Input:
  data/papers_meta.json   — papers with scores (from paper_scorer)
  signal_DATE.json        — term context (social_trend_name, underlying_topic,
                            everme_category, signal_drivers, related_terms)

Output:
  data/documents/<term_id>_<DATE>.md  — one document per term

Document structure:
  # <social_trend_name>
  ## What is this trend
  ## Scientific evidence
  ## Key papers  (title, year, venue, score, key finding)
  ## Evidence quality summary
  ## Practical implications

Each document is generated with a single Gemini call. Context includes:
  - Term metadata from signal
  - related_terms (for broader search context)
  - Top papers sorted by final_score: title, year, abstract, score breakdown
    and rationale from paper_scorer (pre-computed methodology summaries)

Idempotent: existing documents are not regenerated unless --regenerate is passed.

Usage:
  python src/paper_pipeline/doc_generator.py --signal path/to/signal.json
  python src/paper_pipeline/doc_generator.py --regenerate
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
META_PATH   = BASE_DIR / "data" / "papers_meta.json"
DOCS_DIR    = BASE_DIR / "data" / "documents"
SA_PATH     = BASE_DIR.parent.parent / ".secrets" / "longevity-league-dev-new.json"
GCP_PROJECT = "longevity-league-dev"
GCP_REGION  = "us-central1"
MODEL       = "gemini-2.5-flash"
TOP_PAPERS  = 8    # max papers per term fed to LLM

if SA_PATH.exists():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(SA_PATH)
vertexai.init(project=GCP_PROJECT, location=GCP_REGION)
client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_REGION)

ELIGIBLE = {"EMERGING", "HYPED + EMERGING"}


# ── Signal helpers ────────────────────────────────────────────────────────────

def _latest_signal() -> Path | None:
    out_dir = BASE_DIR.parent / "trend_radar" / "data" / "output"
    files = sorted(out_dir.glob("signal_*.json"))
    return files[-1] if files else None


def _load_signal_terms(signal_path: Path) -> list[dict]:
    sig = json.loads(signal_path.read_text(encoding="utf-8"))
    return [t for t in sig.get("terms", []) if t.get("classification") in ELIGIBLE]


# ── Paper context builder ─────────────────────────────────────────────────────

def _papers_for_term(meta: dict, term_id: str) -> list[dict]:
    """Return top-scored scored papers for a term, sorted by final_score desc."""
    papers = [
        p for p in meta.values()
        if term_id in p.get("parent_term_ids", [])
        and p.get("score_status") == "scored"
    ]
    return sorted(papers, key=lambda p: p.get("final_score", 0), reverse=True)[:TOP_PAPERS]


def _paper_context(paper: dict) -> str:
    """Format one paper as a compact context block for the LLM prompt."""
    title   = paper.get("title", "Unknown title")
    year    = paper.get("year", "n/a")
    venue   = paper.get("venue") or "unknown venue"
    score   = paper.get("final_score", 0)
    authors = paper.get("authors", [])
    author_str = ", ".join(a.get("name", "") for a in authors[:3])
    if len(authors) > 3:
        author_str += " et al."
    abstract   = (paper.get("abstract") or "").strip()
    rationale  = paper.get("score_rationale") or {}

    # Pull key findings from scorer rationale (pre-computed LLM summaries)
    hoe_note = rationale.get("HoE", {}).get("rationale", "")
    sd_note  = rationale.get("SD",  {}).get("rationale", "")
    ev_type  = rationale.get("HoE", {}).get("evidence_type", "")

    lines = [
        f"PAPER: {title}",
        f"  Authors: {author_str}",
        f"  Year: {year} | Venue: {venue}",
        f"  Evidence type: {ev_type} | Quality score: {score}/100",
    ]
    if abstract:
        lines.append(f"  Abstract: {abstract[:600]}")
    if hoe_note:
        lines.append(f"  Study design: {hoe_note}")
    if sd_note:
        lines.append(f"  Methodology: {sd_note}")
    return "\n".join(lines)


# ── LLM generation ────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a science communicator writing for a health-conscious consumer audience.
You translate peer-reviewed research into clear, evidence-based content that is
accurate, nuanced, and practical. You never overstate findings and always reflect
the actual quality and limitations of the evidence. This document will serve as
a comprehensive reference resource — depth, completeness, and accuracy matter.\
"""

def _generate_document(term: dict, papers: list[dict]) -> str:
    social_name    = term.get("social_trend_name", "")
    underlying     = term.get("underlying_topic", "")
    category       = term.get("everme_category", "")
    signal_drivers = term.get("signal_drivers", [])
    related_terms  = term.get("related_terms", [])

    drivers_str = "\n".join(f"- {d}" for d in signal_drivers) if signal_drivers else "n/a"
    related_str = ", ".join(related_terms) if related_terms else "n/a"
    papers_str  = "\n\n".join(_paper_context(p) for p in papers)
    n_papers    = len(papers)
    avg_score   = round(sum(p.get("final_score", 0) for p in papers) / n_papers, 1) if papers else 0

    prompt = f"""\
Write a comprehensive, detailed evidence document for the following health trend.
This document will be used as a reference resource for a health platform, so it
must be thorough. Each section should have multiple paragraphs where appropriate.

TREND INFORMATION:
  Social trend name : {social_name}
  Underlying topic  : {underlying}
  EverMe category   : {category}
  Related terms     : {related_str}
  Signal drivers (why this is trending now):
{drivers_str}

SCIENTIFIC PAPERS ({n_papers} papers, avg quality score {avg_score}/100):
{papers_str}

Write the document in Markdown using EXACTLY this structure:

# {social_name}

## What is this trend
[3–5 paragraphs. Explain what the trend is in depth — what people actually do,
what the underlying science topic ({underlying}) is, how it connects to the
related terms ({related_str}), and why it is emerging now. Explain the mechanism
being invoked by proponents. Do not hype, but be engaging and informative.]

## Scientific evidence
[5–8 paragraphs. Synthesise what the papers collectively say. For each major
claim or mechanism: describe the supporting studies (type, sample size, population,
duration), report specific findings and effect sizes where available, and note the
confidence level. Distinguish well-established findings from preliminary ones.
Reference individual papers by title where appropriate. Be specific — avoid vague
generalisations like "studies show".]

## Key papers
[One bullet per paper. Format:
**Title** (Year, Venue) — Quality score: X/100 — Evidence type: [RCT/cohort/etc]
Key finding: one concrete sentence stating what was found and the effect size or
clinical significance where reported.]

## Evidence quality assessment
[2–3 paragraphs. What is the overall strength of the evidence base? What study
designs dominate — and what does that mean for confidence in the findings? What
are the key limitations across papers (sample sizes, populations, duration, funding)?
What research gaps exist? What would a definitive study look like?]

## Mechanisms and biology
[2–4 paragraphs. Explain the biological or physiological mechanisms proposed to
explain how this intervention or approach works. What does current science say
about each mechanism — is it well-characterised, plausible but unproven, or
speculative? Mention any counterarguments or alternative explanations.]

## Who benefits and practical considerations
[2–3 paragraphs. Which populations does the evidence most support? Are there
responder subgroups (age, sex, baseline health status, fitness level)? What
protocols, dosages, or frequencies have the most evidence? What are realistic
expectations vs. marketing claims?]

## Cautions and limitations
[2–3 paragraphs. Risks, contraindications, or red flags from the evidence.
What are the known adverse effects or safety considerations? Who should be
cautious or avoid this? What are the quality or regulation issues if applicable?]

## Practical implications
[3–5 bullet points. Concrete, specific, actionable takeaways for a health-conscious
person. Each bullet should be grounded in the evidence — cite the basis for each
recommendation. Be honest about what the evidence supports vs. what requires
more caution.]
"""

    resp = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM,
            temperature=0.3,
        ),
    )
    return resp.text.strip()


# ── Pipeline phase ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Doc Generator — phase 8 of EMERGING track")
    parser.add_argument("--signal",     default=None, metavar="PATH")
    parser.add_argument("--meta",       default=str(META_PATH), metavar="PATH")
    parser.add_argument("--docs-dir",   default=str(DOCS_DIR), metavar="PATH")
    parser.add_argument("--regenerate", action="store_true",
                        help="Regenerate documents even if they already exist")
    args = parser.parse_args()

    signal_path = Path(args.signal) if args.signal else _latest_signal()
    if not signal_path or not signal_path.exists():
        sys.exit(f"Signal file not found: {signal_path}")

    meta_path = Path(args.meta)
    if not meta_path.exists():
        sys.exit(f"papers_meta.json not found: {meta_path}")

    docs_dir = Path(args.docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    terms = _load_signal_terms(signal_path)

    today = date.today().isoformat()

    print("Document Generator")
    print(f"  signal      {signal_path.name}")
    print(f"  terms       {len(terms)} EMERGING terms")
    print(f"  model       {MODEL}")
    print(f"  output      {docs_dir}")
    print()

    counts = {"generated": 0, "skipped": 0, "no_papers": 0}

    for term in terms:
        term_id     = term["term_id"]
        social_name = term.get("social_trend_name", term_id)
        out_path    = docs_dir / f"{term_id}_{today}.md"

        if out_path.exists() and not args.regenerate:
            print(f"  · [exists]      {social_name}")
            counts["skipped"] += 1
            continue

        papers = _papers_for_term(meta, term_id)
        if not papers:
            print(f"  – [no papers]   {social_name}")
            counts["no_papers"] += 1
            continue

        print(f"  ✓ [generating]  {social_name}  ({len(papers)} papers)")
        doc = _generate_document(term, papers)

        out_path.write_text(doc, encoding="utf-8")
        counts["generated"] += 1

    print()
    print(f"  generated   {counts['generated']}")
    print(f"  skipped     {counts['skipped']}  (already exist)")
    print(f"  no papers   {counts['no_papers']}  (no scored papers for term)")
    print(f"\nDone → {docs_dir}")


if __name__ == "__main__":
    main()
