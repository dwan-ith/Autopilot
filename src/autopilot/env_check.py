import logging
import os

log = logging.getLogger("autopilot.env")

def validate_env() -> None:
    """Validate critical and optional environment variables before startup."""
    critical = [
        "AUTOPILOT_CREDENTIAL_ENCRYPTION_KEY",
    ]
    optional = [
        ("GROQ_API_KEY", "Required for Groq LLM slot."),
        ("OPENROUTER_API_KEY", "Required for OpenRouter LLM slot."),
        ("OPENWEATHER_API_KEY", "Required for high-rate weather connector."),
        ("TAVILY_API_KEY", "Required for fast Tavily search (will fallback to DDG)."),
    ]

    # In production, require strict environment keys
    is_prod = os.getenv("AUTOPILOT_ENV") == "production"

    if is_prod:
        missing = [key for key in critical if not os.getenv(key)]
        if missing:
            raise OSError(f"CRITICAL: Missing required environment variables in production: {', '.join(missing)}")

    # Binding beyond loopback without an API key exposes every endpoint —
    # including action approval, mission cancel, and webhook ingest — to the
    # whole network. Refuse in production, warn loudly otherwise.
    host = os.getenv("AUTOPILOT_HOST", "127.0.0.1").strip().lower()
    exposed = host not in {"127.0.0.1", "localhost", "::1"}
    if exposed and not os.getenv("AUTOPILOT_API_KEY", "").strip():
        msg = (
            f"AUTOPILOT_HOST={host} binds beyond loopback but AUTOPILOT_API_KEY is unset: "
            "every endpoint accepts unauthenticated requests."
        )
        if is_prod:
            raise OSError(f"CRITICAL: {msg}")
        log.warning("STARTUP: %s Set AUTOPILOT_API_KEY before exposing this service.", msg)

    # Log warnings for optionals
    warnings = []
    for key, reason in optional:
        if not os.getenv(key):
            warnings.append(f"{key}: {reason}")

    if warnings:
        log.warning("STARTUP: Missing optional environment variables. Some features will fallback or degrade:")
        for w in warnings:
            log.warning("  - %s", w)
    else:
        log.info("STARTUP: All standard environment variables are configured.")
