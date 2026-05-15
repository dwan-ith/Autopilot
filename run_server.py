"""AUTOPILOT server entrypoint."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# Load environment variables early
load_dotenv(ROOT / ".env")


if __name__ == "__main__":
    from autopilot.api.main import app
    port = int(os.getenv("AUTOPILOT_PORT", "8090"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
