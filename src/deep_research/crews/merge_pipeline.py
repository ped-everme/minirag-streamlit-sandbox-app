"""
CrewAI merge pipeline.

Consolidates the refinement-phase tables from the OpenAI and Perplexity
pipelines into a single deduplicated trend table using a CrewAI agent
backed by gpt-4o-mini.

The agent receives both tables as markdown, merges overlapping trends,
preserves all unique entries, and outputs a unified markdown table that
is then saved as both ``.md`` and ``.csv``.
"""

import logging
from pathlib import Path
from crewai import Agent, Task, Crew

from src.deep_research.utils import save_markdown, extract_tables_to_csv
from src.deep_research.crews.config_loader import load_yaml
import os
logger = logging.getLogger(__name__)
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))

AGENTS_CONFIG = load_yaml(Path(__file__).parent / "config" / "agents.yaml")
TASKS_CONFIG = load_yaml(Path(__file__).parent / "config" / "tasks.yaml")


def run_merge_pipeline(openai_report_p2: str, perplexity_report_p2: str) -> str:
    """
    Merge OpenAI and Perplexity refinement tables via a CrewAI agent.

    Creates a single-agent CrewAI crew that receives both refinement tables
    as markdown strings, deduplicates overlapping trends, and produces a
    consolidated markdown table. The merged output is saved to disk as both
    a markdown file and one or more extracted CSVs.

    Agent and task configurations are loaded from YAML files under
    ``crews/config/agents.yaml`` and ``crews/config/tasks.yaml``.

    Args:
        openai_report_p2 (str): The OpenAI phase 2 refinement table as a
            markdown-formatted string (from ``pd.DataFrame.to_markdown()``
            or raw CSV text).
        perplexity_report_p2 (str): The Perplexity phase 2 refinement table
            as a markdown-formatted string.

    Returns:
        str: The raw merged report produced by the CrewAI agent, containing
            a consolidated markdown table with deduplicated trends.

    Side Effects:
        - Saves ``outputs/merged_trends_<date>.md`` (full agent output).
        - Saves ``outputs/merged_trends_table_<n>_<date>.csv`` for each
          markdown table found in the agent output.
    """
    logger.info("Merge Pipeline starting.")

    merge_agent = Agent(
        **AGENTS_CONFIG["merge_agent"],
        llm="gpt-4o-mini",
    )

    merge_task = Task(
        description=TASKS_CONFIG["merge_task"]["description"].format(
            table_a=openai_report_p2,
            table_b=perplexity_report_p2,
        ),
        expected_output=TASKS_CONFIG["merge_task"]["expected_output"],
        agent=merge_agent,
    )

    crew = Crew(
        agents=[merge_agent],
        tasks=[merge_task],
        verbose=True,
    )

    result = crew.kickoff()
    merged_report = result.raw

    logger.info("Merge completed.")
    save_markdown(merged_report, OUTPUT_DIR, prefix="merged_trends")
    extract_tables_to_csv(merged_report, OUTPUT_DIR, prefix="merged_trends_table")

    return merged_report


if __name__ == "__main__":
    run_merge_pipeline("", "")