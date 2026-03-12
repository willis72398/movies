"""
Angelika Film Center Showtime Polling Bot

Polls all three Angelika Film Center locations (NYC, Mosaic, Dallas) every
ANGELIKA_POLL_INTERVAL_MINUTES minutes and sends a Gmail notification when
previously-unseen showtimes appear.

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

from angelika_client import fetch_showtimes
from notifier import send_notification
from state import find_new_showtimes, load_seen_ids, log_discoveries, save_seen_ids

load_dotenv(find_dotenv(usecwd=True))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        logger.error("Required environment variable %s is not set.", key)
        sys.exit(1)
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip() or default


GMAIL_USER         = _require("GMAIL_USER")
GMAIL_APP_PASSWORD = _require("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL       = _require("NOTIFY_EMAIL")
POLL_INTERVAL      = int(_optional("ANGELIKA_POLL_INTERVAL_MINUTES", "30"))

seen_ids: set[str] = set()


def poll() -> None:
    logger.info("Polling Angelika Film Center (NYC, Mosaic, Dallas)…")

    try:
        showtimes = fetch_showtimes()
    except Exception as exc:
        logger.error("Scrape failed: %s", exc)
        return

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
        logger.info("No new showtimes detected.")


def main() -> None:
    global seen_ids

    parser = argparse.ArgumentParser(description="Angelika Film Center Polling Bot")
    parser.add_argument("--once", action="store_true", help="Single poll then exit.")
    args = parser.parse_args()

    seen_ids = load_seen_ids()
    logger.info("Loaded %d previously seen showtime ID(s).", len(seen_ids))

    if args.once:
        poll()
        return

    poll()

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        poll,
        trigger="interval",
        minutes=POLL_INTERVAL,
        id="poll",
        name=f"Angelika poll (every {POLL_INTERVAL} min)",
    )
    logger.info("Scheduler started — polling every %d min. Press Ctrl+C to stop.", POLL_INTERVAL)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
        save_seen_ids(seen_ids)


if __name__ == "__main__":
    main()
