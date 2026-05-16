"""Multi-provider LLM client for AUTOPILOT with role-pinned slots."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

import httpx

log = logging.getLogger("autopilot.llm")


_SLOT_DEFS: list[tuple[str, str, str, str, dict]] = [
    # Groq slots first — fast and all keys are valid
    ("groq-1", "https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "llama-3.3-70b-versatile", {}),
    ("groq-2", "https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY_2", "llama-3.3-70b-versatile", {}),
    ("groq-3", "https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY_3", "llama-3.3-70b-versatile", {}),
    # OpenRouter slots as fallback (key-1 has no credits; key-2 and key-3 may work)
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
    # Primary OpenRouter key last — has no credits, will only be tried if all others fail
    (
        "openrouter-1",
        "https://openrouter.ai/api/v1/chat/completions",
        "OPENROUTER_API_KEY",
        "google/gemini-2.0-flash-001",
        {"HTTP-Referer": "https://github.com/dwan-ith/Autopilot", "X-Title": "AUTOPILOT"},
    ),
]

_ROLE_SLOT: dict[str, int] = {
    # Slots 0-2 = groq-1/2/3 (fast), slots 3-5 = openrouter-2/3/1 (fallback)
    "investigator": 0,
    "planner": 1,
    "verifier": 2,
    "synthesizer": 2,
    "correlator": 0,
    "executor": 1,
    "validator": 2,
    "governor": 0,
    "memory": 1,
    "reflection": 2,
    "signal evaluator": 0,
    "mission planner": 1,
    "verification gate": 2,
    "synthesis operator": 1,
    "adaptive replanner": 0,
}

_DISABLE_LLM = os.getenv("AUTOPILOT_DISABLE_LLM", "").lower() in {"1", "true", "yes"}
_BAD_SLOTS: dict[str, tuple[float, str]] = {}


def _build_slots() -> list[dict[str, Any]]:
    slots = []
    for name, url, env_key, model, extra in _SLOT_DEFS:
        key = os.getenv(env_key, "").strip()
        if key:
            slots.append({"name": name, "url": url, "key": key, "model": model, "extra": extra})
    return slots


def active_provider_name() -> str:
    """Return the name of the first non-quarantined available slot (for metrics)."""
    now = time.time()
    cooldown = float(os.getenv("AUTOPILOT_LLM_BAD_SLOT_COOLDOWN_SECONDS", "300"))
    for slot in _build_slots():
        name = slot["name"]
        if name in _BAD_SLOTS:
            bad_at, _ = _BAD_SLOTS[name]
            if now - bad_at < cooldown:
                continue
        return name
    return "none"


def _slots_for_role(role: str) -> list[dict[str, Any]]:
    all_slots = _build_slots()
    now = time.time()
    all_slots = [
        slot for slot in all_slots
        if _BAD_SLOTS.get(slot["name"], (0.0, ""))[0] <= now
    ]
    if not all_slots:
        return []
    preferred = _ROLE_SLOT.get(role.lower().strip(), 0)
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
    now = time.time()
    names = [
        slot["name"] for slot in slots
        if _BAD_SLOTS.get(slot["name"], (0.0, ""))[0] <= now
    ]
    if not names:
        return "heuristic"
    suffix = "..." if len(names) > 3 else ""
    quarantined = len(slots) - len(names)
    quarantine_note = f"; {quarantined} quarantined" if quarantined else ""
    return f"pool({len(names)}): {', '.join(names[:3])}{suffix}{quarantine_note}"


async def reason(
    system: str,
    prompt: str,
    *,
    role: str = "investigator",
    model: str | None = None,
    temperature: float = 0.35,
    json_mode: bool = False,
    max_tokens: int = 1024,
) -> str | None:
    """Call the best available LLM slot for the role, then fall back across slots."""
    if _DISABLE_LLM:
        return None

    slots = _slots_for_role(role)
    if not slots:
        log.warning("No LLM provider configured; heuristic fallback")
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
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    max_attempts = max(1, int(os.getenv("AUTOPILOT_LLM_RETRIES", "2")))
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(slot["url"], headers=headers, json=body)
                if resp.status_code == 429:
                    delay = _retry_delay(resp, attempt)
                    log.warning("Rate-limit on slot %s; retrying in %.2fs", slot["name"], delay)
                    await asyncio.sleep(delay)
                    continue
                if resp.status_code in {401, 402, 403}:
                    _quarantine_slot(slot["name"], f"HTTP {resp.status_code}: {resp.text[:160]}")
                    return None
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                log.debug("LLM %s: %.120s", slot["name"], text)
                return text
        except httpx.TimeoutException:
            delay = min(2.0, 0.25 * (2**attempt))
            log.warning("LLM %s timeout; retrying in %.2fs", slot["name"], delay)
            await asyncio.sleep(delay)
            continue
        except httpx.HTTPStatusError as exc:
            log.error("LLM %s HTTP %s: %s", slot["name"], exc.response.status_code, exc.response.text[:300])
            return None
        except Exception as exc:
            log.error("LLM %s error: %s", slot["name"], exc)
            return None
    return None


def _quarantine_slot(name: str, reason: str) -> None:
    cooldown = max(30, int(os.getenv("AUTOPILOT_LLM_BAD_SLOT_COOLDOWN_SECONDS", "900")))
    _BAD_SLOTS[name] = (time.time() + cooldown, reason)
    log.error("LLM slot %s quarantined for %ss: %s", name, cooldown, reason)


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    retry_after = resp.headers.get("retry-after", "").strip()
    if retry_after:
        try:
            return min(10.0, max(0.1, float(retry_after)))
        except ValueError:
            pass
    return min(5.0, 0.5 * (2**attempt))


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
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

    return None
