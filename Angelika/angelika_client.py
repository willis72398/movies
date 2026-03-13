"""
Angelika Film Center showtime scraper.

Strategy
--------
The Angelika website is a React SPA backed by production-api.readingcinemas.com.
Session data lives in the per-film API response, NOT the initial page response.

Phase 1: Navigate to the now-playing page.
         Extract film slugs from rendered hrefs.

Phase 2: Navigate to each film's detail page.
         Intercept /films?...&flag=nowshowing&movieSlug={slug} responses.
         Each response contains:

  nowShowing.data.movies[0]
    .movieSlug, .name
    .showdates[j]
      .date
      .showtypes[k]
        .type          ← format label, e.g. "Standard", "English subtitles"
        .showtimes[l]
          .id          ← session ID
          .date_time   ← ISO datetime, e.g. "2026-03-13T15:15:00-04"
          .soldout

The initial /films response only contains a count (int) for each showtype's
.showtimes field, not the actual session list.
"""

import logging
import re
from datetime import datetime

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

THEATERS = [
    ("Angelika Film Center NYC", "nyc", "https://angelikafilmcenter.com/nyc/now-playing"),
    ("Village East by Angelika", "villageeast", "https://angelikafilmcenter.com/villageeast/now-playing"),
    ("Cinema 123 by Angelika", "cinemas123", "https://angelikafilmcenter.com/cinemas123/now-playing"),
]

API_HOST = "production-api.readingcinemas.com"
BASE_URL = "https://angelikafilmcenter.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Timezone offset suffix, e.g. "-04" or "+05:30"
_TZ_SUFFIX_RE = re.compile(r"[+-]\d{2}(?::\d{2})?$")


def _parse_showtime(iso: str) -> tuple[str, str, str]:
    """
    Parse an ISO datetime string into (date_str, time_str, datetime_str).
    e.g. '2026-03-13T15:15:00-04' → ('2026-03-13', '3:15pm', '2026-03-13T15:15:00')
    """
    try:
        clean = _TZ_SUFFIX_RE.sub("", iso).rstrip("Z")
        dt = datetime.fromisoformat(clean)
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%-I:%M") + dt.strftime("%p").lower()
        return date_str, time_str, dt.isoformat()
    except (ValueError, AttributeError):
        return iso[:10] if len(iso) >= 10 else iso, "", iso


def _showtimes_from_movie(
    movie: dict,
    theater_name: str,
    theater_slug: str,
    seen_ids: set,
) -> list[dict]:
    """Extract individual showtimes from a movie object with .showdates."""
    movie_slug = movie.get("movieSlug") or movie.get("slug") or ""
    title = movie.get("name") or "Unknown"
    film_url = f"{BASE_URL}/{theater_slug}/movies/details/{movie_slug}" if movie_slug else ""

    showtimes = []
    showdates = movie.get("showdates")
    if not isinstance(showdates, list):
        return showtimes

    for showdate in showdates:
        if not isinstance(showdate, dict):
            continue
        showtypes = showdate.get("showtypes")
        if not isinstance(showtypes, list):
            continue
        for showtype in showtypes:
            if not isinstance(showtype, dict):
                continue
            fmt = showtype.get("type") or "Standard"
            sessions_list = showtype.get("showtimes")
            if not isinstance(sessions_list, list):
                continue
            for session in sessions_list:
                if not isinstance(session, dict):
                    continue
                session_id = str(session.get("id") or "").strip()
                if not session_id or session_id in seen_ids:
                    continue
                seen_ids.add(session_id)

                date_str, time_str, datetime_str = _parse_showtime(
                    session.get("date_time") or ""
                )
                if not date_str or not time_str:
                    continue

                ticket_url = (
                    session.get("ticketUrl") or session.get("purchaseUrl")
                    or f"{BASE_URL}/{theater_slug}/sessions/{session_id}/{movie_slug}"
                )
                showtimes.append({
                    "id": session_id,
                    "title": title,
                    "show_date": date_str,
                    "show_time": time_str,
                    "show_datetime": datetime_str,
                    "format": fmt,
                    "location": theater_name,
                    "ticket_url": ticket_url,
                    "film_url": film_url,
                })
    return showtimes


def _scrape_theater(browser, theater_name: str, theater_slug: str, url: str) -> list[dict]:
    """
    Scrape one Angelika theater using a two-phase Playwright approach.
    """
    context = browser.new_context(user_agent=USER_AGENT)
    page = context.new_page()

    per_film_bodies: list[dict] = []  # bodies from /films?...&flag=nowshowing&movieSlug=...

    def on_response(response):
        if API_HOST not in response.url:
            return
        if "movieSlug=" not in response.url:
            return
        try:
            body = response.json()
            per_film_bodies.append(body)
        except Exception:
            pass

    page.on("response", on_response)

    # ------------------------------------------------------------------
    # Phase 1: now-playing page → get film slugs
    # ------------------------------------------------------------------
    try:
        page.goto(url, timeout=60_000, wait_until="networkidle")
    except Exception as exc:
        logger.warning("Phase 1 navigation warning (%s): %s", theater_name, exc)

    slug_pattern = re.compile(rf"/{theater_slug}/movies/details/([^/?#]+)")
    try:
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    except Exception:
        hrefs = []

    seen_slugs: set[str] = set()
    film_slugs: list[str] = []
    for href in hrefs:
        if not href:
            continue
        m = slug_pattern.search(href)
        if m:
            slug = m.group(1)
            if slug not in seen_slugs:
                seen_slugs.add(slug)
                film_slugs.append(slug)

    logger.info("%s: found %d film(s)", theater_name, len(film_slugs))

    # ------------------------------------------------------------------
    # Phase 2: navigate to each film detail page → capture session data
    # ------------------------------------------------------------------
    for film_slug in film_slugs:
        film_url = f"{BASE_URL}/{theater_slug}/movies/details/{film_slug}"
        try:
            page.goto(film_url, timeout=30_000, wait_until="networkidle")
        except Exception as exc:
            logger.warning("Film page navigation warning (%s / %s): %s", theater_name, film_slug, exc)

    context.close()

    logger.info("%s: captured %d per-film response(s)", theater_name, len(per_film_bodies))

    # ------------------------------------------------------------------
    # Parse sessions from per-film responses
    # ------------------------------------------------------------------
    seen_ids: set[str] = set()
    showtimes: list[dict] = []

    for body in per_film_bodies:
        if not isinstance(body, dict):
            continue
        ns = body.get("nowShowing", {})
        if not isinstance(ns, dict):
            continue
        data = ns.get("data", {})
        if not isinstance(data, dict):
            continue
        movies = data.get("movies", [])
        if not isinstance(movies, list):
            continue
        for movie in movies:
            if isinstance(movie, dict):
                showtimes.extend(
                    _showtimes_from_movie(movie, theater_name, theater_slug, seen_ids)
                )

    logger.info("%s: parsed %d showtime(s)", theater_name, len(showtimes))
    return showtimes


def fetch_showtimes() -> list[dict]:
    """Scrape all Angelika Film Center locations and return normalized showtimes."""
    all_showtimes: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for theater_name, theater_slug, url in THEATERS:
            logger.info("Scraping %s…", theater_name)
            try:
                showtimes = _scrape_theater(browser, theater_name, theater_slug, url)
                logger.info("%s: %d showtime(s)", theater_name, len(showtimes))
                all_showtimes.extend(showtimes)
            except Exception as exc:
                logger.error("Failed to scrape %s: %s", theater_name, exc)

        browser.close()

    logger.info("Scraped %d Angelika showtime(s) total.", len(all_showtimes))
    return all_showtimes
