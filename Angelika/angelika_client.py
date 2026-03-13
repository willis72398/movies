"""
Angelika Film Center showtime scraper.

Strategy
--------
The Angelika website is a React SPA backed by production-api.readingcinemas.com.
All session data is embedded in the initial /films API response under:

  advanceTicket.data.advSessions[i]
    .movieSlug, .name
    .showdates[j]
      .date
      .showtypes[k]
        .type          ← format label, e.g. "Standard", "English subtitles"
        .showtimes[l]
          .id          ← session ID
          .date_time   ← ISO datetime, e.g. "2026-03-13T15:15:00-04"
          .soldout

We navigate to the now-playing page once with Playwright (to behave like a
real browser and pick up the Cloudflare-protected API responses), intercept
the /films response, and extract showtimes directly from it.
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

# Timezone offset suffix, e.g. "-04" or "+05:30"
_TZ_SUFFIX_RE = re.compile(r"[+-]\d{2}(?::\d{2})?$")


def _parse_showtime(iso: str) -> tuple[str, str, str]:
    """
    Parse an ISO datetime string into (date_str, time_str, datetime_str).
    e.g. '2026-03-13T15:15:00-04' → ('2026-03-13', '3:15pm', '2026-03-13T15:15:00')
    """
    try:
        # Strip timezone suffix and any trailing Z so fromisoformat is portable
        clean = _TZ_SUFFIX_RE.sub("", iso).rstrip("Z")
        dt = datetime.fromisoformat(clean)
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%-I:%M") + dt.strftime("%p").lower()
        return date_str, time_str, dt.isoformat()
    except (ValueError, AttributeError):
        return iso[:10] if len(iso) >= 10 else iso, "", iso


def _showtimes_from_adv_sessions(
    adv_sessions: list,
    theater_name: str,
    theater_slug: str,
    seen_ids: set,
) -> list[dict]:
    """Flatten advSessions / cmgSessions list into normalized showtime dicts."""
    showtimes = []
    n_films = n_showdates = n_showtypes = n_sessions_raw = 0
    for film in adv_sessions:
        if not isinstance(film, dict):
            continue
        n_films += 1
        movie_slug = film.get("movieSlug") or film.get("slug") or ""
        title = film.get("name") or "Unknown"
        film_url = f"{BASE_URL}/{theater_slug}/movies/details/{movie_slug}" if movie_slug else ""

        showdates = film.get("showdates")
        if not isinstance(showdates, list):
            logger.debug("  film %s: showdates is %s, skipping", movie_slug, type(showdates).__name__)
            continue
        for showdate in showdates:
            if not isinstance(showdate, dict):
                continue
            n_showdates += 1
            showtypes = showdate.get("showtypes")
            if not isinstance(showtypes, list):
                logger.debug("  showdate %s: showtypes is %s, skipping", showdate.get("date"), type(showtypes).__name__)
                continue
            for showtype in showtypes:
                if not isinstance(showtype, dict):
                    continue
                n_showtypes += 1
                fmt = showtype.get("type") or "Standard"
                sessions_list_inner = showtype.get("showtimes")
                logger.info("  showtype #%d: fmt=%r showtimes type=%s val=%r",
                            n_showtypes, fmt, type(sessions_list_inner).__name__,
                            sessions_list_inner if not isinstance(sessions_list_inner, list)
                            else f"list[{len(sessions_list_inner)}] first={sessions_list_inner[0] if sessions_list_inner else 'empty'}")
                if not isinstance(sessions_list_inner, list):
                    logger.debug("  showtype %s: showtimes is %s, skipping", fmt, type(sessions_list_inner).__name__)
                    continue
                for session in sessions_list_inner:
                    n_sessions_raw += 1
                    if not isinstance(session, dict):
                        continue
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
    logger.info("  parse counts: films=%d showdates=%d showtypes=%d sessions_raw=%d → kept=%d",
                n_films, n_showdates, n_showtypes, n_sessions_raw, len(showtimes))
    return showtimes


def _scrape_theater(browser, theater_name: str, theater_slug: str, url: str) -> list[dict]:
    """
    Scrape one Angelika theater.
    """
    context = browser.new_context(user_agent=USER_AGENT)
    page = context.new_page()

    all_api_responses: list[dict] = []  # {url, body}

    def on_response(response):
        if API_HOST not in response.url:
            return
        try:
            body = response.json()
            all_api_responses.append({"url": response.url, "body": body})
        except Exception:
            pass

    page.on("response", on_response)

    # Phase 1: now-playing page
    try:
        page.goto(url, timeout=60_000, wait_until="networkidle")
    except Exception as exc:
        logger.warning("Phase 1 navigation warning (%s): %s", theater_name, exc)

    # Extract one film slug to navigate to its detail page
    slug_pattern = re.compile(rf"/{theater_slug}/movies/details/([^/?#]+)")
    try:
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    except Exception:
        hrefs = []
    first_slug = next(
        (m.group(1) for h in hrefs if h and (m := slug_pattern.search(h))),
        None
    )

    # Phase 2: navigate to first film detail page to trigger full session data
    if first_slug:
        film_url = f"{BASE_URL}/{theater_slug}/movies/details/{first_slug}"
        try:
            page.goto(film_url, timeout=30_000, wait_until="networkidle")
        except Exception as exc:
            logger.warning("Phase 2 navigation warning (%s): %s", theater_name, exc)

    context.close()

    # Log ALL captured endpoints and their top-level keys
    logger.info("%s: captured %d API response(s)", theater_name, len(all_api_responses))
    for resp in all_api_responses:
        body = resp["body"]
        keys = list(body.keys()) if isinstance(body, dict) else f"list[{len(body)}]" if isinstance(body, list) else type(body).__name__
        logger.info("  %s  keys=%s", resp["url"].split(API_HOST)[1][:80], keys)

    # Log advSessions showtimes type from each /films response
    for resp in all_api_responses:
        if "/films" not in resp["url"]:
            continue
        body = resp["body"]
        if not isinstance(body, dict):
            continue
        adv = body.get("advanceTicket", {})
        data = adv.get("data", {}) if isinstance(adv, dict) else {}
        adv_sess = data.get("advSessions") if isinstance(data, dict) else None
        if isinstance(adv_sess, list) and adv_sess and isinstance(adv_sess[0], dict):
            sd_list = adv_sess[0].get("showdates")
            if isinstance(sd_list, list) and sd_list:
                st_list = sd_list[0].get("showtypes") if isinstance(sd_list[0], dict) else None
                if isinstance(st_list, list) and st_list:
                    first_st = st_list[0]
                    st_val = first_st.get("showtimes") if isinstance(first_st, dict) else None
                    logger.info("  advSessions[0].showdates[0].showtypes[0].showtimes type=%s val=%r",
                                type(st_val).__name__,
                                st_val if not isinstance(st_val, list) else f"list[{len(st_val)}]")

    # Log full keys of nowShowing.data.movies[0] from per-film responses
    for resp in all_api_responses:
        if "movieSlug" in resp["url"] and "/films" in resp["url"]:
            body = resp["body"]
            if isinstance(body, dict):
                ns = body.get("nowShowing", {})
                data = ns.get("data", {}) if isinstance(ns, dict) else {}
                movies = data.get("movies", []) if isinstance(data, dict) else []
                if isinstance(movies, list) and movies and isinstance(movies[0], dict):
                    logger.info("  per-film movies[0] ALL keys=%s", list(movies[0].keys()))

    seen_ids: set[str] = set()
    showtimes: list[dict] = []

    for body in [r["body"] for r in all_api_responses]:
        if not isinstance(body, dict):
            continue
        adv = body.get("advanceTicket", {})
        if not isinstance(adv, dict):
            continue
        data = adv.get("data", {})
        if not isinstance(data, dict):
            continue
        for key in ("advSessions", "cmgSessions"):
            sessions_list = data.get(key)
            if isinstance(sessions_list, list):
                showtimes.extend(
                    _showtimes_from_adv_sessions(
                        sessions_list, theater_name, theater_slug, seen_ids
                    )
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
