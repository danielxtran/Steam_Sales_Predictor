import logging
from datetime import datetime
from typing import Optional

import requests

from collector.settings import ITAD_API_KEY
from collector.sources.steam import RateLimiter

logger = logging.getLogger(__name__)

_BASE = "https://api.isthereanydeal.com"


# ITAD doesn't publish a rate limit; one request per second is a conservative pace.
itad_limiter = RateLimiter(1.0)


class ITADError(Exception):
    """An ITAD request failed (as opposed to ITAD answering "not found")."""


def _get(path: str, params: dict | None = None) -> Optional[dict | list]:
    """
    GET an ITAD endpoint and return its JSON. Raises ITADError on any failure.
    Log and error messages never include the URL's query string (the API key).
    """
    if not ITAD_API_KEY:
        raise ITADError("ITAD_API_KEY is not set")
    itad_limiter.wait()
    try:
        resp = requests.get(
            f"{_BASE}{path}",
            params={"key": ITAD_API_KEY, **(params or {})},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise ITADError(f"{path}: {type(exc).__name__}") from None
    if resp.status_code == 429:
        raise ITADError(f"{path}: rate limited (HTTP 429)")
    if resp.status_code >= 400:
        raise ITADError(f"{path}: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError:
        raise ITADError(f"{path}: invalid JSON") from None


def lookup_game(steam_app_id: int) -> Optional[dict]:
    """
    Resolve a Steam app ID to an ITAD game record {id, slug, title}, or None if ITAD
    doesn't know the game. Raises ITADError if the request fails.
    """
    data = _get("/games/lookup/v1", {"appid": steam_app_id})
    if not data or not data.get("found"):
        return None
    return data.get("game")


def _amount_cents(price: dict | None) -> Optional[int]:
    """Read an ITAD price object ({amount, amountInt, currency}) as integer cents."""
    if not price:
        return None
    if price.get("amountInt") is not None:
        return int(price["amountInt"])
    if price.get("amount") is not None:
        return round(price["amount"] * 100)
    return None


def get_price_history(itad_id: str, country: str = "US", since: str = "2012-01-01T00:00:00Z") -> list[dict]:
    """
    Fetch all historical price points for a game across every store ITAD tracks.

    Returns a flat list of dicts: {shop, price_cents, regular_cents, discount_pct, recorded_at}.
    Each entry is one price event: {timestamp, shop: {id, name}, deal: {price, regular, cut}}.
    The default `since` goes back to 2012 to capture all Steam sale history.
    Without this parameter the API only returns the last ~3 entries.
    """
    data = _get("/games/history/v2", {"id": itad_id, "country": country, "since": since})
    if not data:
        return []

    results: list[dict] = []
    for entry in data:
        raw_ts = entry.get("timestamp")
        recorded_at: Optional[datetime] = None
        if raw_ts:
            try:
                recorded_at = datetime.fromisoformat(raw_ts)
            except ValueError:
                pass

        deal = entry.get("deal") or {}
        results.append({
            "shop": (entry.get("shop") or {}).get("name", "unknown"),
            "price_cents": _amount_cents(deal.get("price")),
            "regular_cents": _amount_cents(deal.get("regular")),
            "discount_pct": deal.get("cut", 0),
            "recorded_at": recorded_at,
        })
    return results
