"""
Perplexity API utilities for the Deep Research pipeline.

Provides a retry-capable wrapper around the Perplexity Sonar Deep Research
endpoint, handling authentication, payload construction, error retries with
back-off, and structured response parsing.
"""

import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

PERPLEXITY_API_URL = os.getenv("PERPLEXITY_API_URL", "https://api.perplexity.ai/v1/sonar")


def call_perplexity_deep_research(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    search_after: str | None = None,
    search_before: str | None = None,
    temperature: float = 0.2,
    max_retries: int = 2,
) -> dict:
    """
    Call the Perplexity Sonar Deep Research API and return structured results.

    Sends a chat-completion request with web search enabled, automatically
    retrying on transient failures with linear back-off. The web search is
    scoped to US-based sources within the provided date range.

    Args:
        api_key (str): Perplexity API bearer token.
        model (str): Model identifier (e.g. ``"sonar-deep-research"``).
        system_prompt (str): System-level instruction for the model. If empty
            or falsy, only the user message is sent.
        user_prompt (str): The user-facing research query or refinement prompt.
        search_after (str | None, optional): Start date filter for web search
            in ``MM/DD/YYYY`` format (e.g. ``"02/05/2026"``). If None, no
            start filter is applied. Defaults to None.
        search_before (str | None, optional): End date filter for web search
            in ``MM/DD/YYYY`` format (e.g. ``"05/06/2026"``). If None, no
            end filter is applied. Defaults to None.
        temperature (float, optional): Sampling temperature. Lower values
            produce more deterministic output. Defaults to 0.2.
        max_retries (int, optional): Maximum number of retry attempts after
            the initial call. Total attempts = ``max_retries + 1``.
            Defaults to 2.

    Returns:
        dict: Parsed response with the following keys:
            - ``content`` (str): The model's text response.
            - ``citations`` (list | None): Source citations returned by the API.
            - ``search_results`` (list | None): Raw search results used by the model.
            - ``usage`` (dict | None): Token usage statistics.
            - ``raw`` (dict): The full, unmodified API response payload.

    Raises:
        RuntimeError: If the API returns an HTTP status >= 400 on the final attempt.
        Exception: Any other request or parsing error after all retries are exhausted.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    web_search_options = {
        "search_context_size": "high",
        "user_location": {"country": "US"},
    }
    if search_after:
        web_search_options["search_after_date_filter"] = search_after
    if search_before:
        web_search_options["search_before_date_filter"] = search_before

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "web_search_options": web_search_options,
    }

    last_err = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(PERPLEXITY_API_URL, headers=headers, json=payload, timeout=300)
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:1000]}")
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return {
                "content": content,
                "citations": data.get("citations"),
                "search_results": data.get("search_results"),
                "usage": data.get("usage"),
                "raw": data,
            }
        except Exception as e:
            last_err = e
            logger.warning(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries:
                time.sleep(2 + attempt * 3)
            else:
                raise last_err