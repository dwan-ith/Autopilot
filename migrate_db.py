"""
Migration: fix connector_connections to use composite PK (connector_id, user_id).
Run once — safe to re-run (it checks if migration is already applied).
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "autopilot.db"

def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Check current schema
    schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='connector_connections'"
    ).fetchone()

    if schema is None:
        print("Table does not exist yet — no migration needed.")
        conn.close()
        return

    if "PRIMARY KEY (connector_id, user_id)" in schema["sql"]:
        print("Migration already applied — composite PK exists.")
        conn.close()
        return

    print("Migrating connector_connections to composite PK (connector_id, user_id)...")

    # Get existing rows
    rows = conn.execute("SELECT * FROM connector_connections").fetchall()
    print(f"  Found {len(rows)} existing row(s) — preserving data.")

    conn.execute("BEGIN")
    conn.execute("""
        CREATE TABLE connector_connections_new (
            connector_id text not null,
            user_id      text not null default 'default_user',
            status       text not null,
            auth_mode    text not null,
            granted_scopes text not null,
            connected_at text,
            credentials_ref text,
            metadata     text not null,
            PRIMARY KEY (connector_id, user_id)
        )
    """)

    for row in rows:
        conn.execute("""
            INSERT OR IGNORE INTO connector_connections_new
                (connector_id, user_id, status, auth_mode, granted_scopes,
                 connected_at, credentials_ref, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row["connector_id"],
            row["user_id"] if "user_id" in row.keys() else "default_user",
            row["status"],
            row["auth_mode"],
            row["granted_scopes"],
            row["connected_at"],
            row["credentials_ref"],
            row["metadata"],
        ))

    conn.execute("DROP TABLE connector_connections")
    conn.execute("ALTER TABLE connector_connections_new RENAME TO connector_connections")
    conn.execute("COMMIT")

    # Verify
    new_schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='connector_connections'"
    ).fetchone()
    print("  Migration complete. New schema:")
    print(" ", new_schema["sql"])
    conn.close()

if __name__ == "__main__":
    migrate()
