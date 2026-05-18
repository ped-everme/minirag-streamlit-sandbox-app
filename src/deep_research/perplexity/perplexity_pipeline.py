"""
Perplexity Deep Research pipeline.

Executes a single-phase research pipeline using Perplexity's Sonar Deep Research
model to discover social-first health and longevity trends.

Phase 1 (base research):
    Queries Sonar Deep Research with a broad social-listening prompt.
    Produces a markdown report and extracts a ranked trend table as CSV.
"""

import os
import logging
from pathlib import Path
from datetime import datetime
import pandas as pd

from dotenv import load_dotenv
import sys
from src.deep_research.perplexity.utils import call_perplexity_deep_research
from src.deep_research.utils import save_markdown, extract_tables_to_csv
from src.deep_research.constants import (
    PERPLEXITY_BASE_RESEARCH_AGENT,
    PERPLEXITY_BASE_RESEARCH_PROMPT,
)

logger = logging.getLogger(__name__)

if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(handler)

logger.setLevel(logging.INFO)
logger.propagate = False

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))

load_dotenv()
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
PERPLEXITY_BASE_RESEARCH_MODEL = os.getenv("PERPLEXITY_BASE_RESEARCH_MODEL")


def run_perplexity_pipeline(time_window: str) -> list[Path]:
    """
    Run the Perplexity deep research pipeline (single phase).

    Sends a social-listening prompt scoped to the given time window
    and extracts a trend table from the response. The web search date
    filters are derived dynamically from the time_window string.

    Args:
        time_window (str): Human-readable date range string injected into the
            research prompt (e.g. ``"From 2026-02-04 to 2026-05-05,
            prioritizing the most recent months when possible."``).

    Returns:
        list[Path]: CSV paths extracted from the response. Typically contains
            one file (``perplexity_base_table_1_<date>.csv``).

    Raises:
        RuntimeError: If the Perplexity API returns an HTTP error after
            all retry attempts are exhausted.
        EnvironmentError: If ``PERPLEXITY_API_KEY`` is not set (raised
            at module import time).

    """
    if not PERPLEXITY_API_KEY:
        raise EnvironmentError("PERPLEXITY_API_KEY not found. Check your .env file.")
    logger.info("Perplexity Client Configured Successfully.")

    # Extract dates from time_window "From YYYY-MM-DD to YYYY-MM-DD, ..."
    dates = time_window.split("From ")[1].split(",")[0]
    start_str, end_str = dates.split(" to ")
    search_after = datetime.strptime(start_str, "%Y-%m-%d").strftime("%m/%d/%Y")
    search_before = datetime.strptime(end_str, "%Y-%m-%d").strftime("%m/%d/%Y")

    user_query = PERPLEXITY_BASE_RESEARCH_PROMPT.format(TIME_HORIZON=time_window)
    result = call_perplexity_deep_research(
        api_key=PERPLEXITY_API_KEY,
        model=PERPLEXITY_BASE_RESEARCH_MODEL,
        system_prompt=PERPLEXITY_BASE_RESEARCH_AGENT,
        user_prompt=user_query,
        search_after=search_after,
        search_before=search_before,
    )
    report = result["content"]
    logger.info("Perplexity Phase 1 completed.")

    save_markdown(report, OUTPUT_DIR, prefix="perplexity_base")
    csv_paths = extract_tables_to_csv(report, OUTPUT_DIR, prefix="perplexity_base_table")

    return csv_paths


if __name__ == "__main__":
    run_perplexity_pipeline()