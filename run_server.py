"""AUTOPILOT server entrypoint with reloading and PYTHONPATH setup."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "autopilot.api.main:app",
        host="127.0.0.1",
        port=8080,
        reload=True,
        reload_dirs=[str(ROOT / "src")],
        log_level="info",
    )
