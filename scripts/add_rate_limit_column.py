"""
Migration: Add rate_limit_model column to the apikey table.

Usage:  python scripts/add_rate_limit_column.py
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "aethergate.db")


def migrate() -> None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check existing columns on the apikey table
    cursor.execute("PRAGMA table_info(apikey);")
    columns = [row[1] for row in cursor.fetchall()]

    if "rate_limit_model" in columns:
        print("Column already exists. No changes needed.")
    else:
        cursor.execute(
            "ALTER TABLE apikey ADD COLUMN rate_limit_model TEXT DEFAULT '60/m';"
        )
        conn.commit()
        print("Migration successful: added 'rate_limit_model' to apikey table.")

    conn.close()


if __name__ == "__main__":
    migrate()
