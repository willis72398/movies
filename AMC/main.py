"""
AMC Lincoln Square 13 IMAX Showtime Polling Bot

Polls the AMC website every POLL_INTERVAL_MINUTES minutes for new IMAX
showtimes and sends a Gmail notification whenever a previously-unseen
showing appears.

first_seen, hour, and weekday are stored in Postgres for every discovery
so that a future peak-hours feature can be built from real data.

Usage
-----
Local / Pi:     python main.py
GitHub Actions: python main.py --once
"""

import argparse
import logging
import os
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import find_dotenv, load_dotenv

from amc_client import AMCClient, THEATRE_NAME
from notifier import send_notification
from state import find_new_showtimes, load_seen_ids, log_discoveries, save_seen_ids

load_dotenv(find_dotenv(usecwd=True))

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
        logger.error("Required environment variable %s is not set. Check your .env file.", key)
        sys.exit(1)
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip() or default


GMAIL_USER        = _require("GMAIL_USER")
GMAIL_APP_PASSWORD = _require("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL      = _require("NOTIFY_EMAIL")

POLL_INTERVAL = int(_optional("AMC_POLL_INTERVAL_MINUTES", "15"))


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
client = AMCClient()
seen_ids: set[str] = set()


# ---------------------------------------------------------------------------
# Poll
# ---------------------------------------------------------------------------

def poll() -> None:
    logger.info("Polling %s…", THEATRE_NAME)

    try:
        showtimes = client.get_future_imax_showtimes()
    except Exception as exc:
        logger.error("Scrape failed: %s", exc)
        return

    logger.info("Fetched %d IMAX showtime(s).", len(showtimes))

    for st in showtimes:
        st["_theatre_name"] = THEATRE_NAME

    new = find_new_showtimes(showtimes, seen_ids)
    if new:
        logger.info("Found %d new showtime(s) — sending notification.", len(new))
        log_discoveries(new)
        send_notification(
            gmail_user=GMAIL_USER,
            gmail_app_password=GMAIL_APP_PASSWORD,
            notify_email=NOTIFY_EMAIL,
            new_showtimes=new,
            theatre_name=THEATRE_NAME,
        )
        save_seen_ids(seen_ids)
    else:
        logger.info("No new showtimes detected.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global seen_ids

    parser = argparse.ArgumentParser(description="AMC Lincoln Square IMAX Polling Bot")
    parser.add_argument("--once", action="store_true", help="Single poll then exit.")
    args = parser.parse_args()

    seen_ids = load_seen_ids()
    logger.info("Loaded %d previously seen showtime ID(s).", len(seen_ids))

    if args.once:
        poll()
        return

    # Run one poll immediately on startup, then on the interval.
    poll()

    scheduler = BlockingScheduler(timezone="local")
    scheduler.add_job(
        poll,
        trigger="interval",
        minutes=POLL_INTERVAL,
        id="poll",
        name=f"AMC IMAX poll (every {POLL_INTERVAL} min)",
    )

    logger.info("Scheduler started — polling every %d min. Press Ctrl+C to stop.", POLL_INTERVAL)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
        save_seen_ids(seen_ids)


if __name__ == "__main__":
    main()
