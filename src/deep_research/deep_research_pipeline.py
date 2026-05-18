"""
Deep Research Pipeline — Health & Longevity Trends

Orchestrates a three-stage pipeline that discovers, refines, and merges
social-first consumer health trends using OpenAI Deep Research,
Perplexity Sonar Deep Research, and a CrewAI merge agent.

Stages:
    1. OpenAI pipeline   — two-phase deep research (base → refinement table)
    2. Perplexity pipeline — two-phase deep research (base → refinement table)
    3. CrewAI merge       — deduplicates and consolidates both refinement tables
    4. GCS export         — uploads individual and merged CSVs to Google Cloud Storage

Usage:
    python -m src.deep_research.deep_research_pipeline --time_window last_three_months
    python -m src.deep_research.deep_research_pipeline --time_window last_twelve_months
"""

import os 
import json 
import time 
import pandas as pd 
from datetime import datetime, date
from pathlib import Path

from db.connection.gcloud import GcloudConnection
from src.deep_research.openai.openai_pipeline import run_openai_pipeline
from src.deep_research.perplexity.perplexity_pipeline import run_perplexity_pipeline
from src.deep_research.crews.merge_pipeline import run_merge_pipeline
from src.deep_research.utils import parse_time_window
MINIRAG_BUCKET = os.getenv("MINIRAG_BUCKET")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))

import logging, sys
import argparse
import shutil

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

def main():
    """
    Run the full deep research pipeline end-to-end.

    This function parses CLI arguments, connects to Google Cloud Storage,
    executes the OpenAI and Perplexity research pipelines sequentially,
    merges their refinement-phase CSVs via a CrewAI agent, and uploads
    all resulting artefacts to GCS.


    CLI Args:
        --time_window (str): Research time window. Accepted values are
            ``last_three_months`` or ``last_twelve_months``. Converted
            internally to a human-readable date range string
            (e.g. "From 2026-02-04 to 2026-05-05") via
            :func:`parse_time_window`.


    GCS Destination:
        ``gs://<MINIRAG_BUCKET>/deep_research_reports/<storage_label>/<filename>``

    Raises:
        No exceptions are raised to the caller; all errors are caught,
        logged, and the pipeline continues with available data.
    """

    parser = argparse.ArgumentParser(description="Deep Research Pipeline for Health and Longevity Trends")
    parser.add_argument(
        "--time_window",
        required=True,
        choices=["last_three_months", "last_twelve_months"],
        help="last_three_months or last_twelve_months",
    )
    args = parser.parse_args()

    time_horizon, storage_label = parse_time_window(args.time_window)
    logger.info(f"Time horizon: {time_horizon}")

    ##### GCLOUD CONNECTION BLOCK #######
    try: 
        gcloud_connection = GcloudConnection(bucket_name = MINIRAG_BUCKET)
        gcloud_connection.connect()
        logger.info("Connected to GCloud successfully.")
    except Exception as e: 
        logger.error(f"Error connecting to GCloud: {e}")


    ##### OPENAI PIPELINE BLOCK #####
    try:
        logger.info("OpenAI Pipeline Starting")
        oai_csv_p1, oai_csv_p2 = run_openai_pipeline(time_window=time_horizon)
        logger.info("OpenAI Pipeline Completed Successfully")
    except Exception as e:
        logger.error(f"Error during OpenAI Pipeline execution: {e}")
        oai_csv_p2 = []

    

    ##### PERPLEXITY PIPELINE BLOCK #####
    try:
        logger.info("Perplexity Pipeline Starting")
        ppl_csv = run_perplexity_pipeline(time_window=time_horizon)
        logger.info("Perplexity Pipeline Completed Successfully")
    except Exception as e:
        logger.error(f"Error during Perplexity Pipeline execution: {e}")
        ppl_csv = []

    ##### MERGE UNIQUE BLOCK #######
    if oai_csv_p2 and ppl_csv:
        try:
            logger.info("Merge Pipeline Starting")
            oai_table = pd.read_csv(oai_csv_p2[0]).to_markdown(index=False)
            ppl_table = pd.read_csv(ppl_csv[0]).to_markdown(index=False)
            merged_report = run_merge_pipeline(oai_table, ppl_table)
            logger.info("Merge Pipeline Completed Successfully")
        except Exception as e:
            logger.error(f"Error during Merge Pipeline execution: {e}")
    else:
        logger.warning("Skipping merge — missing CSV from one or both pipelines.")

    ##### EXPORT PHASE GCLOUD #####
    storage_path = f"deep_research_reports/{storage_label}"

    csvs_to_upload = []
    if oai_csv_p2:
        csvs_to_upload.append(oai_csv_p2[0])
    if ppl_csv:
        csvs_to_upload.append(ppl_csv[0])

    merged_csv = list(OUTPUT_DIR.glob(f"merged_trends_table_*_{date.today().isoformat()}.csv"))
    if merged_csv:
        csvs_to_upload.append(merged_csv[0])

    for csv_path in csvs_to_upload:
        gcloud_connection.upload_json(
            local_file_path=str(csv_path),
            storage_path=f"{storage_path}/{csv_path.name}",
        )
        logger.info(f"Uploaded {csv_path.name} to GCS.")
    
    #### DELETE OUTPUT FILES ######
    logger.info(f"Deleting output directory: {OUTPUT_DIR}")
    try:
        shutil.rmtree(OUTPUT_DIR)
        logger.info(f"Deleted output directory: {OUTPUT_DIR}")
    except Exception as e:
        logger.error(f"Error deleting output directory: {e}")

if __name__ == "__main__":
    main()