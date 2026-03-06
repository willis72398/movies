"""
AMC Theatres API client.

Handles authentication, theatre/attribute discovery, showtime fetching,
pagination, and rate-limit/error backoff.
"""

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.amctheatres.com"
MAX_PAGE_SIZE = 100
MAX_RETRIES = 5


class RateLimitError(Exception):
    """Raised when the API returns 429 and retry is exhausted."""


class AMCClient:
    def __init__(self, api_key: str) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-AMC-Vendor-Key": api_key,
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        """
        Issue a GET request with retry logic for 429 and 5xx responses.

        - 429: honour Retry-After header (or wait 60 s default).
        - 5xx: exponential backoff starting at 5 s.
        - Other errors: raise immediately.
        """
        url = f"{BASE_URL}{path}"
        backoff = 5

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, params=params, timeout=15)
            except requests.RequestException as exc:
                if attempt == MAX_RETRIES:
                    raise
                logger.warning("Request error (%s), retrying in %ds…", exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 60))
                logger.warning(
                    "Rate limited (429). Waiting %d s before retry %d/%d…",
                    wait,
                    attempt,
                    MAX_RETRIES,
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                if attempt == MAX_RETRIES:
                    resp.raise_for_status()
                logger.warning(
                    "Server error %d, retrying in %ds… (attempt %d/%d)",
                    resp.status_code,
                    backoff,
                    attempt,
                    MAX_RETRIES,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue

            resp.raise_for_status()
            return resp.json()

        raise RateLimitError("Exceeded maximum retries due to rate limiting.")

    def _get_all_pages(self, path: str, params: Optional[dict] = None) -> list[dict]:
        """Fetch all pages for a paginated endpoint and return the combined item list."""
        params = dict(params or {})
        params["page-size"] = MAX_PAGE_SIZE
        params["page-number"] = 1

        all_items: list[dict] = []

        while True:
            data = self._get(path, params)

            # AMC uses HAL-style _embedded wrappers; try common key names.
            embedded = data.get("_embedded", {})
            items = (
                embedded.get("showtimes")
                or embedded.get("theatres")
                or embedded.get("attributes")
                or []
            )
            all_items.extend(items)

            total_count = data.get("totalCount", len(all_items))
            if len(all_items) >= total_count:
                break

            params["page-number"] += 1

        return all_items

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_theatres(self, query: str) -> list[dict]:
        """
        Search for theatres by name or location keyword.

        Returns a list of theatre dicts with at minimum 'id' and 'name' keys.
        """
        data = self._get("/v2/theatres", params={"q": query, "page-size": 25})
        embedded = data.get("_embedded", {})
        return embedded.get("theatres", [])

    def get_theatre(self, theatre_number: int) -> dict:
        """Fetch details for a single theatre by its number."""
        return self._get(f"/v2/theatres/{theatre_number}")

    def get_imax_attribute_code(self) -> str:
        """
        Dynamically discover the IMAX attribute code from /v1/attributes.

        Searches for attributes whose name contains 'IMAX' (case-insensitive).
        Raises RuntimeError if none is found.
        """
        attributes = self._get_all_pages("/v1/attributes")
        for attr in attributes:
            if "imax" in attr.get("name", "").lower():
                code = attr.get("code") or attr.get("id")
                logger.info("Discovered IMAX attribute code: %s (%s)", code, attr.get("name"))
                return str(code)

        # If the endpoint returned nothing useful, fall back to a well-known value.
        logger.warning(
            "Could not find IMAX attribute via /v1/attributes — "
            "falling back to hardcoded code 'IMAX'. Update if needed."
        )
        return "IMAX"

    def get_future_imax_showtimes(self, theatre_number: int, imax_code: str) -> list[dict]:
        """
        Fetch all upcoming IMAX showtimes for the given theatre.

        Returns a flat list of showtime dicts.
        """
        path = f"/v2/theatres/{theatre_number}/showtimes"
        params = {
            "include-attributes": imax_code,
        }
        return self._get_all_pages(path, params)
