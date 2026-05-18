"""
Paper Scorer — Phase 6 of the EMERGING track.

Reads downloaded PDFs from data/papers/ and papers_meta.json, extracts
full text from each PDF, and scores each paper on four scientific quality
dimensions using an LLM (one call per dimension, 4 calls per paper total).

Only papers with download_status="downloaded" or "already_exists" are scored.
Papers without a PDF on disk are skipped.

This is Option B (simplified). See plan.md Step 6 for Option A — the full
longevus-style 5-agent-per-crew implementation with granular Pydantic models.

Scoring dimensions:
  HoE  Hierarchy of Evidence      0–30  (study type: meta-analysis > RCT > cohort …)
  CL   Conclusions & Limitations  0–20  (are conclusions supported and caveated?)
  SD   Study Design               0–30  (randomisation, blinding, sample size …)
  SS   Statistical Significance   0–20  (p-values, effect sizes, clinical relevance)
  ─────────────────────────────────────
  Final score                     0–100

Output: papers_meta.json updated per paper with:
  final_score, hoe_score, cl_score, sd_score, ss_score,
  score_rationale (dict keyed by dimension), scored_at

Idempotent: papers already marked score_status="scored" are skipped.
Use --rescore to force re-scoring of all papers.

Usage:
  python src/paper_pipeline/paper_scorer.py
  python src/paper_pipeline/paper_scorer.py --meta data/papers_meta.json
  python src/paper_pipeline/paper_scorer.py --rescore
"""

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional

import pdfplumber
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BASE_DIR   = Path(__file__).resolve().parent
META_PATH  = BASE_DIR / "data" / "papers_meta.json"
MODEL = "gpt-4.1"   # needs genuine reasoning over scientific methodology

client = OpenAI()


# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a scientific paper quality assessor. You will receive a paper's text \
and score it on a specific dimension of scientific quality. \
Respond ONLY with a valid JSON object matching the schema provided. \
Be strict: reserve high scores for genuinely rigorous work.\
"""

_PROMPTS = {
    "HoE": {
        "max": 30,
        "instruction": """\
Score this paper on HIERARCHY OF EVIDENCE (0–30).

Scoring guide:
  26–30  Systematic review or meta-analysis of RCTs, pre-registered, PRISMA-compliant
  20–25  RCT with proper randomisation, allocation concealment, and blinding
  14–19  Well-designed cohort or case-control study with a clear control group
   8–13  Cross-sectional survey, retrospective analysis, or small observational study
   2–7   Case report, expert opinion, mechanistic study, or animal/in-vitro only
   0–1   No empirical data; purely theoretical or narrative commentary

Return JSON:
{
  "score": <float 0–30>,
  "evidence_type": "<one of: meta-analysis, systematic-review, RCT, cohort, case-control, cross-sectional, case-report, mechanistic, expert-opinion>",
  "rationale": "<2–3 sentences explaining the score>"
}""",
    },

    "CL": {
        "max": 20,
        "instruction": """\
Score this paper on CONCLUSIONS & LIMITATIONS quality (0–20).

Scoring guide:
  17–20  Conclusions tightly match results; limitations section is explicit, specific,
         and includes threats to internal/external validity
  12–16  Conclusions generally supported; some limitations acknowledged but not fully discussed
   7–11  Minor overstating of conclusions OR limitations are vague/generic
   2–6   Conclusions go noticeably beyond the evidence; limitations largely absent
   0–1   Conclusions contradict the data or no limitations section at all

Return JSON:
{
  "score": <float 0–20>,
  "conclusions_supported": <true|false>,
  "limitations_explicit": <true|false>,
  "rationale": "<2–3 sentences>"
}""",
    },

    "SD": {
        "max": 30,
        "instruction": """\
Score this paper on STUDY DESIGN & METHODOLOGY (0–30).

Scoring guide:
  26–30  Rigorous design: adequate sample size (power-calculated), randomisation,
         blinding, validated outcome measures, pre-registration
  20–25  Good design with most of the above; minor gaps (e.g. no blinding in
         low-risk context, or small but justified sample)
  14–19  Adequate design but with notable weaknesses (unvalidated outcomes,
         convenience sample, no control group rationale)
   8–13  Significant methodological limitations that affect interpretation
   2–7   Poorly designed: no controls, very small n, major confounders unaddressed
   0–1   No discernible methodology; anecdotal or purely descriptive

Return JSON:
{
  "score": <float 0–30>,
  "sample_size": <integer or null if not reported>,
  "has_control_group": <true|false|null>,
  "randomised": <true|false|null>,
  "rationale": "<2–3 sentences>"
}""",
    },

    "SS": {
        "max": 20,
        "instruction": """\
Score this paper on STATISTICAL & CLINICAL SIGNIFICANCE (0–20).

Scoring guide:
  17–20  Clear statistical significance (p < 0.05) with effect sizes reported
         (Cohen's d, OR, HR, etc.) and clinical relevance discussed
  12–16  Statistical significance shown; effect size or clinical relevance partially addressed
   7–11  Results are significant but effect sizes absent, or clinical relevance unclear
   2–6   Marginal or inconsistent significance; no effect size; clinical relevance speculative
   0–1   No statistical analysis, or results are non-significant with no discussion of power

Return JSON:
{
  "score": <float 0–20>,
  "statistically_significant": <true|false|null>,
  "effect_size_reported": <true|false>,
  "rationale": "<2–3 sentences>"
}""",
    },
}


# ── Text extraction ───────────────────────────────────────────────────────────

def _extract_text(pdf_path: Path) -> str:
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return " ".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return ""


# ── LLM scoring ──────────────────────────────────────────────────────────────

def _score_dimension(paper_context: str, dim: str) -> dict:
    prompt = _PROMPTS[dim]
    user_msg = f"{prompt['instruction']}\n\n---\nPAPER TEXT:\n{paper_context}"
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            response_format={"type": "json_object"},
            temperature=0,
            seed=42,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
        )
        result = json.loads(resp.choices[0].message.content)
        score = float(result.get("score", 0))
        result["score"] = min(max(score, 0.0), float(prompt["max"]))
        return result
    except Exception as exc:
        return {"score": 0.0, "rationale": f"scoring error: {exc}"}


def _score_paper(paper: dict, papers_dir: Path) -> Optional[dict]:
    """Score one paper. Returns score dict or None if text extraction fails."""
    pdf_path = papers_dir / f"{paper['openalex_id']}.pdf"
    context  = _extract_text(pdf_path)

    if not context.strip():
        return None

    rationale = {}
    scores    = {}
    for dim in ("HoE", "CL", "SD", "SS"):
        result         = _score_dimension(context, dim)
        scores[dim]    = result.get("score", 0.0)
        rationale[dim] = result
        time.sleep(0.3)

    return {
        "final_score":     round(sum(scores.values()), 1),
        "hoe_score":       scores["HoE"],
        "cl_score":        scores["CL"],
        "sd_score":        scores["SD"],
        "ss_score":        scores["SS"],
        "score_rationale": rationale,
        "scored_at":       date.today().isoformat(),
        "score_status":    "scored",
    }


# ── Pipeline phase ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Paper Scorer — phase 6 of EMERGING track")
    parser.add_argument("--meta", default=str(META_PATH), metavar="PATH")
    parser.add_argument("--papers-dir", default=str(BASE_DIR / "data" / "papers"), metavar="PATH")
    parser.add_argument("--rescore", action="store_true",
                        help="Re-score papers already marked score_status=scored")
    args = parser.parse_args()

    meta_path  = Path(args.meta)
    papers_dir = Path(args.papers_dir)

    if not meta_path.exists():
        sys.exit(f"papers_meta.json not found: {meta_path}")

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    if args.rescore:
        for p in meta.values():
            p.pop("score_status", None)
        print("--rescore: cleared score_status for all papers")

    to_score = [
        p for p in meta.values()
        if p.get("score_status") != "scored"
        and p.get("download_status") in ("downloaded", "already_exists")
    ]

    print("Paper Scorer")
    print(f"  meta        {meta_path.name}  ({len(meta)} papers total)")
    print(f"  to score    {len(to_score)}  (model: {MODEL})")
    print(f"  papers dir  {papers_dir}")
    print()

    counts = {"scored": 0, "abstract_only": 0, "failed": 0}

    for paper in to_score:
        pid   = paper["openalex_id"]
        title = (paper.get("title") or "")[:60]

        result = _score_paper(paper, papers_dir)

        if result is None:
            meta[pid]["score_status"] = "failed"
            counts["failed"] += 1
            print(f"  ✗ [failed]  {title}")
        else:
            meta[pid].update(result)
            counts["scored"] += 1
            print(f"  ✓ [{result['final_score']:5.1f}]  {title}")

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print()
    print(f"  scored   {counts['scored']}")
    print(f"  failed   {counts['failed']}")
    print(f"\nDone → {meta_path}")


if __name__ == "__main__":
    main()
