"""
Paper Pipeline — end-to-end orchestrator.

Runs the EMERGING and HYPED tracks in sequence, phase by phase.
Each phase is an autonomous script; this file calls them via subprocess
and validates I/O contracts between phases using contracts.py.

EMERGING track phases:
  1  term_expander.py    signal → expansion_DATE.json
  2  catalog.py          expansion → catalog.json (updated)
  3  longevus_checker.py catalog → catalog.json (longevus_covered flagged)
  4  paper_search.py     catalog → papers_meta.json + catalog.json (paper_count)
  5  paper_downloader.py papers_meta → data/papers/*.pdf
  6  paper_scorer.py     PDFs + papers_meta → papers_meta.json (scores added)
  7  doc_generator.py    papers_meta + signal → data/documents/*.md

HYPED track phases:
  H  hyped_summarizer.py signal → data/documents/*.md

Final phase (both tracks):
  8  indexer.py          PDFs + documents → data/index/ (FAISS + SQLite FTS5)

Skip flags allow partial runs (useful when resuming after a failure or when
running only specific phases during development).

Usage:
  python src/paper_pipeline/pipeline.py --signal src/trend_radar/data/output/signal_2026-05-13.json
  python src/paper_pipeline/pipeline.py --signal signal.json --skip-longevus --skip-scorer
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _header(title: str) -> None:
    bar = "─" * 60
    print(f"\n{bar}\n  {title}\n{bar}")


def _run(step: str, cmd: list[str], skip: bool = False,
         contract: str | None = None) -> None:
    script = Path(cmd[1]) if len(cmd) > 1 else None

    if skip:
        _header(f"{step}  [SKIPPED]")
        return

    if script and not script.exists():
        _header(f"{step}  [NOT YET IMPLEMENTED — skipped]")
        return

    _header(step)
    t0 = time.monotonic()
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n✗  {step} failed (exit {exc.returncode})", file=sys.stderr)
        sys.exit(exc.returncode)
    print(f"\n  [{step}] done in {time.monotonic() - t0:.1f}s")

    if contract:
        _validate(contract)


def _validate(phase: str) -> None:
    print(f"\n  validating contract {phase} …")
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "contracts.py"), phase],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if line.strip():
                print(f"  {line.strip()}")
        if result.returncode != 0:
            print(f"\n✗  Contract {phase} failed — pipeline stopped.", file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        print(f"\n✗  Could not run contracts.py: {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Paper Pipeline — end-to-end orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--signal", required=True, metavar="PATH",
                        help="Path to signal_DATE.json (output of trend_radar)")

    parser.add_argument("--skip-expander",   action="store_true")
    parser.add_argument("--skip-catalog",    action="store_true")
    parser.add_argument("--skip-longevus",   action="store_true")
    parser.add_argument("--skip-search",     action="store_true")
    parser.add_argument("--skip-download",   action="store_true")
    parser.add_argument("--skip-scorer",     action="store_true")
    parser.add_argument("--skip-indexer",    action="store_true")
    parser.add_argument("--skip-docgen",     action="store_true")
    parser.add_argument("--skip-hyped",      action="store_true")
    args = parser.parse_args()

    signal_path = Path(args.signal)
    if not signal_path.exists():
        sys.exit(f"Signal file not found: {signal_path}")

    py = sys.executable
    ph = lambda name: str(BASE_DIR / name)

    print("\n╔══════════════════════════════════════════════╗")
    print("║       Paper Pipeline — EMERGING + HYPED      ║")
    print(f"║  signal: {signal_path.name:<37}║")
    print("╚══════════════════════════════════════════════╝")

    # ── EMERGING track ────────────────────────────────────────────────────────

    _run("Phase 1 — Term Expander",
         [py, ph("term_expander.py"), "--signal", str(signal_path)],
         skip=args.skip_expander, contract="phase1")

    _run("Phase 2 — Catalog",
         [py, ph("catalog.py")],
         skip=args.skip_catalog, contract="phase2")

    _run("Phase 3 — Longevus Base Check",
         [py, ph("longevus_checker.py"), "--signal", str(signal_path)],
         skip=args.skip_longevus, contract="phase3")

    _run("Phase 4 — Paper Search",
         [py, ph("paper_search.py")],
         skip=args.skip_search, contract="phase4")

    _run("Phase 5 — Paper Download",
         [py, ph("paper_downloader.py")],
         skip=args.skip_download, contract="phase5")

    _run("Phase 6 — Paper Scorer",
         [py, ph("paper_scorer.py")],
         skip=args.skip_scorer, contract="phase6")

    _run("Phase 7 — Document Generator",
         [py, ph("doc_generator.py"), "--signal", str(signal_path)],
         skip=args.skip_docgen)

    # ── HYPED track ───────────────────────────────────────────────────────────

    _run("Phase H — Hyped Summarizer",
         [py, ph("hyped_summarizer.py"), "--signal", str(signal_path)],
         skip=args.skip_hyped)

    # ── Final indexing (both tracks) ──────────────────────────────────────────

    _run("Phase 8 — Indexer",
         [py, ph("indexer.py"), "--signal", str(signal_path)],
         skip=args.skip_indexer, contract="phase7")

    print("\n╔══════════════════════════════════════════════╝")
    print("║   Paper Pipeline complete                    ║")
    print("╚══════════════════════════════════════════════╝\n")


if __name__ == "__main__":
    main()
