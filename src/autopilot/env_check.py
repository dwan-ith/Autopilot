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
