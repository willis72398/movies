"""
Persistent state management.

Stores the set of showtime IDs that have already triggered a notification,
so restarts don't produce duplicate alerts.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))


def load_seen_ids() -> set[str]:
    """Load the set of previously seen showtime IDs from disk."""
    if not STATE_FILE.exists():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text())
        return set(data.get("seen_ids", []))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read state file (%s); starting fresh.", exc)
        return set()


def save_seen_ids(seen_ids: set[str]) -> None:
    """Persist the set of seen showtime IDs to disk."""
    try:
        STATE_FILE.write_text(json.dumps({"seen_ids": sorted(seen_ids)}, indent=2))
    except OSError as exc:
        logger.error("Could not write state file: %s", exc)


def find_new_showtimes(showtimes: list[dict], seen_ids: set[str]) -> list[dict]:
    """
    Return only the showtimes whose ID has not been seen before,
    and update seen_ids in place.
    """
    new = []
    for showtime in showtimes:
        sid = str(showtime.get("id", ""))
        if not sid:
            continue
        if sid not in seen_ids:
            new.append(showtime)
            seen_ids.add(sid)
    return new
