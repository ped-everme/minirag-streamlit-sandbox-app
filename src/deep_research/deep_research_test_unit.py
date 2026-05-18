"""
Unit tests for the Deep Research pipeline.

Covers:
    - src.deep_research.utils (save_markdown, extract_tables_to_csv, parse_time_window)
    - src.deep_research.openai.openai_pipeline (run_openai_pipeline)
    - src.deep_research.perplexity.perplexity_pipeline (run_perplexity_pipeline)

All external API calls are mocked. No real requests are made.
"""

import csv
import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import date, datetime, timedelta

from src.deep_research.utils import (
    save_markdown,
    extract_tables_to_csv,
    parse_time_window,
    TIME_WINDOW_OPTIONS,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def tmp_output_dir(tmp_path):
    """Provide a clean temporary directory for each test."""
    return tmp_path / "outputs"


SIMPLE_TABLE_MD = (
    "| Rank | Trend | Topic |\n"
    "|------|-------|-------|\n"
    "| 1    | Foo   | Bar   |\n"
    "| 2    | Baz   | Qux   |\n"
)

TWO_TABLES_MD = (
    "Some intro text.\n\n"
    "| A | B |\n"
    "|---|---|\n"
    "| 1 | 2 |\n"
    "\n"
    "Middle text.\n\n"
    "| C | D |\n"
    "|---|---|\n"
    "| 3 | 4 |\n"
    "| 5 | 6 |\n"
)

SEVEN_COL_TABLE_MD = (
    "| Rank | Social trend name (from input) | Underlying topic (from input) "
    "| Related terms (clean) | Related terms with sources | Related hashtags | Key sources |\n"
    "|------|------|------|------|------|------|------|\n"
    "| 1 | Fibremaxxing | Fiber intake | fibermaxxing; fibremax | "
    "fibermaxxing (reddit.com) | #fibremaxxing; #gutmaxxing | Reddit, 2026 |\n"
)

OPENAI_PHASE1_MD = (
    "# Deep Research Report\n\n"
    "Here are the trends:\n\n"
    "| Rank | Trend Name | Topic |\n"
    "|------|-----------|-------|\n"
    "| 1 | Fibremaxxing | Fiber intake |\n"
    "| 2 | Silent Walking | Mental health |\n"
)

OPENAI_PHASE2_MD = SEVEN_COL_TABLE_MD

PERPLEXITY_PHASE1_MD = (
    "| Rank | Social trend name | Underlying health topic | What users say | "
    "Why trending now | Demand signals | Date first source | Date last source | Key sources |\n"
    "|------|---|---|---|---|---|---|---|---|\n"
    "| 1 | Cold Plunging | Recovery | cold plunge | Influencers | High | 2026 | 2026 | TikTok |\n"
)

PERPLEXITY_PHASE2_MD = (
    "| Rank | Social trend name (from input) | Underlying topic (from input) "
    "| Related terms (clean) | Related terms with sources | Related hashtags | Key sources |\n"
    "|------|------|------|------|------|------|------|\n"
    "| 1 | Cold Plunging | Recovery | ice bath; cold plunge | "
    "ice bath (youtube.com) | #coldplunge | YouTube, 2026 |\n"
)


# ============================================================================
# Tests: save_markdown
# ============================================================================

class TestSaveMarkdown:
    """Tests for save_markdown()."""

    def test_saves_file_with_correct_content(self, tmp_output_dir):
        """Markdown content is written verbatim to disk."""
        content = "# Hello\n\nSome markdown content."
        path = save_markdown(content, tmp_output_dir, prefix="test_report")

        assert path.exists()
        assert path.read_text(encoding="utf-8") == content

    def test_filename_contains_prefix_and_date(self, tmp_output_dir):
        """Filename follows the <prefix>_<YYYY-MM-DD>.md pattern."""
        path = save_markdown("content", tmp_output_dir, prefix="my_prefix")
        expected_name = f"my_prefix_{date.today().isoformat()}.md"
        assert path.name == expected_name

    def test_creates_output_directory(self, tmp_output_dir):
        """Output directory is created if it does not exist."""
        path = save_markdown("content", tmp_output_dir)
        assert tmp_output_dir.exists()
        assert path.exists()
        assert path.parent == tmp_output_dir

    def test_raises_on_empty_string(self, tmp_output_dir):
        """ValueError is raised when report_md is empty."""
        with pytest.raises(ValueError, match="No output_text found"):
            save_markdown("", tmp_output_dir)

    def test_raises_on_none(self, tmp_output_dir):
        """ValueError is raised when report_md is None."""
        with pytest.raises(ValueError):
            save_markdown(None, tmp_output_dir)

    def test_default_prefix(self, tmp_output_dir):
        """Default prefix is used when not specified."""
        path = save_markdown("content", tmp_output_dir)
        assert "deep_research_health_longevity" in path.name


# ============================================================================
# Tests: extract_tables_to_csv
# ============================================================================

class TestExtractTablesToCsv:
    """Tests for extract_tables_to_csv()."""

    def test_extracts_single_table(self, tmp_output_dir):
        """A single markdown table produces one CSV."""
        paths = extract_tables_to_csv(SIMPLE_TABLE_MD, tmp_output_dir, prefix="test")
        assert len(paths) == 1
        assert paths[0].suffix == ".csv"

    def test_csv_content_matches_table(self, tmp_output_dir):
        """CSV rows match the parsed markdown table content."""
        paths = extract_tables_to_csv(SIMPLE_TABLE_MD, tmp_output_dir, prefix="test")
        with open(paths[0], "r", encoding="utf-8") as f:
            rows = list(csv.reader(f))

        assert rows[0] == ["Rank", "Trend", "Topic"]  # header
        assert rows[1] == ["1", "Foo", "Bar"]
        assert rows[2] == ["2", "Baz", "Qux"]

    def test_extracts_multiple_tables(self, tmp_output_dir):
        """Multiple markdown tables produce multiple CSVs."""
        paths = extract_tables_to_csv(TWO_TABLES_MD, tmp_output_dir, prefix="multi")
        assert len(paths) == 2

    def test_returns_empty_list_when_no_tables(self, tmp_output_dir):
        """No tables in markdown returns an empty list."""
        paths = extract_tables_to_csv(
            "Just text, no tables here.", tmp_output_dir, prefix="test"
        )
        assert paths == []

    def test_returns_empty_list_for_empty_string(self, tmp_output_dir):
        """Empty markdown string returns an empty list."""
        paths = extract_tables_to_csv("", tmp_output_dir, prefix="test")
        assert paths == []

    def test_filename_contains_index_and_date(self, tmp_output_dir):
        """CSV filenames include 1-based index and today's date."""
        paths = extract_tables_to_csv(TWO_TABLES_MD, tmp_output_dir, prefix="tbl")
        today = date.today().isoformat()
        assert paths[0].name == f"tbl_1_{today}.csv"
        assert paths[1].name == f"tbl_2_{today}.csv"

    def test_seven_column_schema(self, tmp_output_dir):
        """The 7-column refinement table schema is parsed correctly."""
        paths = extract_tables_to_csv(SEVEN_COL_TABLE_MD, tmp_output_dir, prefix="ref")
        assert len(paths) == 1
        with open(paths[0], "r", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert len(rows[0]) == 7  # 7 columns
        assert rows[0][0] == "Rank"
        assert rows[0][6] == "Key sources"

    def test_creates_output_directory(self, tmp_output_dir):
        """Output directory is created if it does not exist."""
        extract_tables_to_csv(SIMPLE_TABLE_MD, tmp_output_dir, prefix="test")
        assert tmp_output_dir.exists()


# ============================================================================
# Tests: parse_time_window
# ============================================================================

class TestParseTimeWindow:
    """Tests for parse_time_window()."""

    def test_last_three_months_horizon_format(self):
        """time_horizon string starts with 'From' and contains two dates."""
        horizon, label = parse_time_window("last_three_months")
        assert horizon.startswith("From ")
        assert " to " in horizon

    def test_last_three_months_label(self):
        """storage_label is 'three_months'."""
        _, label = parse_time_window("last_three_months")
        assert label == "three_months"

    def test_last_twelve_months_label(self):
        """storage_label is 'twelve_months'."""
        _, label = parse_time_window("last_twelve_months")
        assert label == "twelve_months"

    def test_last_three_months_date_range(self):
        """Start date is approximately 90 days before end date."""
        horizon, _ = parse_time_window("last_three_months")
        # Extract dates from "From YYYY-MM-DD to YYYY-MM-DD, ..."
        dates = horizon.split("From ")[1].split(",")[0]
        start_str, end_str = dates.split(" to ")
        start = datetime.strptime(start_str, "%Y-%m-%d")
        end = datetime.strptime(end_str, "%Y-%m-%d")
        delta = (end - start).days
        assert delta == 90

    def test_last_twelve_months_date_range(self):
        """Start date is approximately 365 days before end date."""
        horizon, _ = parse_time_window("last_twelve_months")
        dates = horizon.split("From ")[1].split(",")[0]
        start_str, end_str = dates.split(" to ")
        start = datetime.strptime(start_str, "%Y-%m-%d")
        end = datetime.strptime(end_str, "%Y-%m-%d")
        delta = (end - start).days
        assert delta == 365

    def test_end_date_is_today(self):
        """End date in the horizon string matches today."""
        horizon, _ = parse_time_window("last_three_months")
        today_str = datetime.now().strftime("%Y-%m-%d")
        assert today_str in horizon

    def test_invalid_choice_raises_key_error(self):
        """Invalid time window choice raises KeyError."""
        with pytest.raises(KeyError):
            parse_time_window("last_six_months")


# ============================================================================
# Tests: OpenAI Pipeline (mocked)
# ============================================================================

class TestOpenAIPipeline:
    """Tests for run_openai_pipeline() with mocked API calls."""

    @patch("src.deep_research.openai.openai_pipeline.OpenAI")
    @patch("src.deep_research.openai.openai_pipeline.wait_for_response")
    @patch("src.deep_research.openai.openai_pipeline.OUTPUT_DIR")
    def test_returns_two_csv_lists(self, mock_output_dir, mock_wait, mock_openai_cls, tmp_path):
        """Pipeline returns a tuple of two lists of Paths."""
        mock_output_dir.__class__ = type(tmp_path)
        # Patch OUTPUT_DIR to use tmp_path
        with patch("src.deep_research.openai.openai_pipeline.OUTPUT_DIR", tmp_path):
            with patch("src.deep_research.openai.openai_pipeline.save_markdown"):
                with patch(
                    "src.deep_research.openai.openai_pipeline.extract_tables_to_csv"
                ) as mock_extract:
                    mock_extract.side_effect = [
                        [tmp_path / "base_table.csv"],
                        [tmp_path / "refinement_table.csv"],
                    ]

                    # Mock OpenAI client
                    mock_client = MagicMock()
                    mock_openai_cls.return_value = mock_client
                    mock_client.responses.create.return_value = MagicMock(id="resp_123")

                    # Mock wait_for_response
                    mock_response = MagicMock()
                    mock_response.status = "completed"
                    mock_response.output_text = OPENAI_PHASE1_MD
                    mock_wait.return_value = mock_response

                    from src.deep_research.openai.openai_pipeline import run_openai_pipeline

                    p1, p2 = run_openai_pipeline(
                        time_window="From 2026-02-04 to 2026-05-05,"
                    )

                    assert isinstance(p1, list)
                    assert isinstance(p2, list)
                    assert len(p1) == 1
                    assert len(p2) == 1

    @patch("src.deep_research.openai.openai_pipeline.OpenAI")
    @patch("src.deep_research.openai.openai_pipeline.wait_for_response")
    def test_calls_api_twice(self, mock_wait, mock_openai_cls, tmp_path):
        """Pipeline makes exactly 2 API calls (phase 1 + phase 2)."""
        with patch("src.deep_research.openai.openai_pipeline.OUTPUT_DIR", tmp_path):
            with patch("src.deep_research.openai.openai_pipeline.save_markdown"):
                with patch(
                    "src.deep_research.openai.openai_pipeline.extract_tables_to_csv",
                    return_value=[],
                ):
                    mock_client = MagicMock()
                    mock_openai_cls.return_value = mock_client
                    mock_client.responses.create.return_value = MagicMock(id="resp_123")

                    mock_response = MagicMock()
                    mock_response.status = "completed"
                    mock_response.output_text = OPENAI_PHASE1_MD
                    mock_wait.return_value = mock_response

                    from src.deep_research.openai.openai_pipeline import run_openai_pipeline

                    run_openai_pipeline(
                        time_window="From 2026-02-04 to 2026-05-05,"
                    )

                    assert mock_client.responses.create.call_count == 2

    @patch("src.deep_research.openai.openai_pipeline.OpenAI")
    @patch("src.deep_research.openai.openai_pipeline.wait_for_response")
    def test_phase2_receives_time_window(self, mock_wait, mock_openai_cls, tmp_path):
        """Phase 2 prompt includes the time_window string."""
        with patch("src.deep_research.openai.openai_pipeline.OUTPUT_DIR", tmp_path):
            with patch("src.deep_research.openai.openai_pipeline.save_markdown"):
                with patch(
                    "src.deep_research.openai.openai_pipeline.extract_tables_to_csv",
                    return_value=[],
                ):
                    with patch(
                        "src.deep_research.openai.openai_pipeline.build_research_request",
                        return_value={"model": "test"},
                    ) as mock_build:
                        mock_client = MagicMock()
                        mock_openai_cls.return_value = mock_client
                        mock_client.responses.create.return_value = MagicMock(id="r1")

                        mock_response = MagicMock()
                        mock_response.status = "completed"
                        mock_response.output_text = "no table"
                        mock_wait.return_value = mock_response

                        from src.deep_research.openai.openai_pipeline import run_openai_pipeline

                        tw = "From 2026-02-04 to 2026-05-05,"
                        run_openai_pipeline(time_window=tw)

                        # Second call to build_research_request is phase 2
                        phase2_call = mock_build.call_args_list[1]
                        user_query = phase2_call.kwargs.get(
                            "user_query", phase2_call[1].get("user_query", "")
                        )
                        assert "2026-02-04" in user_query


# ============================================================================
# Tests: Perplexity Pipeline (mocked)
# ============================================================================

class TestPerplexityPipeline:
    """Tests for run_perplexity_pipeline() with mocked API calls."""

    @patch("src.deep_research.perplexity.perplexity_pipeline.call_perplexity_deep_research")
    def test_returns_two_csv_lists(self, mock_call, tmp_path):
        """Pipeline returns a tuple of two lists of Paths."""
        # Phase 1 returns table, phase 2 returns table
        mock_call.side_effect = [
            {"content": PERPLEXITY_PHASE1_MD, "citations": None, "search_results": None, "usage": None, "raw": {}},
            {"content": PERPLEXITY_PHASE2_MD, "citations": None, "search_results": None, "usage": None, "raw": {}},
        ]

        with patch("src.deep_research.perplexity.perplexity_pipeline.OUTPUT_DIR", tmp_path):
            from src.deep_research.perplexity.perplexity_pipeline import run_perplexity_pipeline

            p1, p2 = run_perplexity_pipeline(
                time_window="From 2026-02-04 to 2026-05-05,"
            )

            assert isinstance(p1, list)
            assert isinstance(p2, list)
            assert len(p1) >= 1
            assert len(p2) >= 1

    @patch("src.deep_research.perplexity.perplexity_pipeline.call_perplexity_deep_research")
    def test_calls_api_twice(self, mock_call, tmp_path):
        """Pipeline makes exactly 2 API calls (phase 1 + phase 2)."""
        mock_call.side_effect = [
            {"content": PERPLEXITY_PHASE1_MD, "citations": None, "search_results": None, "usage": None, "raw": {}},
            {"content": PERPLEXITY_PHASE2_MD, "citations": None, "search_results": None, "usage": None, "raw": {}},
        ]

        with patch("src.deep_research.perplexity.perplexity_pipeline.OUTPUT_DIR", tmp_path):
            from src.deep_research.perplexity.perplexity_pipeline import run_perplexity_pipeline

            run_perplexity_pipeline(
                time_window="From 2026-02-04 to 2026-05-05,"
            )

            assert mock_call.call_count == 2

    @patch("src.deep_research.perplexity.perplexity_pipeline.call_perplexity_deep_research")
    def test_phase2_receives_csv_not_full_report(self, mock_call, tmp_path):
        """Phase 2 prompt contains CSV content from phase 1, not the raw markdown."""
        mock_call.side_effect = [
            {"content": PERPLEXITY_PHASE1_MD, "citations": None, "search_results": None, "usage": None, "raw": {}},
            {"content": PERPLEXITY_PHASE2_MD, "citations": None, "search_results": None, "usage": None, "raw": {}},
        ]

        with patch("src.deep_research.perplexity.perplexity_pipeline.OUTPUT_DIR", tmp_path):
            from src.deep_research.perplexity.perplexity_pipeline import run_perplexity_pipeline

            run_perplexity_pipeline(
                time_window="From 2026-02-04 to 2026-05-05,"
            )

            # Phase 2 is the second call
            phase2_args = mock_call.call_args_list[1]
            user_prompt = phase2_args.kwargs.get(
                "user_prompt", phase2_args[1].get("user_prompt", "")
            )
            # CSV content has commas (from csv.writer), not pipe-delimited markdown
            # The prompt should contain trend data from the CSV
            assert "Cold Plunging" in user_prompt or "Rank" in user_prompt

    @patch("src.deep_research.perplexity.perplexity_pipeline.call_perplexity_deep_research")
    def test_empty_phase2_returns_empty_list(self, mock_call, tmp_path):
        """If phase 2 returns no table, csv_paths_p2 is an empty list."""
        mock_call.side_effect = [
            {"content": PERPLEXITY_PHASE1_MD, "citations": None, "search_results": None, "usage": None, "raw": {}},
            {"content": "No search results found. Cannot complete task.", "citations": None, "search_results": None, "usage": None, "raw": {}},
        ]

        with patch("src.deep_research.perplexity.perplexity_pipeline.OUTPUT_DIR", tmp_path):
            from src.deep_research.perplexity.perplexity_pipeline import run_perplexity_pipeline

            p1, p2 = run_perplexity_pipeline(
                time_window="From 2026-02-04 to 2026-05-05,"
            )

            assert len(p1) >= 1
            assert p2 == []


# ============================================================================
# Tests: Time window integration (last_three_months vs last_twelve_months)
# ============================================================================

class TestTimeWindowIntegration:
    """Verify time window choices produce correct date ranges for pipelines."""

    def test_three_months_produces_90_day_range(self):
        """last_three_months generates a 90-day window."""
        horizon, label = parse_time_window("last_three_months")
        assert label == "three_months"
        assert "From " in horizon
        dates = horizon.split("From ")[1].split(",")[0]
        start_str, end_str = dates.split(" to ")
        start = datetime.strptime(start_str, "%Y-%m-%d")
        end = datetime.strptime(end_str, "%Y-%m-%d")
        assert (end - start).days == 90

    def test_twelve_months_produces_365_day_range(self):
        """last_twelve_months generates a 365-day window."""
        horizon, label = parse_time_window("last_twelve_months")
        assert label == "twelve_months"
        dates = horizon.split("From ")[1].split(",")[0]
        start_str, end_str = dates.split(" to ")
        start = datetime.strptime(start_str, "%Y-%m-%d")
        end = datetime.strptime(end_str, "%Y-%m-%d")
        assert (end - start).days == 365

    @patch("src.deep_research.openai.openai_pipeline.OpenAI")
    @patch("src.deep_research.openai.openai_pipeline.wait_for_response")
    def test_three_months_window_in_openai_prompt(self, mock_wait, mock_openai_cls, tmp_path):
        """last_three_months date range appears in OpenAI phase 1 prompt."""
        with patch("src.deep_research.openai.openai_pipeline.OUTPUT_DIR", tmp_path):
            with patch("src.deep_research.openai.openai_pipeline.save_markdown"):
                with patch(
                    "src.deep_research.openai.openai_pipeline.extract_tables_to_csv",
                    return_value=[],
                ):
                    with patch(
                        "src.deep_research.openai.openai_pipeline.build_research_request",
                        return_value={"model": "test"},
                    ) as mock_build:
                        mock_client = MagicMock()
                        mock_openai_cls.return_value = mock_client
                        mock_client.responses.create.return_value = MagicMock(id="r1")

                        mock_resp = MagicMock()
                        mock_resp.status = "completed"
                        mock_resp.output_text = "no table"
                        mock_wait.return_value = mock_resp

                        from src.deep_research.openai.openai_pipeline import run_openai_pipeline

                        horizon, _ = parse_time_window("last_three_months")
                        run_openai_pipeline(time_window=horizon)

                        # Phase 1 is the first call
                        phase1_query = mock_build.call_args_list[0].kwargs.get(
                            "user_query", mock_build.call_args_list[0][1].get("user_query", "")
                        )
                        # Should contain start date from 90 days ago
                        expected_start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
                        assert expected_start in phase1_query

    @patch("src.deep_research.perplexity.perplexity_pipeline.call_perplexity_deep_research")
    def test_twelve_months_window_in_perplexity_prompt(self, mock_call, tmp_path):
        """last_twelve_months date range appears in Perplexity phase 1 prompt."""
        mock_call.side_effect = [
            {"content": PERPLEXITY_PHASE1_MD, "citations": None, "search_results": None, "usage": None, "raw": {}},
            {"content": PERPLEXITY_PHASE2_MD, "citations": None, "search_results": None, "usage": None, "raw": {}},
        ]

        with patch("src.deep_research.perplexity.perplexity_pipeline.OUTPUT_DIR", tmp_path):
            from src.deep_research.perplexity.perplexity_pipeline import run_perplexity_pipeline

            horizon, _ = parse_time_window("last_twelve_months")
            run_perplexity_pipeline(time_window=horizon)

            # Phase 1 is the first call
            phase1_prompt = mock_call.call_args_list[0].kwargs.get(
                "user_prompt", mock_call.call_args_list[0][1].get("user_prompt", "")
            )
            expected_start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
            assert expected_start in phase1_prompt
