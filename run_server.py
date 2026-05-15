"""AUTOPILOT server entrypoint."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


if __name__ == "__main__":
    port = int(os.getenv("AUTOPILOT_PORT", "8090"))
    uvicorn.run("autopilot.api.main:app", host="127.0.0.1", port=port, log_level="info")
