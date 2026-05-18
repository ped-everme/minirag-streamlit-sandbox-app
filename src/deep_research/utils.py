"""
Shared utilities for the Deep Research pipeline.

Provides common helpers used across OpenAI, Perplexity, and merge pipelines:
markdown persistence, markdown-table-to-CSV extraction, and CLI time window
parsing.
"""

import re
import csv
from pathlib import Path
import time
from datetime import datetime, timedelta
from datetime import date
import logging

logger = logging.getLogger(__name__)

TIME_WINDOW_OPTIONS = {
    "last_three_months": 90,
    "last_twelve_months": 365,
}


def save_markdown(report_md: str, output_dir: Path, prefix: str = "deep_research_health_longevity") -> Path:
    """
    Save a markdown string to disk and return the file path.

    Creates the output directory if it does not exist. The file is named
    using the given prefix and today's date in ISO format
    (e.g. ``deep_research_base_2026-05-05.md``).

    Args:
        report_md (str): The markdown content to persist. Must be non-empty.
        output_dir (Path): Directory where the file will be written.
        prefix (str, optional): Filename prefix before the date stamp.
            Defaults to ``"deep_research_health_longevity"``.

    Returns:
        Path: Absolute path to the saved ``.md`` file.

    Raises:
        ValueError: If ``report_md`` is empty or falsy.
    """
    if not report_md:
        raise ValueError("No output_text found.")

    output_dir.mkdir(exist_ok=True)
    md_path = output_dir / f"{prefix}_{date.today().isoformat()}.md"
    md_path.write_text(report_md, encoding="utf-8")
    logger.info(f"MD saved in: {md_path}")
    return md_path

def extract_tables_to_csv(report_md: str, output_dir: Path, prefix: str = "deep_research_table") -> list[Path]:
    """
    Extract markdown tables from a report string and save each as a CSV.

    Uses a regex pattern to detect standard markdown tables (header row,
    separator row, and one or more data rows). Each table found is parsed
    by splitting on ``|`` delimiters and written to a numbered CSV file.

    Args:
        report_md (str): The full markdown report that may contain one or
            more pipe-delimited tables.
        output_dir (Path): Directory where CSV files will be written.
        prefix (str, optional): Filename prefix for the generated CSVs.
            Each file is named ``<prefix>_<n>_<date>.csv`` where ``n`` is
            the 1-based table index. Defaults to ``"deep_research_table"``.

    Returns:
        list[Path]: List of paths to the generated CSV files, one per table
            found. Returns an empty list if no tables are detected.
    """
    table_pattern = re.compile(
        r'(\|.+\|)\n(\|[-:\s|]+\|)\n((?:\|.+\|\n?)+)',
        re.MULTILINE,
    )
    matches = table_pattern.findall(report_md)

    if not matches:
        logger.info("No tables found in markdown.")
        return []

    output_dir.mkdir(exist_ok=True)
    csv_paths = []
    for i, (header, _sep, body) in enumerate(matches, 1):
        rows = [header] + body.strip().split("\n")
        parsed = [
            [c.strip() for c in row.strip().strip("|").split("|")]
            for row in rows
        ]

        csv_path = output_dir / f"{prefix}_{i}_{date.today().isoformat()}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(parsed)

        logger.info(f"Table {i} → {csv_path}")
        csv_paths.append(csv_path)

    return csv_paths

def parse_time_window(choice: str) -> tuple[str, str]:
    """Returns (time_horizon_text, storage_label) from argparse choice."""
    days = TIME_WINDOW_OPTIONS[choice]
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    time_horizon = (
        f"From {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}, "
    )
    # e.g. "three_months" or "twelve_months"
    storage_label = choice.replace("last_", "")

    return time_horizon, storage_label