"""
Angelika Film Center showtime scraper.

Strategy
--------
The Angelika website is a React SPA backed by production-api.readingcinemas.com.
Raw HTTP requests return an empty shell — all content is JS-rendered.

We use Playwright to navigate each theater's now-playing page and intercept
the JSON responses from the Reading Cinemas API before they hit the renderer.
This gives us clean structured data without any DOM parsing.

One browser instance is shared across all three theaters per poll cycle to
amortize the browser startup cost.
"""

import logging
from datetime import datetime

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# The three Angelika Film Center locations
THEATERS = [
    ("Angelika Film Center NYC",    "https://angelikafilmcenter.com/nyc/now-playing"),
    ("Angelika Film Center Mosaic", "https://angelikafilmcenter.com/mosaic/now-playing"),
    ("Angelika Film Center Dallas", "https://angelikafilmcenter.com/dallas/now-playing"),
]

_THEATER_SLUGS = {
    "Angelika Film Center NYC":    "nyc",
    "Angelika Film Center Mosaic": "mosaic",
    "Angelika Film Center Dallas": "dallas",
}

API_HOST = "production-api.readingcinemas.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _parse_showtime(iso: str) -> tuple[str, str, str]:
    """
    Parse an ISO datetime string from the API into (date_str, time_str, datetime_str).
    e.g. '2026-03-14T19:00:00' → ('2026-03-14', '7:00pm', '2026-03-14T19:00:00')
    """
    try:
        # Strip timezone offset if present so fromisoformat works on all Python versions
        dt = datetime.fromisoformat(iso.split("+")[0].split("Z")[0])
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%-I:%M") + dt.strftime("%p").lower()
        return date_str, time_str, dt.isoformat()
    except (ValueError, AttributeError):
        return iso[:10] if len(iso) >= 10 else iso, "", iso


def _build_ticket_url(theater_name: str, session_id: str, film_id: str) -> str:
    slug = _THEATER_SLUGS.get(theater_name, "nyc")
    return f"https://angelikafilmcenter.com/{slug}/sessions/{session_id}/{film_id}"


def _build_film_url(theater_name: str, film: dict, film_id: str) -> str:
    slug = _THEATER_SLUGS.get(theater_name, "nyc")
    film_slug = film.get("slug") or film.get("urlSlug") or film_id
    return f"https://angelikafilmcenter.com/{slug}/films/{film_slug}"


def _extract_format(session: dict) -> str:
    """Pull a human-readable format string from a session object."""
    # Try various field names the API might use
    for key in ("attributes", "sessionAttributes", "formats"):
        attrs = session.get(key)
        if isinstance(attrs, list) and attrs:
            parts = []
            for a in attrs:
                if isinstance(a, dict):
                    parts.append(a.get("name") or a.get("description") or "")
                elif isinstance(a, str):
                    parts.append(a)
            result = ", ".join(p for p in parts if p)
            if result:
                return result
    return session.get("format") or session.get("screenType") or "Standard"


def _scrape_theater(browser, theater_name: str, url: str) -> list[dict]:
    """
    Open a new browser context, navigate to the theater's now-playing page,
    intercept all API JSON responses, and return normalized showtime dicts.
    """
    context = browser.new_context(user_agent=USER_AGENT)
    page = context.new_page()

    api_responses: list[dict] = []

    def on_response(response):
        resp_url = response.url
        # Log all non-static URLs so we can see what the SPA is calling
        if not any(ext in resp_url for ext in (".js", ".css", ".png", ".ico", ".woff")):
            logger.debug("RESPONSE %d  %s", response.status, resp_url)
        if API_HOST not in resp_url:
            return
        try:
            body = response.json()
            # Log top-level structure so we can understand the shape
            if isinstance(body, dict):
                logger.info("API response %s → keys: %s", resp_url.split("?")[0].split("/")[-1], list(body.keys()))
            elif isinstance(body, list):
                logger.info("API response %s → list[%d]", resp_url.split("?")[0].split("/")[-1], len(body))
                if body:
                    first = body[0]
                    if isinstance(first, dict):
                        logger.info("  first item keys: %s", list(first.keys())[:15])
                        # If this looks like a film, log nested session keys
                        for session_key in ("sessions", "showtimes", "sessionList", "dates"):
                            if session_key in first and first[session_key]:
                                s0 = first[session_key]
                                if isinstance(s0, list) and s0:
                                    logger.info("  [%s] first session keys: %s", session_key, list(s0[0].keys())[:15] if isinstance(s0[0], dict) else s0[0])
            # If films response, log nowShowing structure
            if "films" in resp_url and isinstance(body, dict) and "nowShowing" in body:
                ns = body["nowShowing"]
                logger.info("  nowShowing: list[%d]", len(ns) if isinstance(ns, list) else -1)
                if isinstance(ns, list) and ns:
                    f0 = ns[0]
                    logger.info("  nowShowing[0] keys: %s", list(f0.keys())[:20] if isinstance(f0, dict) else f0)
                    # Look for sessions inside
                    for session_key in ("sessions", "showtimes", "sessionList", "dates", "times"):
                        if isinstance(f0, dict) and session_key in f0:
                            sv = f0[session_key]
                            logger.info("  [%s] type=%s len=%s", session_key, type(sv).__name__, len(sv) if hasattr(sv, '__len__') else "?")
                            if isinstance(sv, list) and sv and isinstance(sv[0], dict):
                                logger.info("  [%s][0] keys: %s", session_key, list(sv[0].keys())[:15])
            api_responses.append({"url": resp_url, "body": body})
        except Exception as exc:
            logger.warning("Could not parse API response as JSON (%s): %s", resp_url, exc)

    page.on("response", on_response)

    try:
        page.goto(url, timeout=60_000, wait_until="networkidle")
    except Exception as exc:
        logger.warning("Navigation warning for %s: %s", theater_name, exc)

    logger.info("%s: captured %d API response(s)", theater_name, len(api_responses))
    context.close()

    # ------------------------------------------------------------------
    # Parse captured API responses into films and sessions
    # ------------------------------------------------------------------
    films: dict[str, dict] = {}
    sessions: list[dict] = []

    for resp in api_responses:
        resp_url = resp["url"]
        body = resp["body"]

        # Films endpoint — body is a list or wrapped in data/films key
        if "/films" in resp_url:
            film_list = (
                body
                if isinstance(body, list)
                else body.get("data") or body.get("films") or []
            )
            for film in film_list if isinstance(film_list, list) else []:
                fid = str(film.get("id") or film.get("filmId") or "")
                if fid:
                    films[fid] = film

        # Sessions endpoint
        if "/printsession" in resp_url or "/sessions" in resp_url:
            session_list = (
                body
                if isinstance(body, list)
                else body.get("data") or body.get("sessions") or []
            )
            if isinstance(session_list, list):
                sessions.extend(session_list)

    logger.debug(
        "%s: captured %d film(s) and %d session(s) from API",
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

        film_id = str(
            session.get("filmId") or session.get("movieId") or ""
        ).strip()
        film = films.get(film_id, {})

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

        # Prefer an explicit ticket URL from the API; fall back to SPA route
        ticket_url = (
            session.get("ticketUrl") or session.get("purchaseUrl")
            or _build_ticket_url(theater_name, session_id, film_id)
        )
        film_url = _build_film_url(theater_name, film, film_id)

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

        for theater_name, url in THEATERS:
            logger.info("Scraping %s…", theater_name)
            try:
                showtimes = _scrape_theater(browser, theater_name, url)
                logger.info("%s: %d showtime(s)", theater_name, len(showtimes))
                all_showtimes.extend(showtimes)
            except Exception as exc:
                logger.error("Failed to scrape %s: %s", theater_name, exc)

        browser.close()

    logger.info("Scraped %d Angelika showtime(s) total.", len(all_showtimes))
    return all_showtimes
