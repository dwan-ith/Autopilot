"""Multi-provider LLM client for AUTOPILOT — 6-key pool with role pinning.

Key assignment (prevents parallel agents from hitting the same rate-limit bucket):
  Slot 0 (OpenRouter-1): Investigator           — deep reasoning + search context
  Slot 1 (OpenRouter-2): Planner                — hypothesis & branch planning
  Slot 2 (OpenRouter-3): Verifier / Synthesizer — evidence scoring & brief writing
  Slot 3 (Groq-1):       Correlator             — fast entity correlation
  Slot 4 (Groq-2):       Executor / Validator   — fast action & post-check
  Slot 5 (Groq-3):       Governor / Memory      — fast policy & recall

Each slot is tried in order on 429 / timeout.  Returns None only when every
slot is exhausted, triggering the heuristic fallback path.
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
# Slot definitions  (name, base_url, env_key, model)
# ---------------------------------------------------------------------------

_SLOT_DEFS: list[tuple[str, str, str, str, dict]] = [
    (
        "openrouter-1",
        "https://openrouter.ai/api/v1/chat/completions",
        "OPENROUTER_API_KEY",
        "google/gemini-2.0-flash-001",
        {"HTTP-Referer": "https://github.com/dwan-ith/Autopilot", "X-Title": "AUTOPILOT"},
    ),
    (
        "openrouter-2",
        "https://openrouter.ai/api/v1/chat/completions",
        "OPENROUTER_API_KEY_2",
        "google/gemini-2.0-flash-001",
        {"HTTP-Referer": "https://github.com/dwan-ith/Autopilot", "X-Title": "AUTOPILOT"},
    ),
    (
        "openrouter-3",
        "https://openrouter.ai/api/v1/chat/completions",
        "OPENROUTER_API_KEY_3",
        "google/gemini-2.0-flash-001",
        {"HTTP-Referer": "https://github.com/dwan-ith/Autopilot", "X-Title": "AUTOPILOT"},
    ),
    (
        "groq-1",
        "https://api.groq.com/openai/v1/chat/completions",
        "GROQ_API_KEY",
        "llama-3.3-70b-versatile",
        {},
    ),
    (
        "groq-2",
        "https://api.groq.com/openai/v1/chat/completions",
        "GROQ_API_KEY_2",
        "llama-3.3-70b-versatile",
        {},
    ),
    (
        "groq-3",
        "https://api.groq.com/openai/v1/chat/completions",
        "GROQ_API_KEY_3",
        "llama-3.3-70b-versatile",
        {},
    ),
]

# Role → preferred slot index  (deterministic, no per-call overhead)
_ROLE_SLOT: dict[str, int] = {
    "investigator":  0,
    "planner":       1,
    "verifier":      2,
    "synthesizer":   2,
    "correlator":    3,
    "executor":      4,
    "validator":     4,
    "governor":      5,
    "memory":        5,
    # legacy / fallback roles
    "signal evaluator":   3,
    "mission planner":    1,
    "verification gate":  2,
    "synthesis operator": 2,
    "adaptive replanner": 1,
}

_DISABLE_LLM = os.getenv("AUTOPILOT_DISABLE_LLM", "").lower() in {"1", "true", "yes"}


def _build_slots() -> list[dict[str, Any]]:
    """Read env vars and return usable slot configs."""
    slots = []
    for name, url, env_key, model, extra in _SLOT_DEFS:
        key = os.getenv(env_key, "").strip()
        if key:
            slots.append({"name": name, "url": url, "key": key, "model": model, "extra": extra})
    return slots


def _slots_for_role(role: str) -> list[dict[str, Any]]:
    """Return slots ordered by preference for the given role."""
    all_slots = _build_slots()
    if not all_slots:
        return []
    preferred = _ROLE_SLOT.get(role.lower().strip(), 0)
    # Rotate so the preferred slot is first, cycle through the rest as fallback
    n = len(all_slots)
    order = [i % n for i in range(preferred, preferred + n)]
    seen, ordered = set(), []
    for i in order:
        if i not in seen:
            seen.add(i)
            ordered.append(all_slots[i])
    return ordered


def active_provider_name() -> str:
    if _DISABLE_LLM:
        return "heuristic"
    slots = _build_slots()
    if not slots:
        return "heuristic"
    names = [s["name"] for s in slots]
    return f"pool({len(names)}): {', '.join(names[:3])}{'…' if len(names) > 3 else ''}"


# ---------------------------------------------------------------------------
# Core reasoning call  (tries all available slots before giving up)
# ---------------------------------------------------------------------------

async def reason(
    system: str,
    prompt: str,
    *,
    role: str = "investigator",
    model: str | None = None,
    temperature: float = 0.35,
    json_mode: bool = False,
    max_tokens: int = 2048,
) -> str | None:
    """Send a chat-completion request using the best slot for the given role.

    Tries each slot in preference order, skipping on 429 / timeout / error.
    Returns the assistant message text, or ``None`` when all slots fail.
    """
    if _DISABLE_LLM:
        return None

    slots = _slots_for_role(role)
    if not slots:
        log.warning("No LLM provider configured — heuristic fallback")
        return None

    for slot in slots:
        result = await _call_slot(slot, system, prompt, model, temperature, json_mode, max_tokens)
        if result is not None:
            return result
        log.warning("Slot %s failed; trying next slot", slot["name"])

    log.error("All %d LLM slots exhausted for role '%s'", len(slots), role)
    return None


async def _call_slot(
    slot: dict[str, Any],
    system: str,
    prompt: str,
    model: str | None,
    temperature: float,
    json_mode: bool,
    max_tokens: int,
) -> str | None:
    headers = {
        "Authorization": f"Bearer {slot['key']}",
        "Content-Type": "application/json",
        **slot["extra"],
    }
    body: dict[str, Any] = {
        "model": model or slot["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(slot["url"], headers=headers, json=body)
            if resp.status_code == 429:
                log.warning("Rate-limit on slot %s", slot["name"])
                return None
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            log.debug("LLM %s: %.120s…", slot["name"], text)
            return text
    except httpx.HTTPStatusError as exc:
        log.error("LLM %s HTTP %s: %s", slot["name"], exc.response.status_code, exc.response.text[:300])
        return None
    except Exception as exc:
        log.error("LLM %s error: %s", slot["name"], exc)
        return None


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def parse_json(text: str | None) -> dict[str, Any] | list | None:
    """Best-effort JSON extraction from potentially messy LLM output."""
    if not text:
        return None
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    for open_ch, close_ch in [("{", "}"), ("[", "]")]:
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(text[start: end + 1])
            except json.JSONDecodeError:
                pass

    return None
