"""
Angelika Film Center showtime scraper.

Strategy
--------
The Angelika website is a React SPA backed by production-api.readingcinemas.com.
Sessions are loaded lazily — they only appear when the user navigates to an
individual film's detail page. So we use a two-phase approach:

  Phase 1: Navigate to the theater's now-playing page.
           Intercept the /films API response to get film slugs and the
           JWT auth token embedded in the request headers.

  Phase 2: For each film slug, navigate to its detail page
           (/{theaterSlug}/movies/details/{movieSlug}).
           Intercept the session API responses that fire on that page.

One Playwright browser instance is reused across all three theaters and all
film navigations within a poll cycle, keeping startup costs low.
"""

import logging
import re
from datetime import datetime

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# Angelika Film Center NYC
THEATERS = [
    ("Angelika Film Center NYC", "nyc", "https://angelikafilmcenter.com/nyc/now-playing"),
]

API_HOST = "production-api.readingcinemas.com"
BASE_URL = "https://angelikafilmcenter.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _parse_showtime(iso: str) -> tuple[str, str, str]:
    """
    Parse an ISO datetime string into (date_str, time_str, datetime_str).
    e.g. '2026-03-14T19:00:00' → ('2026-03-14', '7:00pm', '2026-03-14T19:00:00')
    """
    try:
        dt = datetime.fromisoformat(iso.split("+")[0].rstrip("Z"))
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%-I:%M") + dt.strftime("%p").lower()
        return date_str, time_str, dt.isoformat()
    except (ValueError, AttributeError):
        return iso[:10] if len(iso) >= 10 else iso, "", iso


def _extract_format(session: dict) -> str:
    for key in ("attributes", "sessionAttributes", "formats"):
        attrs = session.get(key)
        if isinstance(attrs, list) and attrs:
            parts = [
                (a.get("name") or a.get("description") or "")
                if isinstance(a, dict) else str(a)
                for a in attrs
            ]
            result = ", ".join(p for p in parts if p)
            if result:
                return result
    return session.get("format") or session.get("screenType") or "Standard"


def _get_films_from_response(body: dict) -> list[dict]:
    """Extract the list of film objects from a /films API response body."""
    for section_key in ("nowShowing", "advanceTicket"):
        section = body.get(section_key, {})
        if isinstance(section, dict):
            data = section.get("data", {})
            if isinstance(data, dict):
                movies = data.get("movies", [])
                if isinstance(movies, list) and movies:
                    return movies
    return []


def _sessions_from_body(body) -> list[dict]:
    """Extract a flat list of session dicts from any API response body."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("data", "sessions", "showtimes", "sessionList"):
            val = body.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                # Sometimes data is {statusCode, data: [...]}
                inner = val.get("data") or val.get("sessions") or []
                if isinstance(inner, list):
                    return inner
    return []


def _scrape_theater(browser, theater_name: str, theater_slug: str, url: str) -> list[dict]:
    """
    Scrape one Angelika theater using a two-phase Playwright approach.
    Returns a list of normalized showtime dicts.
    """
    context = browser.new_context(user_agent=USER_AGENT)
    page = context.new_page()

    all_api_bodies: list[dict] = []   # {url, body}
    auth_header: list[str] = []       # mutable box for the JWT

    def on_request(request):
        if API_HOST in request.url and not auth_header:
            token = request.headers.get("authorization", "")
            if token:
                auth_header.append(token)

    def on_response(response):
        if API_HOST not in response.url:
            return
        try:
            body = response.json()
            all_api_bodies.append({"url": response.url, "body": body})
        except Exception:
            pass

    page.on("request", on_request)
    page.on("response", on_response)

    # ------------------------------------------------------------------
    # Phase 1: now-playing page → get film slugs
    # ------------------------------------------------------------------
    try:
        page.goto(url, timeout=60_000, wait_until="networkidle")
    except Exception as exc:
        logger.warning("Phase 1 navigation warning (%s): %s", theater_name, exc)

    # Extract film slugs from rendered hrefs
    slug_pattern = re.compile(rf"/{theater_slug}/movies/details/([^/?#]+)")
    try:
        hrefs = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href'))"
        )
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

    logger.info("%s: found %d film(s): %s", theater_name, len(film_slugs), film_slugs)

    # ------------------------------------------------------------------
    # Phase 2: navigate to each film detail page → capture session data
    # ------------------------------------------------------------------
    for film_slug in film_slugs:
        film_url = f"{BASE_URL}/{theater_slug}/movies/details/{film_slug}"
        try:
            page.goto(film_url, timeout=30_000, wait_until="networkidle")
        except Exception as exc:
            logger.warning("Film page navigation warning (%s / %s): %s", theater_name, film_slug, exc)

    logger.info(
        "%s: captured %d API response(s) total",
        theater_name, len(all_api_bodies),
    )
    # Log every captured API URL so we can see what fires on film detail pages
    for resp in all_api_bodies:
        body = resp["body"]
        keys = list(body.keys()) if isinstance(body, dict) else f"list[{len(body)}]" if isinstance(body, list) else type(body).__name__
        logger.info("API RESP: %s  top_keys=%s", resp["url"], keys)

    # Log full structure of /films calls
    for resp in all_api_bodies:
        if "/films" in resp["url"]:
            body = resp["body"]
            keys = list(body.keys()) if isinstance(body, dict) else f"list[{len(body)}]"
            logger.info("FILMS URL: %s  keys=%s", resp["url"], keys)
            if isinstance(body, dict):
                for k, v in body.items():
                    logger.info("  body[%s] type=%s", k, type(v).__name__)
                    if isinstance(v, dict):
                        inner = v.get("data", {})
                        if isinstance(inner, dict):
                            logger.info("    [%s].data keys=%s", k, list(inner.keys()))
                            for ik, iv in inner.items():
                                logger.info("      [%s].data[%s] type=%s%s", k, ik, type(iv).__name__,
                                            f" len={len(iv)}" if hasattr(iv, '__len__') else "")
                                if isinstance(iv, list) and iv and isinstance(iv[0], dict):
                                    logger.info("        [0] keys=%s", list(iv[0].keys())[:20])
                                    # Dump showdates structure one level deeper
                                    showdates = iv[0].get("showdates")
                                    if isinstance(showdates, list) and showdates:
                                        first_sd = showdates[0]
                                        logger.info("          showdates[0] type=%s%s", type(first_sd).__name__,
                                                    f" keys={list(first_sd.keys())[:20]}" if isinstance(first_sd, dict) else f" val={first_sd!r:.200}")
                                        if isinstance(first_sd, dict):
                                            showtypes = first_sd.get("showtypes")
                                            if isinstance(showtypes, list) and showtypes:
                                                first_st = showtypes[0]
                                                logger.info("            showtypes[0] type=%s%s", type(first_st).__name__,
                                                            f" keys={list(first_st.keys())[:20]}" if isinstance(first_st, dict) else f" val={first_st!r:.200}")
                                                if isinstance(first_st, dict):
                                                    sessions_inner = first_st.get("sessions") or first_st.get("times") or first_st.get("showtimes") or []
                                                    if isinstance(sessions_inner, list) and sessions_inner:
                                                        logger.info("              sessions[0]=%r", sessions_inner[0])
    context.close()

    # ------------------------------------------------------------------
    # Parse all captured API responses
    # ------------------------------------------------------------------
    films: dict[str, dict] = {}     # movieSlug → film dict
    sessions: list[dict] = []

    for resp in all_api_bodies:
        resp_url = resp["url"]
        body = resp["body"]

        if "/films" in resp_url and isinstance(body, dict):
            for film in _get_films_from_response(body):
                key = film.get("movieSlug") or film.get("slug") or film.get("name", "")
                if key:
                    films[key] = film

        if "/printsession" in resp_url or "/sessions" in resp_url:
            sessions.extend(_sessions_from_body(body))

    logger.info(
        "%s: parsed %d film(s), %d session(s)",
        theater_name, len(films), len(sessions),
    )

    # ------------------------------------------------------------------
    # Build normalized showtime dicts
    # ------------------------------------------------------------------
    showtimes: list[dict] = []

    for session in sessions:
        session_id = str(
            session.get("id") or session.get("sessionId") or ""
        ).strip()
        if not session_id:
            continue

        film_key = (
            session.get("movieSlug") or session.get("filmSlug")
            or session.get("movieId") or session.get("filmId") or ""
        )
        film = films.get(str(film_key), {})

        title = (
            film.get("name") or film.get("title")
            or session.get("movieName") or session.get("filmName")
            or "Unknown"
        )

        showtime_iso = (
            session.get("startTime") or session.get("showtime")
            or session.get("showDateTimeLocal") or ""
        )
        date_str, time_str, datetime_str = _parse_showtime(showtime_iso)
        if not date_str or not time_str:
            continue

        ticket_url = (
            session.get("ticketUrl") or session.get("purchaseUrl")
            or f"{BASE_URL}/{theater_slug}/sessions/{session_id}/{film_key}"
        )
        film_slug_val = film.get("movieSlug") or film.get("slug") or film_key
        film_url = f"{BASE_URL}/{theater_slug}/movies/details/{film_slug_val}"

        showtimes.append({
            "id": session_id,
            "title": title,
            "show_date": date_str,
            "show_time": time_str,
            "show_datetime": datetime_str,
            "format": _extract_format(session),
            "location": theater_name,
            "ticket_url": ticket_url,
            "film_url": film_url,
        })

    return showtimes


def fetch_showtimes() -> list[dict]:
    """
    Scrape all three Angelika Film Center locations and return a combined
    list of showtime dicts.
    """
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
