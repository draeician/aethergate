"""
Migration: Extract api_base/api_key from llmmodel into new llmendpoint table.

Steps:
  1. Create llmendpoint table if it doesn't exist.
  2. Add endpoint_id, rpm_limit, day_limit columns to llmmodel if missing.
  3. For each model with a custom api_base, create an LLMEndpoint and link it.
  4. Leave old api_base/api_key columns in place (SQLite can't DROP COLUMN
     on older versions; they'll just be ignored by SQLModel).

Usage:
    python scripts/migrate_endpoints.py
"""

import sqlite3
import sys

DB_PATH = "aethergate.db"


def get_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table});")
    return {row[1] for row in cursor.fetchall()}


def table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (table,)
    )
    return cursor.fetchone() is not None


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # ---- Step 1: Create llmendpoint table ----
    if not table_exists(cur, "llmendpoint"):
        print("Creating table: llmendpoint")
        cur.execute("""
            CREATE TABLE llmendpoint (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                name     TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key  TEXT,
                rpm_limit INTEGER,
                day_limit INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1
            );
        """)
    else:
        print("Table llmendpoint already exists — skipping creation.")

    # ---- Step 2: Add new columns to llmmodel ----
    model_cols = get_columns(cur, "llmmodel")

    for col, typedef in [
        ("endpoint_id", "INTEGER REFERENCES llmendpoint(id)"),
        ("rpm_limit", "INTEGER"),
        ("day_limit", "INTEGER"),
    ]:
        if col not in model_cols:
            print(f"Adding column: llmmodel.{col}")
            cur.execute(f"ALTER TABLE llmmodel ADD COLUMN {col} {typedef};")
        else:
            print(f"Column llmmodel.{col} already exists — skipping.")

    conn.commit()

    # ---- Step 3: Migrate existing api_base/api_key into endpoints ----
    if "api_base" in model_cols:
        cur.execute("SELECT id, api_base, api_key FROM llmmodel WHERE api_base IS NOT NULL AND api_base != '';")
        rows = cur.fetchall()

        if rows:
            print(f"Migrating {len(rows)} model(s) with custom api_base...")
            for model_id, api_base, api_key in rows:
                # Check if an endpoint with this base_url already exists
                cur.execute("SELECT id FROM llmendpoint WHERE base_url = ?;", (api_base,))
                existing = cur.fetchone()

                if existing:
                    ep_id = existing[0]
                    print(f"  {model_id} -> reusing endpoint #{ep_id} ({api_base})")
                else:
                    ep_name = f"Endpoint for {model_id}"
                    cur.execute(
                        "INSERT INTO llmendpoint (name, base_url, api_key, is_active) VALUES (?, ?, ?, 1);",
                        (ep_name, api_base, api_key),
                    )
                    ep_id = cur.lastrowid
                    print(f"  {model_id} -> created endpoint #{ep_id} '{ep_name}'")

                cur.execute(
                    "UPDATE llmmodel SET endpoint_id = ? WHERE id = ?;",
                    (ep_id, model_id),
                )

            conn.commit()
        else:
            print("No models with custom api_base found — nothing to migrate.")
    else:
        print("Column api_base not found in llmmodel — fresh install, skipping data migration.")

    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    main()
