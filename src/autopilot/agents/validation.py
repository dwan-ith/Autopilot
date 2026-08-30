"""Defensive coercion of LLM-produced values into typed, bounded fields.

LLMs routinely emit `"confidence": "high"`, priorities as words, out-of-range
floats, or free-text where an enum is required. Before this module existed a
single malformed field crashed the owning agent step (and often the mission).
Every consumer of LLM-structured output should pass values through these
helpers so malformed input degrades to a safe default instead.
"""

from __future__ import annotations

import re
from typing import Any

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_INT_PATTERN = re.compile(r"\d+")
_WORD_CONFIDENCE = {
    "very low": 0.1,
    "low": 0.25,
    "medium": 0.5,
    "moderate": 0.5,
    "high": 0.75,
    "very high": 0.9,
    "certain": 0.95,
}


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def safe_confidence(value: Any, default: float = 0.5) -> float:
    """Coerce an LLM-emitted confidence to a float in [0, 1].

    Accepts numeric values and qualitative words ("high", "moderate", ...).
    Never raises; falls back to `default` for None/garbage.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        try:
            return clamp(float(value))
        except (OverflowError, ValueError):
            return default
    if isinstance(value, str):
        text = value.strip().lower().rstrip("%")
        if text in _WORD_CONFIDENCE:
            return _WORD_CONFIDENCE[text]
        try:
            parsed = float(text)
        except ValueError:
            return default
        # "85" or "85%" almost certainly means 0.85
        if parsed > 1.0:
            parsed /= 100.0
        return clamp(parsed)
    return default


def safe_int(value: Any, default: int, lo: int | None = None, hi: int | None = None) -> int:
    """Coerce to int with optional bounds; words like 'high'/'urgent' map high."""
    result: int | None = None
    if isinstance(value, bool):
        result = int(value)
    elif isinstance(value, (int, float)):
        result = int(value)
    elif isinstance(value, str):
        text = value.strip().lower()
        if text in _SEVERITY_ORDER:
            result = _SEVERITY_ORDER[text] * 10
        else:
            match = _INT_PATTERN.search(text)
            if match:
                try:
                    result = int(match.group())
                except ValueError:  # pragma: no cover - \d+ always parses
                    result = None
    if result is None:
        result = default
    if lo is not None:
        result = max(lo, result)
    if hi is not None:
        result = min(hi, result)
    return result


def safe_severity(value: Any, default: str = "medium") -> str:
    """Map arbitrary severity/urgency text onto the canonical four-level scale."""
    if not isinstance(value, str):
        return default
    text = value.strip().lower()
    if text in _SEVERITY_ORDER:
        return text
    if text in {"sev1", "s1", "p0", "blocker", "emergency"}:
        return "critical"
    if text in {"sev2", "s2", "p1", "major", "elevated", "urgent"}:
        return "high"
    if text in {"sev3", "s3", "p2", "minor", "warning"}:
        return "medium"
    if text in {"sev4", "s4", "p3", "info", "informational", "notice"}:
        return "low"
    return default


def severity_at_least(severity: str, level: str) -> bool:
    """True when `severity` ranks at or above `level` on the canonical scale."""
    return _SEVERITY_ORDER.get(safe_severity(severity), 1) >= _SEVERITY_ORDER.get(level, 0)


def safe_str_list(value: Any, limit: int = 32) -> list[str]:
    """Coerce LLM list-of-anything into a bounded list of strings."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value[:limit]:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            items.append(text[:500])
    return items


def ensure_dict(value: Any) -> dict[str, Any]:
    """Return `value` if it is a dict, else an empty dict."""
    return value if isinstance(value, dict) else {}
