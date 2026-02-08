"""
Migration: Add api_base and api_key columns to llmmodel table.

Usage:
    python scripts/add_model_config_columns.py
"""

import sqlite3
import sys

DB_PATH = "aethergate.db"


def get_existing_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table});")
    return {row[1] for row in cursor.fetchall()}


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    columns = get_existing_columns(cursor, "llmmodel")

    if "api_base" not in columns:
        print("Adding column: llmmodel.api_base")
        cursor.execute("ALTER TABLE llmmodel ADD COLUMN api_base TEXT;")
    else:
        print("Column llmmodel.api_base already exists — skipping.")

    if "api_key" not in columns:
        print("Adding column: llmmodel.api_key")
        cursor.execute("ALTER TABLE llmmodel ADD COLUMN api_key TEXT;")
    else:
        print("Column llmmodel.api_key already exists — skipping.")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    main()
