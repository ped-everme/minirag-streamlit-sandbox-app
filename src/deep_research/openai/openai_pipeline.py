"""
OpenAI Deep Research pipeline.

Executes a two-phase research pipeline using OpenAI's o4-mini-deep-research
model to discover and refine social-first health and longevity trends.

Phase 1 (base research):
    Sends a broad social-listening prompt via the Responses API in background
    mode with web search and code interpreter tools enabled. Produces a
    markdown report and extracts any trend tables found as CSV.

Phase 2 (refinement):
    Feeds the full phase 1 report back to the model, asking it to find
    alternative consumer-facing names, related search terms, hashtags,
    and source-backed citations for each trend. Produces a second markdown
    report and a refinement CSV with a standardised 7-column schema.
"""

import os
import sys
import logging
from pathlib import Path
from datetime import date

from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
from src.deep_research.openai.utils import (
    build_research_request,
    wait_for_response,
)

from src.deep_research.utils import save_markdown, extract_tables_to_csv

from src.deep_research.constants import (
    OPEN_AI_BASE_RESEARCH_AGENT,
    OPEN_AI_BASE_RESEARCH_PROMPT,
    OPEN_AI_REFINEMENT_AGENT,
    OPEN_AI_REFINEMENT_PROMPT,
)

OPEN_AI_BASE_RESEARCH_MODEL = os.getenv("OPEN_AI_BASE_RESEARCH_MODEL")
OPEN_AI_REFINEMENT_MODEL = os.getenv("OPEN_AI_REFINEMENT_MODEL")
logger = logging.getLogger(__name__)
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise EnvironmentError("OPENAI_API_KEY not found. Check your .env file.")


def run_openai_pipeline(time_window: str) -> tuple[list[Path], list[Path]]:
    """
    Run the two-phase OpenAI deep research pipeline.

    Phase 1 sends a social-listening prompt scoped to the given time window
    and extracts trend tables from the response. Phase 2 receives the full
    phase 1 report as context and queries for alternative names, hashtags,
    and source-backed related terms for each trend.

    Both phases run as background responses with web search and code
    interpreter tools. Each phase saves its raw markdown output and
    attempts to extract any markdown tables found into CSV files.

    Args:
        time_window (str): Human-readable date range string injected into
            both the base and refinement prompts
            (e.g. ``"From 2026-02-04 to 2026-05-05, prioritizing the
            most recent months when possible."``).

    Returns:
        tuple[list[Path], list[Path]]: A two-element tuple where:
            - ``[0]`` (list[Path]): CSV paths extracted from the phase 1
              response. Typically contains one file
              (``deep_research_base_table_1_<date>.csv``).
            - ``[1]`` (list[Path]): CSV paths extracted from the phase 2
              response. Typically contains one file
              (``deep_research_refinement_table_1_<date>.csv``).
              May be empty if the model did not return a markdown table.

    Raises:
        TimeoutError: If either phase does not complete within 30 minutes.
        EnvironmentError: If ``OPENAI_API_KEY`` is not set (raised at
            module import time).
    """
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    logger.info("OpenAI Client Configured Successfully.")

    # Phase 1
    user_query = OPEN_AI_BASE_RESEARCH_PROMPT.format(TIME_HORIZON=time_window)
    request_kwargs = build_research_request(
        model=OPEN_AI_BASE_RESEARCH_MODEL,
        agent_prompt=OPEN_AI_BASE_RESEARCH_AGENT,
        user_query=user_query,
    )
    response = openai_client.responses.create(**request_kwargs)
    result_p1 = wait_for_response(client=openai_client, response_id=response.id, poll_seconds=5, timeout_minutes=30)
    logger.info(f"Phase 1 status: {result_p1.status}")

    report_p1 = getattr(result_p1, "output_text", "") or ""
    save_markdown(report_p1, OUTPUT_DIR, prefix="deep_research_base")
    csv_paths_p1 = extract_tables_to_csv(report_p1, OUTPUT_DIR, prefix="deep_research_base_table")

    # Phase 2
    refinement_query = OPEN_AI_REFINEMENT_PROMPT.format(
        trends_block=report_p1,
        TIME_HORIZON=time_window,
    )
    request_kwargs_p2 = build_research_request(
        model=OPEN_AI_REFINEMENT_MODEL,
        agent_prompt=OPEN_AI_REFINEMENT_AGENT,
        user_query=refinement_query,
    )
    response_p2 = openai_client.responses.create(**request_kwargs_p2)
    result_p2 = wait_for_response(client=openai_client, response_id=response_p2.id, poll_seconds=5, timeout_minutes=30)
    logger.info(f"Phase 2 status: {result_p2.status}")

    report_p2 = getattr(result_p2, "output_text", "") or ""
    save_markdown(report_p2, OUTPUT_DIR, prefix="deep_research_refinement")
    csv_paths_p2 = extract_tables_to_csv(report_p2, OUTPUT_DIR, prefix="deep_research_refinement_table")

    return csv_paths_p1, csv_paths_p2


if __name__ == "__main__":
    run_openai_pipeline()