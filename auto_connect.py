import os
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DB_PATH = ROOT / "data" / "autopilot.db"

# API-key / token connectors: auto-connect when the env var is present.
API_KEY_CONNECTORS = {
    "linear":  "LINEAR_API_KEY",
    "notion":  "NOTION_API_KEY",
    "tavily":  "TAVILY_API_KEY",
    "weather": "OPENWEATHER_API_KEY",
    "sentry":  "SENTRY_TOKEN",
}

# Connectors that always work (webhook ingestion / local fallback).
# No env vars needed — they self-configure at runtime.
ALWAYS_CONNECTED = ["sentry", "slack", "webhook"]

# OAuth connectors should NOT be auto-connected by this script.
# They require the user to complete an interactive OAuth flow.
# GitHub, Gmail, Google Drive are handled by the /oauth/callback/* routes.


def auto_connect():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}. Run the app first to initialize it.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    now = datetime.now(timezone.utc).isoformat()
    user_id = "default_user"

    def upsert(connector_id: str, env_var: str, auth_mode: str):
        cursor.execute(
            "SELECT 1 FROM connector_connections WHERE connector_id = ? AND user_id = ?",
            (connector_id, user_id),
        )
        if cursor.fetchone():
            print(f"  {connector_id} already connected. Updating...")
            cursor.execute("""
                UPDATE connector_connections
                SET status = 'connected', connected_at = ?
                WHERE connector_id = ? AND user_id = ?
            """, (now, connector_id, user_id))
        else:
            metadata = {
                "display_name": connector_id.replace("_", " ").title(),
                "auto_connected": True,
                "configured": True,
            }
            cursor.execute("""
                INSERT INTO connector_connections
                (connector_id, user_id, status, auth_mode, granted_scopes, connected_at, credentials_ref, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                connector_id,
                user_id,
                "connected",
                auth_mode,
                "[]",
                now,
                env_var,
                json.dumps(metadata),
            ))
            print(f"  {connector_id} auto-connected.")

    # 1. API-key-gated connectors
    for connector_id, env_var in API_KEY_CONNECTORS.items():
        val = os.getenv(env_var)
        if val and val.strip():
            print(f"Found configuration for {connector_id} ({env_var}).")
            upsert(connector_id, env_var, "api_key")

    # 2. Always-on connectors (webhook / local fallback)
    for connector_id in ALWAYS_CONNECTED:
        print(f"Auto-connecting always-on connector: {connector_id}")
        upsert(connector_id, "ALWAYS_ON", "webhook")

    conn.commit()
    conn.close()
    print("Auto-connection complete.")
    print()
    print("NOTE: OAuth connectors (GitHub, Gmail, Google Drive) require")
    print("      interactive login. Use the 'Connect' button in the UI.")


if __name__ == "__main__":
    auto_connect()
