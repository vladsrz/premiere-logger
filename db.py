import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "time_log.db"
TICK_SECONDS = 10

DEFAULT_CATEGORIES = {
    "Adobe Premiere": "work",
    "After Effects":  "work",
    "DaVinci Resolve":"work",
    "Photoshop":      "work",
    "Audition":       "work",
    "VS Code":        "work",
}


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ticks (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            app       TEXT NOT NULL DEFAULT 'Adobe Premiere',
            project   TEXT NOT NULL,
            status    TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON ticks(timestamp)")

    # Migrations: add columns to existing installs that don't have them
    for migration in [
        "ALTER TABLE ticks ADD COLUMN app      TEXT NOT NULL DEFAULT 'Adobe Premiere'",
        "ALTER TABLE ticks ADD COLUMN sequence  TEXT",
        "ALTER TABLE ticks ADD COLUMN source    TEXT NOT NULL DEFAULT 'tracker'",
    ]:
        try:
            conn.execute(migration)
        except Exception:
            pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            app      TEXT PRIMARY KEY,
            category TEXT NOT NULL
        )
    """)

    for app, cat in DEFAULT_CATEGORIES.items():
        conn.execute("INSERT OR IGNORE INTO categories (app, category) VALUES (?, ?)", (app, cat))

    conn.commit()
    conn.close()
