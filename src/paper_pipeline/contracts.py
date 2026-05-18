"""
Pipeline Contract Validator.

Validates the output structure of each phase so the next phase won't
break on malformed or missing data. Each check is a fast structural
assertion — it does not re-run any logic, just reads the output files.

Called by pipeline.py after each phase. Also runnable standalone:
  python src/paper_pipeline/contracts.py          # check all phases
  python src/paper_pipeline/contracts.py phase4   # check one phase
  python src/paper_pipeline/contracts.py --list   # show available checks

Exit 0 on success, 1 on first failure.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import faiss

BASE_DIR    = Path(__file__).resolve().parent
DATA_DIR    = BASE_DIR / "data"
EXPANSIONS  = DATA_DIR / "expansions"
CATALOG     = DATA_DIR / "catalog.json"
META        = DATA_DIR / "papers_meta.json"
PAPERS_DIR  = DATA_DIR / "papers"
INDEX_DIR   = DATA_DIR / "index"


class ContractError(Exception):
    pass


def _require(condition: bool, msg: str) -> None:
    if not condition:
        raise ContractError(msg)


# ── Phase 1 — term_expander output ────────────────────────────────────────────

def check_phase1() -> str:
    """expansion_DATE.json exists with at least one term and one query."""
    files = sorted(EXPANSIONS.glob("expansion_*.json"))
    _require(bool(files), f"No expansion_*.json found in {EXPANSIONS}")

    latest = files[-1]
    d = json.loads(latest.read_text())

    _require("expansions" in d, "expansion file missing 'expansions' key")
    _require(isinstance(d["expansions"], list), "'expansions' must be a list")
    _require(len(d["expansions"]) > 0, "'expansions' list is empty — no terms expanded")

    for i, term in enumerate(d["expansions"]):
        _require("term_id" in term,   f"expansions[{i}] missing 'term_id'")
        _require("queries" in term,   f"expansions[{i}] missing 'queries'")
        for j, q in enumerate(term["queries"]):
            _require("query" in q,      f"expansions[{i}].queries[{j}] missing 'query'")
            _require("type" in q,       f"expansions[{i}].queries[{j}] missing 'type'")
            _require("confidence" in q, f"expansions[{i}].queries[{j}] missing 'confidence'")

    return f"OK — {latest.name}  ({len(d['expansions'])} terms)"


# ── Phase 2 — catalog output ──────────────────────────────────────────────────

def check_phase2() -> str:
    """catalog.json exists with at least one entry with required fields."""
    _require(CATALOG.exists(), f"catalog.json not found: {CATALOG}")

    d = json.loads(CATALOG.read_text())
    _require("entries" in d, "catalog.json missing 'entries' key")
    _require(len(d["entries"]) > 0, "catalog.json has no entries")

    required = {"query", "query_normalized", "type", "parent_term_id", "status"}
    for i, e in enumerate(d["entries"]):
        missing = required - e.keys()
        _require(not missing, f"entries[{i}] missing fields: {missing}")

    return f"OK — catalog.json  ({len(d['entries'])} entries)"


# ── Phase 3 — longevus_checker output ─────────────────────────────────────────

def check_phase3() -> str:
    """All catalog entries have been longevus-checked (longevus_checked is set)."""
    _require(CATALOG.exists(), f"catalog.json not found: {CATALOG}")

    d    = json.loads(CATALOG.read_text())
    entries = d.get("entries", [])
    _require(len(entries) > 0, "catalog.json has no entries")

    unchecked = [e["query"] for e in entries if not e.get("longevus_checked")]
    _require(
        not unchecked,
        f"{len(unchecked)} entries not yet longevus-checked: {unchecked[:3]}…"
    )

    covered = sum(1 for e in entries if e.get("longevus_covered"))
    return f"OK — {len(entries)} entries checked  ({covered} longevus-covered)"


# ── Phase 4 — paper_search output ─────────────────────────────────────────────

def check_phase4() -> str:
    """papers_meta.json exists with at least one paper and required fields."""
    _require(META.exists(), f"papers_meta.json not found: {META}")

    meta = json.loads(META.read_text())
    _require(len(meta) > 0, "papers_meta.json is empty — no papers found")

    required = {"openalex_id", "title", "found_by_queries", "parent_term_ids"}
    for pid, paper in list(meta.items())[:5]:
        missing = required - paper.keys()
        _require(not missing, f"paper {pid} missing fields: {missing}")
        _require(paper["openalex_id"] == pid,
                 f"openalex_id mismatch: key={pid} field={paper['openalex_id']}")

    return f"OK — papers_meta.json  ({len(meta)} papers)"


# ── Phase 5 — paper_downloader output ────────────────────────────────────────

def check_phase5() -> str:
    """Every paper marked downloaded/already_exists has a PDF on disk."""
    _require(META.exists(), f"papers_meta.json not found: {META}")

    meta       = json.loads(META.read_text())
    downloaded = [p for p in meta.values()
                  if p.get("download_status") in ("downloaded", "already_exists")]

    missing_files = []
    for p in downloaded:
        pdf = PAPERS_DIR / f"{p['openalex_id']}.pdf"
        if not pdf.exists():
            missing_files.append(p["openalex_id"])

    _require(not missing_files,
             f"{len(missing_files)} papers marked downloaded but PDF missing: {missing_files[:3]}")

    total  = len(meta)
    n_dl   = len(downloaded)
    failed = sum(1 for p in meta.values() if p.get("download_status") == "failed")
    return f"OK — {n_dl}/{total} downloaded  ({failed} failed, not blocking)"


# ── Phase 6 — paper_scorer output ────────────────────────────────────────────

def check_phase6() -> str:
    """Every scored paper has all required score fields with valid ranges."""
    _require(META.exists(), f"papers_meta.json not found: {META}")

    meta   = json.loads(META.read_text())
    scored = [p for p in meta.values() if p.get("score_status") == "scored"]

    if not scored:
        # No scored papers is a warning, not a hard failure — scorer may have
        # been skipped because no PDFs were downloaded yet.
        return "WARN — no scored papers yet (scorer may not have run)"

    score_fields = {"final_score", "hoe_score", "cl_score", "sd_score", "ss_score"}
    for p in scored:
        missing = score_fields - p.keys()
        _require(not missing, f"scored paper {p['openalex_id']} missing: {missing}")
        _require(0 <= p["final_score"] <= 100,
                 f"final_score out of range: {p['final_score']} for {p['openalex_id']}")

    return f"OK — {len(scored)} papers scored"


# ── Phase 7 — indexer output ──────────────────────────────────────────────────

def check_phase7() -> str:
    """FAISS index and SQLite FTS5 exist and contain data."""
    faiss_path = INDEX_DIR / "faiss.index"
    ids_path   = INDEX_DIR / "faiss_ids.json"
    db_path    = INDEX_DIR / "chunks.db"

    _require(faiss_path.exists(), f"faiss.index not found: {faiss_path}")
    _require(ids_path.exists(),   f"faiss_ids.json not found: {ids_path}")
    _require(db_path.exists(),    f"chunks.db not found: {db_path}")

    index    = faiss.read_index(str(faiss_path))
    faiss_ids = json.loads(ids_path.read_text())
    _require(index.ntotal > 0,       "FAISS index is empty")
    _require(len(faiss_ids) > 0,     "faiss_ids.json is empty")
    _require(index.ntotal == len(faiss_ids),
             f"FAISS vector count ({index.ntotal}) ≠ faiss_ids length ({len(faiss_ids)})")

    conn  = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()
    _require(count > 0, "chunks.db has no rows")

    return f"OK — {index.ntotal} vectors in FAISS  {count} rows in SQLite"


# ── Registry ──────────────────────────────────────────────────────────────────

CHECKS = {
    "phase1": ("Term Expander output",    check_phase1),
    "phase2": ("Catalog output",          check_phase2),
    "phase3": ("Longevus Checker output", check_phase3),
    "phase4": ("Paper Search output",     check_phase4),
    "phase5": ("Paper Downloader output", check_phase5),
    "phase6": ("Paper Scorer output",     check_phase6),
    "phase7": ("Indexer output",          check_phase7),
}


def run_check(phase: str) -> bool:
    """Run one check. Prints result. Returns True if passed."""
    label, fn = CHECKS[phase]
    try:
        msg = fn()
        status = "WARN" if msg.startswith("WARN") else "PASS"
        print(f"  [{status}] {label}: {msg}")
        return True
    except ContractError as exc:
        print(f"  [FAIL] {label}: {exc}")
        return False


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline contract validator")
    parser.add_argument("phase", nargs="?", choices=list(CHECKS) + ["all"],
                        default="all", help="Which phase to validate (default: all)")
    parser.add_argument("--list", action="store_true", help="List available checks and exit")
    args = parser.parse_args()

    if args.list:
        for k, (label, _) in CHECKS.items():
            print(f"  {k}  {label}")
        return

    phases = list(CHECKS) if args.phase == "all" else [args.phase]

    print("Contract Validator")
    print()

    failed = []
    for phase in phases:
        ok = run_check(phase)
        if not ok:
            failed.append(phase)

    print()
    if failed:
        print(f"  {len(failed)} check(s) failed: {', '.join(failed)}")
        sys.exit(1)
    else:
        print(f"  All {len(phases)} check(s) passed.")


if __name__ == "__main__":
    main()
