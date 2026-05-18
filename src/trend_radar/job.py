"""
Trend Radar — full pipeline orchestrator.

Runs every step in order:
  1. Ingest deep-research CSVs → terms.json          (needs OPENAI_API_KEY)
  2. Collectors: YouTube, Google Trends, Twitter, TikTok  (needs platform keys)
  3. Aggregator  → data/output/signal_DATE.json
  4. ETL         → data/processed/*.csv

Flags for partial runs:
  --skip-ingest       re-use existing data/terms.json (skip step 1)
  --skip-collectors   re-use whatever is already in data/raw/ (skip step 2)
  --mock              steps 1+2 skipped; aggregator reads from data/mock/
  --date YYYY-MM-DD   fix the run date passed to the aggregator

Usage:
  python src/trend_radar/job.py
  python src/trend_radar/job.py --skip-ingest --skip-collectors
  python src/trend_radar/job.py --mock
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


def _run(step: str, cmd: list[str]) -> None:
    _header(step)
    t0 = time.monotonic()
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"\n✗  {step} failed (exit {exc.returncode})", file=sys.stderr)
        sys.exit(exc.returncode)
    print(f"\n  [{step}] done in {time.monotonic() - t0:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trend Radar — end-to-end pipeline job",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Skip ingest step; use existing data/terms.json",
    )
    parser.add_argument(
        "--skip-collectors",
        action="store_true",
        help="Skip all collectors; aggregate from existing data/raw/ files",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Skip ingest + collectors; run aggregator over data/mock/ only",
    )
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Override run date for the aggregator (default: today)",
    )
    args = parser.parse_args()

    py = sys.executable
    terms_path = BASE_DIR / "data" / "terms.json"

    print("\n╔══════════════════════════════════════════════╗")
    print("║         Trend Radar — pipeline job           ║")
    print("╚══════════════════════════════════════════════╝")

    # ── Step 1: Ingest ────────────────────────────────────────────────────────
    if args.mock or args.skip_ingest:
        reason = "mock mode" if args.mock else "--skip-ingest"
        _header(f"Step 1 — Ingest  [SKIPPED — {reason}]")
        if not args.mock and not terms_path.exists():
            print(
                f"\n✗  --skip-ingest set but {terms_path} does not exist.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        _run(
            "Step 1 — Ingest deep-research CSVs → terms.json",
            [
                py,
                str(BASE_DIR / "scripts" / "ingest_deep_research.py"),
                "--output",
                str(terms_path),
            ],
        )

    # ── Step 2: Collectors ────────────────────────────────────────────────────
    if args.mock or args.skip_collectors:
        reason = "mock mode" if args.mock else "--skip-collectors"
        _header(f"Step 2 — Collectors  [SKIPPED — {reason}]")
    else:
        collectors = ["youtube", "google_trends", "twitter", "tiktok"]
        for name in collectors:
            _run(
                f"Step 2 — Collector: {name}",
                [
                    py,
                    str(BASE_DIR / "collectors" / f"{name}.py"),
                    "--terms",
                    str(terms_path),
                ],
            )

    # ── Step 3: Aggregate ─────────────────────────────────────────────────────
    mock_dir = BASE_DIR / "data" / "mock"
    agg_terms = str(mock_dir / "terms.json") if args.mock else str(terms_path)

    agg_cmd = [py, str(BASE_DIR / "pipeline" / "aggregate.py"), "--terms", agg_terms]

    if args.mock:
        # Pass the latest file per collector from the mock directory
        for flag, pattern in [
            ("--youtube",       "youtube_*.json"),
            ("--google-trends", "google_trends_*.json"),
            ("--twitter",       "twitter_*.json"),
            ("--tiktok",        "tiktok_*.json"),
        ]:
            candidates = sorted(mock_dir.glob(pattern))
            if candidates:
                agg_cmd += [flag, str(candidates[-1])]

    if args.date:
        agg_cmd += ["--date", args.date]

    _run("Step 3 — Aggregator → signal_DATE.json + audit_DATE.json", agg_cmd)

    # ── Step 4: ETL ───────────────────────────────────────────────────────────
    _run(
        "Step 4 — ETL → processed/*.csv",
        [py, str(BASE_DIR / "pipeline" / "build_dataset.py")],
    )

    print("\n╔══════════════════════════════════════════════╗")
    print("║   Pipeline complete                          ║")
    print("╚══════════════════════════════════════════════╝\n")


if __name__ == "__main__":
    main()
