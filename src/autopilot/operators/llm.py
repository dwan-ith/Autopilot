"""Multi-provider LLM client for AUTOPILOT operators.

Supports OpenRouter and Groq through the OpenAI-compatible chat
completions API.  Falls back gracefully when no key is set so the
system remains demo-safe without credentials.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

log = logging.getLogger("autopilot.llm")

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDERS: list[dict[str, Any]] = [
    {
        "name": "openrouter",
        "env_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "default_model": "google/gemini-2.5-flash",
        "extra_headers": {
            "HTTP-Referer": "https://github.com/dwan-ith/Autopilot",
            "X-Title": "AUTOPILOT",
        },
    },
    {
        "name": "groq",
        "env_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1/chat/completions",
        "default_model": "llama-3.3-70b-versatile",
        "extra_headers": {},
    },
]

_provider_cache: dict[str, Any] | None = ...  # sentinel


def _detect_provider() -> dict[str, Any] | None:
    """Return the first provider whose API key is present in the env."""
    global _provider_cache
    if _provider_cache is not ...:
        return _provider_cache
    for cfg in PROVIDERS:
        key = os.getenv(cfg["env_key"], "").strip()
        if key:
            _provider_cache = {**cfg, "api_key": key}
            log.info("LLM provider: %s  model: %s", cfg["name"], cfg["default_model"])
            return _provider_cache
    _provider_cache = None
    log.warning("No LLM provider configured — operators will use heuristic fallback")
    return None


def active_provider_name() -> str:
    """Return a human-readable name for the active LLM provider."""
    p = _detect_provider()
    return f"{p['name']}:{p['default_model']}" if p else "heuristic"


# ---------------------------------------------------------------------------
# Core reasoning call
# ---------------------------------------------------------------------------

async def reason(
    system: str,
    prompt: str,
    *,
    model: str | None = None,
    temperature: float = 0.35,
    json_mode: bool = False,
    max_tokens: int = 2048,
) -> str | None:
    """Send a chat completion request to the best available provider.

    Returns the assistant message text, or ``None`` when no provider is
    configured or the call fails.
    """
    provider = _detect_provider()
    if provider is None:
        return None

    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
        **provider.get("extra_headers", {}),
    }

    body: dict[str, Any] = {
        "model": model or provider["default_model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(provider["base_url"], headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            log.debug("LLM response (%s): %.200s…", provider["name"], text)
            return text
    except httpx.HTTPStatusError as exc:
        log.error("LLM %s HTTP %s: %s", provider["name"], exc.response.status_code, exc.response.text[:400])
        return None
    except Exception as exc:
        log.error("LLM call to %s failed: %s", provider["name"], exc)
        return None


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def parse_json(text: str | None) -> dict[str, Any] | list | None:
    """Best-effort JSON extraction from potentially messy LLM output."""
    if not text:
        return None
    text = text.strip()

    # Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Markdown fenced block
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Outermost braces / brackets
    for open_ch, close_ch in [("{", "}"), ("[", "]")]:
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

    return None
