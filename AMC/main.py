"""
AMC IMAX Showtime Polling Bot

Polls the AMC Theatres API for new IMAX showtimes at a configured theatre
and sends a Gmail notification whenever a previously-unseen showing appears.

Scheduling strategy (local mode)
---------------------------------
- During the PEAK_HOURS window (default 09:00–12:00 local time), poll every
  PEAK_POLL_INTERVAL_MINUTES (default 5) minutes.
- Outside that window, poll every POLL_INTERVAL_MINUTES (default 15) minutes.

APScheduler fires both jobs; each checks whether it is currently the "active"
schedule and skips otherwise, so only one poll runs at any given time.

GitHub Actions mode (--once)
-----------------------------
Pass --once to perform a single poll and exit immediately. The cron schedule
in .github/workflows/poll.yml handles the polling frequency instead of
APScheduler. THEATRE_NUMBER must be set (non-interactive).

Usage
-----
Local:          python main.py
GitHub Actions: python main.py --once
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

from amc_client import AMCClient
from notifier import send_notification
from state import find_new_showtimes, load_seen_ids, save_seen_ids

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
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


API_KEY = _require("AMC_API_KEY")
GMAIL_USER = _require("GMAIL_USER")
GMAIL_APP_PASSWORD = _require("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL = _require("NOTIFY_EMAIL")

THEATRE_NUMBER_RAW = _optional("THEATRE_NUMBER")
POLL_INTERVAL = int(_optional("POLL_INTERVAL_MINUTES", "15"))
PEAK_INTERVAL = int(_optional("PEAK_POLL_INTERVAL_MINUTES", "5"))
PEAK_START = _optional("PEAK_HOURS_START", "09:00")
PEAK_END = _optional("PEAK_HOURS_END", "12:00")


# ---------------------------------------------------------------------------
# Shared state (module-level, safe for single-threaded APScheduler)
# ---------------------------------------------------------------------------
client = AMCClient(API_KEY)
seen_ids: set[str] = set()
imax_code: str = ""
theatre_number: int = 0
theatre_name: str = ""


# ---------------------------------------------------------------------------
# Theatre discovery
# ---------------------------------------------------------------------------

def resolve_theatre(non_interactive: bool = False) -> tuple[int, str]:
    """
    Return (theatre_number, theatre_name).

    If THEATRE_NUMBER is set in .env, use it directly.
    Otherwise, interactively ask the user for a search term (local mode only).
    Raises SystemExit in non-interactive mode if THEATRE_NUMBER is not set.
    """
    if THEATRE_NUMBER_RAW:
        number = int(THEATRE_NUMBER_RAW)
        info = client.get_theatre(number)
        name = info.get("longName") or info.get("name", f"Theatre #{number}")
        logger.info("Using theatre: %s (id=%d)", name, number)
        return number, name

    if non_interactive:
        logger.error(
            "THEATRE_NUMBER is not set. "
            "Run locally first (python main.py) to look up your theatre, "
            "then add THEATRE_NUMBER=<id> to your GitHub Actions secrets/variables."
        )
        sys.exit(1)

    # Interactive lookup mode (local only)
    print("\nNo THEATRE_NUMBER set. Let's find your theatre.")
    while True:
        query = input("Enter a theatre name or city to search: ").strip()
        if not query:
            continue
        results = client.find_theatres(query)
        if not results:
            print("No theatres found. Try a different search term.")
            continue

        print(f"\nFound {len(results)} result(s):\n")
        for i, t in enumerate(results, start=1):
            tid = t.get("id", "?")
            tname = t.get("longName") or t.get("name", "Unknown")
            city = t.get("city", "")
            state = t.get("state", "")
            print(f"  [{i}] {tname} (id={tid})  {city}, {state}")

        choice = input("\nEnter the number of your theatre (or 0 to search again): ").strip()
        if choice == "0" or not choice.isdigit():
            continue
        idx = int(choice) - 1
        if 0 <= idx < len(results):
            chosen = results[idx]
            number = int(chosen["id"])
            name = chosen.get("longName") or chosen.get("name", f"Theatre #{number}")

            print(f"\nSelected: {name} (id={number})")
            print(f"Tip: add THEATRE_NUMBER={number} to your .env to skip this next time.\n")
            logger.info("Using theatre: %s (id=%d)", name, number)
            return number, name


# ---------------------------------------------------------------------------
# Peak-hours helper
# ---------------------------------------------------------------------------

def _is_peak_time() -> bool:
    """Return True if the current local time falls within the peak polling window."""
    now = datetime.now().strftime("%H:%M")
    return PEAK_START <= now < PEAK_END


# ---------------------------------------------------------------------------
# Poll function
# ---------------------------------------------------------------------------

def _do_poll(label: str = "poll") -> None:
    """Core polling logic: fetch showtimes, diff, notify, persist state."""
    logger.info("Polling %s (theatre=%d, %s)…", theatre_name, theatre_number, label)

    try:
        showtimes = client.get_future_imax_showtimes(theatre_number, imax_code)
    except Exception as exc:
        logger.error("API call failed: %s", exc)
        return

    logger.info("Fetched %d IMAX showtime(s).", len(showtimes))

    new = find_new_showtimes(showtimes, seen_ids)
    if new:
        logger.info("Found %d new showtime(s) — sending notification.", len(new))
        send_notification(
            gmail_user=GMAIL_USER,
            gmail_app_password=GMAIL_APP_PASSWORD,
            notify_email=NOTIFY_EMAIL,
            new_showtimes=new,
            theatre_name=theatre_name,
        )
        save_seen_ids(seen_ids)
    else:
        logger.info("No new showtimes detected.")


def poll(*, peak: bool = False) -> None:
    """
    Scheduled poll wrapper (local mode).

    The `peak` flag controls which interval this job belongs to. Each job
    skips itself when it is *not* the currently active schedule, preventing
    duplicate polls when both APScheduler jobs fire at the same time.
    """
    if peak != _is_peak_time():
        return
    _do_poll(label="peak" if peak else "normal")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global seen_ids, imax_code, theatre_number, theatre_name

    parser = argparse.ArgumentParser(description="AMC IMAX Showtime Polling Bot")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll and exit (used by GitHub Actions).",
    )
    args = parser.parse_args()

    # Load persisted state before anything else
    seen_ids = load_seen_ids()
    logger.info("Loaded %d previously seen showtime ID(s).", len(seen_ids))

    # Resolve theatre (non-interactive in --once mode)
    theatre_number, theatre_name = resolve_theatre(non_interactive=args.once)

    # Discover IMAX attribute code
    logger.info("Discovering IMAX attribute code…")
    imax_code = client.get_imax_attribute_code()

    # -----------------------------------------------------------------------
    # GitHub Actions mode: single poll then exit
    # -----------------------------------------------------------------------
    if args.once:
        logger.info("Running in single-poll mode (--once).")
        _do_poll(label="github-actions")
        return

    # -----------------------------------------------------------------------
    # Local mode: continuous scheduler
    # -----------------------------------------------------------------------
    logger.info("Running initial poll…")
    poll(peak=_is_peak_time())

    scheduler = BlockingScheduler(timezone="local")

    scheduler.add_job(
        lambda: poll(peak=False),
        trigger="interval",
        minutes=POLL_INTERVAL,
        id="normal_poll",
        name=f"Normal poll (every {POLL_INTERVAL} min)",
    )

    scheduler.add_job(
        lambda: poll(peak=True),
        trigger="interval",
        minutes=PEAK_INTERVAL,
        id="peak_poll",
        name=f"Peak poll (every {PEAK_INTERVAL} min, {PEAK_START}–{PEAK_END})",
    )

    logger.info(
        "Scheduler started. Normal: every %d min | Peak (%s–%s): every %d min. "
        "Press Ctrl+C to stop.",
        POLL_INTERVAL,
        PEAK_START,
        PEAK_END,
        PEAK_INTERVAL,
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
        save_seen_ids(seen_ids)


if __name__ == "__main__":
    main()
