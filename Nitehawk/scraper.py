"""
Nitehawk Cinema special screenings scraper.

Fetches the "Coming Soon" page for both Nitehawk locations and extracts
only the Special Screenings section (ignoring First-Run films).

The page structure (Filmbot-powered WordPress) has two named <section> elements:
  #special-screenings  — curated series, midnite movies, Q&As, events, etc.
  #first-run           — regular new-release films (ignored by this bot)

Each screening is represented as one dict per individual purchasable showtime:
{
    "id":           "21637423",           # Filmbot showtime ID (unique)
    "title":        "Big Hero 6",
    "date":         "Sat, Mar 7",
    "time":         "11:15 am",
    "purchase_url": "https://nitehawkcinema.com/.../purchase/21637423/",
    "details_url":  "https://nitehawkcinema.com/.../movies/big-hero-6/",
    "series":       ["Brunch Movie", "FAMILY FRIENDLY"],
    "location":     "Williamsburg",
}
"""

import logging
import time

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

LOCATIONS: dict[str, str] = {
    "Williamsburg": "https://nitehawkcinema.com/williamsburg/coming-soon/",
    "Prospect Park": "https://nitehawkcinema.com/prospectpark/coming-soon-2/",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _fetch_html(url: str) -> str:
    """GET a URL with exponential-backoff retries on transient errors."""
    backoff = 5
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            if attempt == _MAX_RETRIES:
                raise
            logger.warning(
                "Request failed (attempt %d/%d): %s — retrying in %ds…",
                attempt,
                _MAX_RETRIES,
                exc,
                backoff,
            )
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError("Unreachable")


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------


def _parse_show_details(div, location: str) -> list[dict]:
    """
    Parse a single .show-details <div> and return one dict per purchasable
    showtime button found inside it.

    Returns an empty list if the block is malformed or has no purchase links.
    """
    # --- Title & details URL ---
    title_tag = div.find("h1", class_="show-title")
    if not title_tag:
        return []
    title_link = title_tag.find("a", class_="title")
    title = title_link.get_text(strip=True) if title_link else "Unknown"
    details_url = title_link.get("href", "") if title_link else ""

    # --- Build timestamp → human-readable date map ---
    #
    # Filmbot renders the date two different ways depending on how many dates
    # a screening has:
    #
    # Multi-date → a <select class="datelist"> with <option class="show-date"
    #              data-date="TIMESTAMP"> elements.
    #
    # Single-date → a <div class="selected-date single-date"> containing a
    #               plain <span> with the date text.  No <option> elements
    #               exist, so we fall back to reading that <span> and mapping
    #               every timestamp in this block to it.
    date_map: dict[str, str] = {}
    for option in div.find_all("option", class_="show-date"):
        ts = option.get("data-date", "")
        date_str = " ".join(option.get_text().split())
        if ts:
            date_map[ts] = date_str

    if not date_map:
        single_date_div = div.find("div", class_="selected-date")
        if single_date_div:
            span = single_date_div.find("span")
            if span:
                fallback_date = " ".join(span.get_text().split())
                for li in div.find_all("li", attrs={"data-date": True}):
                    ts = li.get("data-date", "")
                    if ts:
                        date_map[ts] = fallback_date

    # --- Unique series/film labels (deduplicated, order preserved) ---
    seen_labels: set[str] = set()
    series_list: list[str] = []
    for pill in div.find_all("a", class_="pill"):
        label = pill.get_text(strip=True)
        if label and label not in seen_labels:
            seen_labels.add(label)
            series_list.append(label)

    # --- One showtime dict per purchasable <li data-date="…"> entry ---
    showtimes: list[dict] = []
    for li in div.find_all("li", attrs={"data-date": True}):
        a = li.find("a", class_="showtime")
        if not a:
            # Sold-out or unavailable slot — skip (no purchase link exists)
            continue
        showtime_id = a.get("data-showtime_id", "").strip()
        if not showtime_id:
            continue

        date_ts = li.get("data-date", "")
        showtimes.append(
            {
                "id": showtime_id,
                "title": title,
                "date": date_map.get(date_ts, ""),
                "time": " ".join(a.get_text().split()),
                "purchase_url": a.get("href", ""),
                "details_url": details_url,
                "series": series_list,
                "location": location,
            }
        )

    return showtimes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_special_screenings(location: str, url: str) -> list[dict]:
    """
    Fetch the coming-soon page for one Nitehawk location and return all
    Special Screenings showtimes.  First-Run films are excluded.
    """
    logger.info("Fetching %s coming-soon page…", location)
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    section = soup.find("section", id="special-screenings")
    if not section:
        logger.warning(
            "#special-screenings section not found on %s page — "
            "the site HTML may have changed.",
            location,
        )
        return []

    all_showtimes: list[dict] = []
    for show_div in section.find_all("div", class_="show-details"):
        all_showtimes.extend(_parse_show_details(show_div, location))

    logger.info(
        "Found %d purchasable special screening showtime(s) at %s.",
        len(all_showtimes),
        location,
    )
    return all_showtimes


def fetch_all_locations() -> list[dict]:
    """
    Fetch and combine special screenings from all configured Nitehawk locations.
    Failures for one location are logged and skipped so the other can still run.
    """
    combined: list[dict] = []
    for location, url in LOCATIONS.items():
        try:
            combined.extend(fetch_special_screenings(location, url))
        except Exception as exc:
            logger.error("Failed to fetch %s: %s", location, exc)
    return combined
