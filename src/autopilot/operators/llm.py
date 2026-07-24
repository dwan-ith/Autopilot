"""Multi-provider LLM client for AUTOPILOT with role-pinned slots."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
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
        "openrouter/free",
        {"HTTP-Referer": "https://github.com/dwan-ith/Autopilot", "X-Title": "AUTOPILOT"},
    ),
    (
        "openrouter-3",
        "https://openrouter.ai/api/v1/chat/completions",
        "OPENROUTER_API_KEY_3",
        "openrouter/free",
        {"HTTP-Referer": "https://github.com/dwan-ith/Autopilot", "X-Title": "AUTOPILOT"},
    ),
    # Primary OpenRouter key last — has no credits, will only be tried if all others fail
    (
        "openrouter-1",
        "https://openrouter.ai/api/v1/chat/completions",
        "OPENROUTER_API_KEY",
        "openrouter/free",
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
_SLOT_STATUS: dict[str, dict[str, Any]] = {}
_STATE_LOADED = False
_DEFAULT_STATE_PATH = Path(__file__).resolve().parents[3] / "data" / "provider_health.json"
_STATE_PATH = Path(os.getenv("AUTOPILOT_PROVIDER_HEALTH_PATH", "").strip() or str(_DEFAULT_STATE_PATH))


def _now() -> float:
    return time.time()


def _load_state() -> None:
    global _STATE_LOADED
    if _STATE_LOADED:
        return
    _STATE_LOADED = True
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    bad_slots = data.get("bad_slots", {})
    if isinstance(bad_slots, dict):
        now = _now()
        for name, item in bad_slots.items():
            if not isinstance(item, dict):
                continue
            until = float(item.get("until", 0.0) or 0.0)
            if until > now:
                _BAD_SLOTS[str(name)] = (until, str(item.get("reason", "")))
    statuses = data.get("slot_status", {})
    if isinstance(statuses, dict):
        _SLOT_STATUS.update({str(k): v for k, v in statuses.items() if isinstance(v, dict)})


def _save_state() -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(
            json.dumps(
                {
                    "bad_slots": {
                        name: {"until": until, "reason": reason}
                        for name, (until, reason) in _BAD_SLOTS.items()
                        if until > _now()
                    },
                    "slot_status": _SLOT_STATUS,
                    "updated_at": _now(),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    except OSError:
        log.debug("Could not persist provider health state", exc_info=True)


def _build_slots() -> list[dict[str, Any]]:
    _load_state()
    slots = []
    for name, url, env_key, model, extra in _SLOT_DEFS:
        key = os.getenv(env_key, "").strip()
        if key:
            model_env = "AUTOPILOT_OPENROUTER_MODEL" if name.startswith("openrouter") else "AUTOPILOT_GROQ_MODEL"
            selected_model = os.getenv(model_env, "").strip() or model
            slots.append({"name": name, "url": url, "env_key": env_key, "key": key, "model": selected_model, "extra": extra})
    return slots


def _slots_for_role(role: str) -> list[dict[str, Any]]:
    all_slots = _build_slots()
    now = _now()
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
            async with httpx.AsyncClient(timeout=_llm_timeout_seconds()) as client:
                resp = await client.post(slot["url"], headers=headers, json=body)
                if resp.status_code == 429:
                    delay = _retry_delay(resp, attempt)
                    _record_slot_status(slot["name"], "rate_limited", f"HTTP 429; retry in {delay:.2f}s")
                    if attempt + 1 >= max_attempts:
                        _quarantine_slot(slot["name"], f"HTTP 429 rate-limited after {max_attempts} attempt(s)", short=True)
                        return None
                    log.warning("Rate-limit on slot %s; retrying in %.2fs", slot["name"], delay)
                    await asyncio.sleep(delay)
                    continue
                if resp.status_code in {401, 402, 403}:
                    _quarantine_slot(slot["name"], f"HTTP {resp.status_code}: {resp.text[:160]}")
                    return None
                if 400 <= resp.status_code < 500:
                    _quarantine_slot(slot["name"], f"HTTP {resp.status_code}: {resp.text[:160]}")
                    return None
                if resp.status_code >= 500:
                    delay = min(2.0, 0.25 * (2**attempt))
                    _record_slot_status(
                        slot["name"],
                        "provider_error",
                        f"HTTP {resp.status_code}; retry in {delay:.2f}s",
                    )
                    if attempt + 1 >= max_attempts:
                        _quarantine_slot(
                            slot["name"],
                            f"HTTP {resp.status_code} after {max_attempts} attempt(s)",
                            short=True,
                        )
                        return None
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                _record_slot_status(slot["name"], "healthy", "last call succeeded")
                log.debug("LLM %s: %.120s", slot["name"], text)
                return text
        except httpx.TimeoutException:
            delay = min(2.0, 0.25 * (2**attempt))
            _record_slot_status(slot["name"], "timeout", "provider request timed out")
            if attempt + 1 >= max_attempts:
                _quarantine_slot(
                    slot["name"],
                    f"Provider timed out after {max_attempts} attempt(s)",
                    short=True,
                )
                return None
            log.warning("LLM %s timeout; retrying in %.2fs", slot["name"], delay)
            await asyncio.sleep(delay)
            continue
        except httpx.HTTPStatusError as exc:
            log.error("LLM %s HTTP %s: %s", slot["name"], exc.response.status_code, exc.response.text[:300])
            _quarantine_slot(
                slot["name"],
                f"HTTP {exc.response.status_code}: {exc.response.text[:160]}",
                short=exc.response.status_code >= 500,
            )
            return None
        except httpx.RequestError as exc:
            log.error("LLM %s transport error: %s", slot["name"], exc)
            _quarantine_slot(slot["name"], f"Transport error: {exc}", short=True)
            return None
        except Exception as exc:
            log.error("LLM %s error: %s", slot["name"], exc)
            _quarantine_slot(slot["name"], f"{type(exc).__name__}: {exc}", short=True)
            return None
    return None


def _llm_timeout_seconds() -> float:
    try:
        return max(5.0, float(os.getenv("AUTOPILOT_LLM_TIMEOUT_SECONDS", "30")))
    except ValueError:
        return 30.0


def _quarantine_slot(name: str, reason: str, *, short: bool = False) -> None:
    env_name = "AUTOPILOT_LLM_RATE_LIMIT_COOLDOWN_SECONDS" if short else "AUTOPILOT_LLM_BAD_SLOT_COOLDOWN_SECONDS"
    default = "90" if short else "900"
    cooldown = max(30, int(os.getenv(env_name, default)))
    until = _now() + cooldown
    _BAD_SLOTS[name] = (until, reason)
    _record_slot_status(name, "quarantined", reason, quarantined_until=until)
    _save_state()
    log.error("LLM slot %s quarantined for %ss: %s", name, cooldown, reason)


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    try:
        max_delay = max(0.1, float(os.getenv("AUTOPILOT_LLM_MAX_RETRY_DELAY_SECONDS", "2")))
    except ValueError:
        max_delay = 2.0
    retry_after = resp.headers.get("retry-after", "").strip()
    if retry_after:
        try:
            return min(max_delay, max(0.1, float(retry_after)))
        except ValueError:
            pass
    return min(max_delay, 0.5 * (2**attempt))


def _record_slot_status(
    name: str,
    status: str,
    detail: str,
    *,
    quarantined_until: float | None = None,
) -> None:
    _SLOT_STATUS[name] = {
        "status": status,
        "detail": detail,
        "last_checked_at": _now(),
        "quarantined_until": quarantined_until,
    }


def provider_health() -> dict[str, Any]:
    """Return provider pool status without exposing keys."""
    _load_state()
    now = _now()
    slots = _build_slots()
    rows = []
    for slot in slots:
        name = slot["name"]
        until, reason = _BAD_SLOTS.get(name, (0.0, ""))
        status = dict(_SLOT_STATUS.get(name, {}))
        quarantined = until > now
        rows.append({
            "name": name,
            "env_key": slot.get("env_key"),
            "model": slot["model"],
            "configured": True,
            "status": "quarantined" if quarantined else status.get("status", "unknown"),
            "detail": reason if quarantined else status.get("detail", "Configured; not preflighted yet."),
            "quarantined": quarantined,
            "quarantined_until": until if quarantined else None,
            "last_checked_at": status.get("last_checked_at"),
        })
    active = [row for row in rows if not row["quarantined"]]
    pname = active_provider_name()
    return {
        "disabled": _DISABLE_LLM,
        "provider": pname,
        "active_provider": pname,
        "configured_slots": len(rows),
        "total_slots": len(rows),
        "active_slots": len(active),
        "quarantined_slots": len(rows) - len(active),
        "slots": rows,
    }


async def preflight_providers() -> dict[str, Any]:
    """Run a bounded live check across configured LLM slots and quarantine bad keys."""
    if _DISABLE_LLM:
        return provider_health()
    for slot in _build_slots():
        if _BAD_SLOTS.get(slot["name"], (0.0, ""))[0] > _now():
            continue
        result = await _call_slot(
            slot,
            "Return exactly: ok",
            "Health check. Return exactly: ok",
            None,
            0.0,
            False,
            4,
        )
        if result is not None:
            _record_slot_status(slot["name"], "healthy", "preflight succeeded")
    _save_state()
    return provider_health()


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
