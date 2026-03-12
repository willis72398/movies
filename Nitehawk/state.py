"""
Persistent state management — PostgreSQL backend.

All data lives in the `nitehawk_showtimes` table (created by
migrations/001_init.sql).  Each row represents one showtime the bot has
ever detected; the row's existence is the "seen" signal.

Connection is configured via the DATABASE_URL environment variable:
  postgresql://movies:<password>@postgres:5432/movies

Public interface (unchanged — main.py requires no edits):
  load_seen_ids()                → set[str]
  save_seen_ids(seen_ids)        → None   (no-op; DB write happens in log_discoveries)
  log_discoveries(new_showtimes) → None
  find_new_showtimes(showtimes, seen_ids) → list[dict]
"""

import logging
import os
from datetime import datetime

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

_TABLE = "nitehawk_showtimes"


def _connect() -> psycopg2.extensions.connection:
    url = os.environ["DATABASE_URL"]
    return psycopg2.connect(url)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_seen_ids() -> set[str]:
    """Return all showtime IDs already stored in the database."""
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {_TABLE}")
            return {row[0] for row in cur.fetchall()}
    except Exception as exc:
        logger.warning("Could not load seen IDs from DB (%s); starting fresh.", exc)
        return set()


def save_seen_ids(seen_ids: set[str]) -> None:
    """
    No-op — the database write is handled by log_discoveries().

    Kept so main.py requires no changes.
    """


def log_discoveries(new_showtimes: list[dict]) -> None:
    """
    Insert one row per newly-detected showtime into nitehawk_showtimes.

    ON CONFLICT DO NOTHING makes this idempotent — safe to call even if a
    showtime somehow appears twice in the same poll cycle.
    """
    if not new_showtimes:
        return

    now = datetime.now()
    rows = [
        (
            st.get("id", ""),
            st.get("title", ""),
            st.get("date", ""),
            st.get("time", ""),
            st.get("location", ""),
            "|".join(st.get("series", [])),
            st.get("purchase_url", ""),
            st.get("details_url", ""),
            now,
            now.hour,
            now.strftime("%A"),
        )
        for st in new_showtimes
    ]

    sql = f"""
        INSERT INTO {_TABLE}
            (id, title, date, showtime, location, series,
             purchase_url, details_url, first_seen, hour, weekday)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
    """

    try:
        with _connect() as conn, conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows)
        logger.debug("Logged %d discovery row(s) to %s.", len(rows), _TABLE)
    except Exception as exc:
        logger.error("Failed to log discoveries to DB: %s", exc)


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
