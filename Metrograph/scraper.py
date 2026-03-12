"""
Metrograph NYC showtime scraper.

The Metrograph website is a standard WordPress site with no JavaScript
rendering or bot protection — a single HTTP request returns all upcoming
showtimes for the next ~3 weeks.

Page structure
--------------
div.showtimes-grid
  div.calendar-list-day movies-grid  (id="calendar-list-day-YYYY-MM-DD")
    div.item.film-thumbnail           (one per film)
      h4 > a.title                   → movie title + film page URL
      div.film-metadata              → "Director / Year / Runtime / Format"
      div.showtimes
        a[href=ticket_url]           → time text + ticket URL
                                       ticket URL contains txtSessionId=NNNN
"""

import logging
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

URL = "https://metrograph.com/nyc/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
MAX_RETRIES = 3


def _parse_datetime(date_str: str, time_text: str) -> str:
    """Combine '2026-03-12' + '4:00pm' → '2026-03-12T16:00:00'."""
    try:
        dt = datetime.strptime(f"{date_str} {time_text.strip()}", "%Y-%m-%d %I:%M%p")
        return dt.isoformat()
    except ValueError:
        return f"{date_str}T00:00:00"


def _parse_metadata(text: str) -> dict:
    """
    Parse 'Wim Wenders / 1984 / 145min / DCP' into component fields.
    All parts are optional — returns empty strings for missing ones.
    """
    parts = [p.strip() for p in text.split("/")]
    return {
        "director": parts[0] if len(parts) > 0 else "",
        "year":     parts[1] if len(parts) > 1 else "",
        "runtime":  parts[2] if len(parts) > 2 else "",
        "format":   parts[3] if len(parts) > 3 else "",
    }


def fetch_showtimes() -> list[dict]:
    """
    Fetch and parse all upcoming Metrograph showtimes.

    Returns a list of showtime dicts with keys:
        id, title, show_date, show_time, show_datetime,
        director, year, runtime, format,
        film_url, ticket_url
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                URL,
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                logger.error("Failed to fetch Metrograph page: %s", exc)
                return []
            logger.warning("Fetch error (%s) — retrying in %ds…", exc, 2 ** attempt)
            time.sleep(2 ** attempt)

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict] = []

    for day_div in soup.find_all("div", class_="calendar-list-day movies-grid"):
        date_str = day_div["id"].replace("calendar-list-day-", "")

        for film_div in day_div.select("div.item.film-thumbnail"):
            title_tag = film_div.find("a", class_="title")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            film_href = title_tag.get("href", "")
            film_url = (
                f"https://metrograph.com{film_href}"
                if film_href.startswith("/")
                else film_href
            )

            meta_div = film_div.find("div", class_="film-metadata")
            meta = _parse_metadata(meta_div.get_text(strip=True) if meta_div else "")

            showtimes_div = film_div.find("div", class_="showtimes")
            if not showtimes_div:
                continue

            for a in showtimes_div.find_all("a", href=True):
                ticket_url = a["href"]
                m = re.search(r"txtSessionId=(\d+)", ticket_url)
                if not m:
                    continue

                session_id = m.group(1)
                time_text = a.get_text(strip=True)

                results.append(
                    {
                        "id": session_id,
                        "title": title,
                        "show_date": date_str,
                        "show_time": time_text,
                        "show_datetime": _parse_datetime(date_str, time_text),
                        "director": meta["director"],
                        "year": meta["year"],
                        "runtime": meta["runtime"],
                        "format": meta["format"],
                        "film_url": film_url,
                        "ticket_url": ticket_url,
                    }
                )

    logger.info("Scraped %d Metrograph showtime(s).", len(results))
    return results
