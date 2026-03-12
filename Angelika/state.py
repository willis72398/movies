"""
Persistent state management — PostgreSQL backend.

All data lives in the `angelika_showtimes` table (created by
migrations/003_angelika.sql). Each row represents one showtime the bot
has ever detected; its existence is the "seen" signal.

Connection is configured via the DATABASE_URL environment variable.

Public interface:
  load_seen_ids()                → set[str]
  save_seen_ids(seen_ids)        → None   (no-op; write happens in log_discoveries)
  log_discoveries(new_showtimes) → None
  find_new_showtimes(showtimes, seen_ids) → list[dict]
"""

import logging
import os
from datetime import datetime

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

_TABLE = "angelika_showtimes"


def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def load_seen_ids() -> set[str]:
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {_TABLE}")
            return {row[0] for row in cur.fetchall()}
    except Exception as exc:
        logger.warning("Could not load seen IDs from DB (%s); starting fresh.", exc)
        return set()


def save_seen_ids(seen_ids: set[str]) -> None:
    pass  # writes happen in log_discoveries


def log_discoveries(new_showtimes: list[dict]) -> None:
    if not new_showtimes:
        return

    now = datetime.now()
    rows = []
    for st in new_showtimes:
        rows.append((
            st["id"],
            st["title"],
            st["show_date"],
            st["show_time"],
            st["show_datetime"],
            st.get("format", ""),
            st.get("location", ""),
            st["ticket_url"],
            st["film_url"],
            now,
            now.hour,
            now.strftime("%A"),
        ))

    sql = f"""
        INSERT INTO {_TABLE}
            (id, title, show_date, show_time, show_datetime,
             format, location, ticket_url, film_url,
             first_seen, hour, weekday)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
    """

    try:
        with _connect() as conn, conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows)
        logger.debug("Logged %d Angelika discovery row(s).", len(rows))
    except Exception as exc:
        logger.error("Failed to log Angelika discoveries to DB: %s", exc)


def find_new_showtimes(showtimes: list[dict], seen_ids: set[str]) -> list[dict]:
    new = []
    for st in showtimes:
        sid = st.get("id", "").strip()
        if sid and sid not in seen_ids:
            new.append(st)
            seen_ids.add(sid)
    return new
