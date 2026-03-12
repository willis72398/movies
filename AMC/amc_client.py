"""
AMC Lincoln Square 13 IMAX showtime scraper.

Strategy
--------
1. Launch headless Chromium (Playwright) once per polling cycle to pass
   Cloudflare + Queue-it and capture valid session cookies.
2. Reuse those cookies with a lightweight requests.Session to fetch the
   showtimes page for every date in the look-ahead window.
3. Parse the rendered HTML with BeautifulSoup to extract IMAX showtimes.

This means one slow browser launch per poll, followed by many fast HTTP
requests — no vendor API key or registration required.
"""

import logging
import re
import time
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

THEATRE_SLUG = "new-york-city/amc-lincoln-square-13"
THEATRE_NAME = "AMC Lincoln Square 13"
SHOWTIMES_URL = f"https://www.amctheatres.com/movie-theatres/{THEATRE_SLUG}/showtimes"

DAYS_AHEAD = 30
MAX_RETRIES = 3
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class AMCClient:
    """
    Scrapes IMAX showtimes at AMC Lincoln Square 13.
    """

    def __init__(self) -> None:
        self._session: requests.Session | None = None

    # ------------------------------------------------------------------
    # Cookie / session management
    # ------------------------------------------------------------------

    def _build_session(self) -> requests.Session:
        """
        Use headless Chromium to pass Cloudflare + Queue-it and return a
        requests.Session pre-loaded with valid session cookies.
        """
        logger.info("Launching browser to obtain session cookies…")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()

            try:
                page.goto(
                    SHOWTIMES_URL,
                    timeout=60_000,
                    wait_until="domcontentloaded",
                )
            except Exception as exc:
                logger.warning("Initial navigation warning: %s", exc)

            # Wait for content to settle (Queue-it redirect dance can take a moment)
            page.wait_for_timeout(4_000)
            cookies = context.cookies()
            browser.close()

        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT
        for c in cookies:
            session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

        logger.info("Session ready with %d cookies.", len(cookies))
        return session

    def _ensure_session(self) -> None:
        if self._session is None:
            self._session = self._build_session()

    def _invalidate_session(self) -> None:
        """Drop the cached session so the next call re-runs the browser."""
        self._session = None

    # ------------------------------------------------------------------
    # HTTP fetching
    # ------------------------------------------------------------------

    def _fetch_page(self, dt: date) -> str | None:
        """
        Fetch the AMC showtimes page HTML for a given date.
        Returns None on failure; invalidates the session on redirect to Queue-it.
        """
        url = f"{SHOWTIMES_URL}?date={dt.isoformat()}"
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, timeout=30, allow_redirects=False)
            except requests.RequestException as exc:
                if attempt == MAX_RETRIES:
                    logger.error("Request error for %s: %s", dt, exc)
                    return None
                time.sleep(2 ** attempt)
                continue

            # Redirect to Queue-it means our cookies expired
            if resp.status_code in (301, 302):
                loc = resp.headers.get("Location", "")
                if "queue.amctheatres.com" in loc or "queue" in loc.lower():
                    logger.info("Queue-it redirect detected — refreshing session.")
                    self._invalidate_session()
                    self._ensure_session()
                    continue
                # Other redirect — follow it once
                resp = self._session.get(url, timeout=30)

            if resp.status_code == 200:
                return resp.text

            logger.warning("HTTP %d for %s (attempt %d)", resp.status_code, dt, attempt)
            time.sleep(2 ** attempt)

        return None

    # ------------------------------------------------------------------
    # HTML parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_time(time_text: str, page_date: date) -> str:
        """
        Convert a showtime string like '10:30pm' + a date into an ISO-8601
        local datetime string, e.g. '2026-03-12T22:30:00'.
        """
        try:
            dt = datetime.strptime(
                f"{page_date.isoformat()} {time_text.strip()}", "%Y-%m-%d %I:%M%p"
            )
            return dt.isoformat()
        except ValueError:
            return f"{page_date.isoformat()}T00:00:00"

    def _parse_imax_showtimes(self, html: str, page_date: date) -> list[dict]:
        """
        Extract all IMAX showtimes from a rendered AMC showtimes page.

        The page structure is:
          <section id="{movie-slug}">
            <h1>{Movie Title}</h1>
            ...
            <ul id="{movie-slug}-{theatre-slug}-{format}-{n}-attributes">
              <li>IMAX at AMC</li>  ← signals IMAX format group
              ...
            </ul>
            <ul aria-label="Showtime Group Results">
              <li>
                <a id="{showtime_id}" href="/showtimes/{showtime_id}">{time}</a>
              </li>
            </ul>
          </section>
        """
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict] = []

        # Every format-attribute <ul> whose id contains 'imax' signals an IMAX group
        imax_attr_uls = soup.find_all("ul", id=re.compile(r"imax", re.IGNORECASE))

        for attr_ul in imax_attr_uls:
            # Collect the human-readable attribute names for this format group
            attr_names = [li.get_text(strip=True) for li in attr_ul.find_all("li")]

            # Walk up to the movie <section> to get the title
            movie_section = attr_ul.find_parent("section")
            if not movie_section:
                continue
            h1 = movie_section.find("h1")
            movie_title = h1.get_text(strip=True) if h1 else movie_section.get("id", "Unknown")

            # The attributes <ul> lives inside a <li> that also contains
            # the showtime-group <ul aria-label="Showtime Group Results">
            parent_li = attr_ul.find_parent("li")
            if not parent_li:
                continue

            showtime_group = parent_li.find(
                "ul", attrs={"aria-label": re.compile(r"showtime group", re.IGNORECASE)}
            )
            if not showtime_group:
                continue

            for a in showtime_group.find_all("a", href=re.compile(r"^/showtimes/")):
                showtime_id = a.get("id", "").strip()
                href = a.get("href", "")
                time_text = a.get_text(strip=True)

                if not showtime_id or not href:
                    continue

                results.append(
                    {
                        "id": showtime_id,
                        "movieName": movie_title,
                        "showDateTimeLocal": self._parse_time(time_text, page_date),
                        "attributes": [{"name": n} for n in attr_names],
                        "_links": {
                            "purchase:showtime": {
                                "href": f"https://www.amctheatres.com{href}"
                            }
                        },
                    }
                )

        return results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_future_imax_showtimes(self) -> list[dict]:
        """
        Return all upcoming IMAX showtimes at AMC Lincoln Square 13 for
        the next DAYS_AHEAD calendar days.

        Showtime dicts have the keys expected by state.py and notifier.py:
        id, movieName, showDateTimeLocal, attributes, _links.
        """
        self._ensure_session()
        today = date.today()
        all_imax: list[dict] = []

        for i in range(DAYS_AHEAD):
            dt = today + timedelta(days=i)
            html = self._fetch_page(dt)
            if html is None:
                logger.warning("Skipping %s — page fetch failed.", dt)
                continue

            imax = self._parse_imax_showtimes(html, dt)
            logger.debug("%s: %d IMAX showtime(s)", dt, len(imax))
            all_imax.extend(imax)

        logger.info(
            "Scraped %d IMAX showtime(s) across the next %d days.",
            len(all_imax),
            DAYS_AHEAD,
        )
        return all_imax
