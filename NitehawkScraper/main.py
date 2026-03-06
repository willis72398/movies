"""
Nitehawk Special Screenings Bot

Polls the Nitehawk Cinema "Coming Soon" pages (both Williamsburg and Prospect
Park) for new Special Screenings and sends a Gmail alert whenever a
previously-unseen showtime appears.

First-Run films are completely ignored — only curated/series/event screenings
from the #special-screenings section of each location's page are tracked.

Scheduling (local / Raspberry Pi mode)
---------------------------------------
The bot supports two polling intervals:

  Normal  — POLL_INTERVAL_MINUTES   (default 30)  used outside peak hours
  Peak    — PEAK_POLL_INTERVAL_MINUTES (default 15) used inside peak hours

Peak hours are defined by PEAK_HOURS_START and PEAK_HOURS_END (24-h "HH:MM",
local time).  The defaults (11:00–18:00) are a reasonable starting guess
for when a Brooklyn cinema's staff is likely online posting new programming.

After running for a few weeks, run `python analyze.py` to see the actual
hour-by-hour distribution of discoveries and tune these values accordingly.

Usage
-----
Local / Pi (continuous):  python main.py
GitHub Actions (one-shot): python main.py --once

Environment variables (.env locally, GitHub Actions secrets in CI)
------------------------------------------------------------------
GMAIL_USER                 — Gmail address used to send notifications
GMAIL_APP_PASSWORD         — 16-char App Password (not your real Gmail password)
NOTIFY_EMAIL               — Address to receive alerts
POLL_INTERVAL_MINUTES      — Off-peak interval in minutes       (default 30)
PEAK_POLL_INTERVAL_MINUTES — Peak interval in minutes           (default 15)
PEAK_HOURS_START           — Start of peak window, HH:MM local  (default 11:00)
PEAK_HOURS_END             — End of peak window,   HH:MM local  (default 18:00)
DB_FILE                    — Path to SQLite database             (default ./nitehawk.db)
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv

from notifier import send_notification
from scraper import fetch_all_locations
from state import find_new_showtimes, load_seen_ids, log_discoveries, save_seen_ids

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        logger.error("Required environment variable %s is not set.", key)
        sys.exit(1)
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip() or default


GMAIL_USER = _require("GMAIL_USER")
GMAIL_APP_PASSWORD = _require("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL = _require("NOTIFY_EMAIL")

POLL_INTERVAL = int(_optional("POLL_INTERVAL_MINUTES", "30"))
PEAK_INTERVAL = int(_optional("PEAK_POLL_INTERVAL_MINUTES", "15"))
PEAK_START = _optional("PEAK_HOURS_START", "11:00")
PEAK_END = _optional("PEAK_HOURS_END", "18:00")

# Module-level seen_ids shared across poll calls in continuous mode
seen_ids: set[str] = set()


# ---------------------------------------------------------------------------
# Peak-hours helper
# ---------------------------------------------------------------------------


def _is_peak() -> bool:
    """Return True if the current local time falls inside the peak window."""
    now = datetime.now().strftime("%H:%M")
    return PEAK_START <= now < PEAK_END


def _current_interval() -> int:
    return PEAK_INTERVAL if _is_peak() else POLL_INTERVAL


# ---------------------------------------------------------------------------
# Poll
# ---------------------------------------------------------------------------


def _do_poll() -> None:
    """Fetch showtimes, diff against known IDs, notify, log, and persist state."""
    mode = "peak" if _is_peak() else "normal"
    logger.info("Polling Nitehawk special screenings [%s window]…", mode)

    try:
        showtimes = fetch_all_locations()
    except Exception as exc:
        logger.error("Scrape failed: %s", exc)
        return

    logger.info("Fetched %d total special screening showtime(s).", len(showtimes))

    new = find_new_showtimes(showtimes, seen_ids)
    if new:
        logger.info("Found %d new showtime(s) — sending notification.", len(new))
        log_discoveries(new)
        send_notification(
            gmail_user=GMAIL_USER,
            gmail_app_password=GMAIL_APP_PASSWORD,
            notify_email=NOTIFY_EMAIL,
            new_showtimes=new,
        )
        save_seen_ids(seen_ids)
    else:
        logger.info("No new special screenings detected.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    global seen_ids

    parser = argparse.ArgumentParser(description="Nitehawk Special Screenings Bot")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll and exit (used by GitHub Actions).",
    )
    args = parser.parse_args()

    seen_ids = load_seen_ids()
    logger.info("Loaded %d previously seen showtime ID(s).", len(seen_ids))

    # -----------------------------------------------------------------------
    # GitHub Actions mode: one poll, then exit
    # -----------------------------------------------------------------------
    if args.once:
        logger.info("Running in single-poll mode (--once).")
        _do_poll()
        return

    # -----------------------------------------------------------------------
    # Local / Raspberry Pi continuous mode
    # -----------------------------------------------------------------------
    logger.info(
        "Starting continuous polling. "
        "Normal: every %d min | Peak (%s–%s): every %d min. "
        "Press Ctrl+C to stop.",
        POLL_INTERVAL,
        PEAK_START,
        PEAK_END,
        PEAK_INTERVAL,
    )
    try:
        while True:
            _do_poll()
            interval = _current_interval()
            logger.info(
                "Sleeping %d minute(s) [%s window] until next poll…",
                interval,
                "peak" if _is_peak() else "normal",
            )
            time.sleep(interval * 60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped. Saving state…")
        save_seen_ids(seen_ids)


if __name__ == "__main__":
    main()
