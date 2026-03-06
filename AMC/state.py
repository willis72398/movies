"""
Persistent state management — PostgreSQL backend.

All data lives in the `amc_showtimes` table (created by
migrations/001_init.sql).  Each row represents one IMAX showtime the bot
has ever detected; the row's existence is the "seen" signal.

Connection is configured via the DATABASE_URL environment variable:
  postgresql://movies:<password>@postgres:5432/movies

Public interface (unchanged — main.py requires minimal edits):
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

_TABLE = "amc_showtimes"


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

    Kept so main.py requires no changes beyond adding log_discoveries().
    """


def log_discoveries(new_showtimes: list[dict]) -> None:
    """
    Insert one row per newly-detected IMAX showtime into amc_showtimes.

    Each showtime dict is expected to have a '_theatre_name' key injected
    by main.py before this is called.

    ON CONFLICT DO NOTHING makes this idempotent.
    """
    if not new_showtimes:
        return

    now = datetime.now()
    rows = []
    for st in new_showtimes:
        sid = str(st.get("id", "")).strip()
        if not sid:
            continue

        movie = st.get("movieName") or st.get("movie", {}).get("name", "Unknown")
        theatre = st.get("_theatre_name", "")
        show_dt = st.get("showDateTimeLocal") or st.get("dateTime", "")
        attrs = st.get("attributes", [])
        fmt = ", ".join(a.get("name", "") for a in attrs) if attrs else "IMAX"
        links = st.get("_links", {})
        book_url = links.get("purchase:showtime", {}).get("href", "")
        if not book_url:
            book_url = f"https://www.amctheatres.com/showtimes/{sid}"

        rows.append((sid, movie, theatre, show_dt, fmt, book_url, now, now.hour, now.strftime("%A")))

    sql = f"""
        INSERT INTO {_TABLE}
            (id, movie_name, theatre_name, show_datetime, format,
             book_url, first_seen, hour, weekday)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
    """

    try:
        with _connect() as conn, conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows)
        logger.debug("Logged %d AMC discovery row(s) to %s.", len(rows), _TABLE)
    except Exception as exc:
        logger.error("Failed to log AMC discoveries to DB: %s", exc)


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
