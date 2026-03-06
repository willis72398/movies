"""
Persistent state management — SQLite backend.

Two tables live in a single `nitehawk.db` file:

  seen_showtimes   — one row per showtime ID that has already triggered an
                     alert; prevents duplicate notifications across restarts.

  discoveries      — append-only log of every newly-seen showtime with its
                     full metadata and the exact local timestamp it was first
                     detected.  Used by analyze.py to learn which hours
                     Nitehawk typically posts new programming.

The database path defaults to ./nitehawk.db and can be overridden with the
DB_FILE environment variable (useful for GitHub Actions / Docker volume mounts).

Public interface (unchanged from the previous JSON+CSV implementation):
  load_seen_ids()                → set[str]
  save_seen_ids(seen_ids)        → None
  log_discoveries(new_showtimes) → None
  find_new_showtimes(showtimes, seen_ids) → list[dict]
"""

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_FILE = Path(os.getenv("DB_FILE", "nitehawk.db"))

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS seen_showtimes (
    id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS discoveries (
    rowid        INTEGER PRIMARY KEY AUTOINCREMENT,
    showtime_id  TEXT    NOT NULL,
    discovered_at TEXT   NOT NULL,  -- ISO-8601 local datetime
    hour         INTEGER NOT NULL,  -- 0–23, for peak-hour analysis
    weekday      TEXT    NOT NULL,  -- Monday … Sunday
    title        TEXT    NOT NULL,
    date         TEXT    NOT NULL,  -- human-readable, e.g. "Wed, Apr 15"
    showtime     TEXT    NOT NULL,  -- e.g. "7:15 pm"
    location     TEXT    NOT NULL,  -- "Williamsburg" | "Prospect Park"
    series       TEXT    NOT NULL,  -- pipe-separated series labels
    purchase_url TEXT    NOT NULL,
    details_url  TEXT    NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    """Open (and if necessary create) the database, returning a connection."""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")   # safe concurrent writes
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_DDL)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_seen_ids() -> set[str]:
    """Return the set of showtime IDs that have already triggered an alert."""
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT id FROM seen_showtimes").fetchall()
        seen = {row[0] for row in rows}
        logger.debug("Loaded %d seen ID(s) from %s", len(seen), DB_FILE)
        return seen
    except sqlite3.Error as exc:
        logger.warning("Could not read DB (%s); starting fresh.", exc)
        return set()


def save_seen_ids(seen_ids: set[str]) -> None:
    """
    Persist seen showtime IDs to the database.

    Uses INSERT OR IGNORE so existing rows are left untouched and only
    genuinely new IDs are written.
    """
    if not seen_ids:
        return
    try:
        with _connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO seen_showtimes (id) VALUES (?)",
                [(sid,) for sid in seen_ids],
            )
            conn.commit()
        logger.debug("Saved %d ID(s) to %s", len(seen_ids), DB_FILE)
    except sqlite3.Error as exc:
        logger.error("Could not write seen IDs to DB: %s", exc)


def log_discoveries(new_showtimes: list[dict]) -> None:
    """
    Append one row per newly-detected showtime to the discoveries table.

    Captures the exact local timestamp so analyze.py can determine which
    hours Nitehawk tends to post new programming.
    """
    if not new_showtimes:
        return

    now = datetime.now()
    rows = [
        (
            st.get("id", ""),
            now.strftime("%Y-%m-%d %H:%M:%S"),
            now.hour,
            now.strftime("%A"),
            st.get("title", ""),
            st.get("date", ""),
            st.get("time", ""),
            st.get("location", ""),
            "|".join(st.get("series", [])),
            st.get("purchase_url", ""),
            st.get("details_url", ""),
        )
        for st in new_showtimes
    ]

    try:
        with _connect() as conn:
            conn.executemany(
                """
                INSERT INTO discoveries
                    (showtime_id, discovered_at, hour, weekday, title, date,
                     showtime, location, series, purchase_url, details_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        logger.debug(
            "Logged %d discovery row(s) → %s", len(new_showtimes), DB_FILE
        )
    except sqlite3.Error as exc:
        logger.warning("Could not write discovery log to DB: %s", exc)


def find_new_showtimes(showtimes: list[dict], seen_ids: set[str]) -> list[dict]:
    """
    Return only the showtimes whose ID has not been seen before,
    and update seen_ids in-place to include them.
    """
    new: list[dict] = []
    for st in showtimes:
        sid = str(st.get("id", "")).strip()
        if not sid:
            continue
        if sid not in seen_ids:
            new.append(st)
            seen_ids.add(sid)
    return new
