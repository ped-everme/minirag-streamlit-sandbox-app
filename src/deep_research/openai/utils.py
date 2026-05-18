"""
OpenAI Deep Research API utilities.

Provides helpers for building asynchronous deep research requests via the
OpenAI Responses API and polling for completion. Designed for use with
background-mode responses that leverage web search and code interpreter tools.
"""

import re
import csv
from pathlib import Path
import time
from datetime import datetime
from datetime import date
import logging

logger = logging.getLogger(__name__)
DEFAULT_POLL_SECONDS = 5
DEFAULT_TIMEOUT_MINUTES = 30

DEFAULT_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "incomplete",
}


def build_research_request(model: str, agent_prompt: str, user_query: str) -> dict:
    """
    Build the request payload for ``client.responses.create()``.

    Constructs a two-message conversation (developer instruction + user query)
    with web search and code interpreter tools enabled in background mode.
    The returned dict can be unpacked directly into the API call.

    Args:
        model (str): OpenAI model identifier
            (e.g. ``"o4-mini-deep-research"``).
        agent_prompt (str): Developer-level system instruction that sets the
            research agent's persona and constraints.
        user_query (str): The user-facing research query, already formatted
            with any dynamic placeholders (time horizon, region, etc.).

    Returns:
        dict: Keyword arguments ready for ``openai.Client.responses.create(**kwargs)``.
            Contains ``model``, ``input`` (messages), ``tools`` (web search +
            code interpreter), and ``background=True``.
    """
    return dict(
        model=model,
        input=[
            {
                "role": "developer",
                "content": [{"type": "input_text", "text": agent_prompt}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": user_query}],
            },
        ],
        tools=[
            {"type": "web_search_preview"},
            {"type": "code_interpreter", "container": {"type": "auto"}},
        ],
        background=True,
    )


def wait_for_response(
    client,
    response_id: str,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
    timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES,
    terminal_statuses: set[str] = DEFAULT_TERMINAL_STATUSES,
):
    """
    Poll an OpenAI background response until it reaches a terminal status.

    Periodically retrieves the response object by ID and checks its status.
    Prints a timestamped status line on each poll for live monitoring.
    Returns the completed response object or raises on timeout.

    Args:
        client: An initialised ``openai.OpenAI`` client instance.
        response_id (str): The response ID returned by
            ``client.responses.create()`` (e.g. ``"resp_abc123..."``).
        poll_seconds (int, optional): Seconds between consecutive status
            checks. Defaults to 5.
        timeout_minutes (int, optional): Maximum wall-clock minutes to wait
            before raising a timeout error. Defaults to 30.
        terminal_statuses (set[str], optional): Status strings that indicate
            the response is finished. Defaults to
            ``{"completed", "failed", "cancelled", "incomplete"}``.

    Returns:
        openai.types.Response: The fully resolved response object. Callers
            should check ``.status`` to confirm ``"completed"`` before
            accessing ``.output_text``.

    Raises:
        TimeoutError: If the response does not reach a terminal status
            within ``timeout_minutes``. The error message includes the
            ``response_id`` for manual recovery.
    """
    deadline = time.time() + timeout_minutes * 60

    while True:
        resp = client.responses.retrieve(response_id)
        status = getattr(resp, "status", None)
        now = datetime.now().strftime("%H:%M:%S")
        logger.info(f"[{now}] status={status}")

        if status in terminal_statuses:
            return resp

        if time.time() > deadline:
            raise TimeoutError(
                f"Timeout local após {timeout_minutes} minutos. "
                f"Response ID para tentar recuperar depois: {response_id}"
            )

        time.sleep(poll_seconds)