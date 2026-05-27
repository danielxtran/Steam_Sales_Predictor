import logging
from datetime import datetime
from typing import Optional

import requests

from config import ITAD_API_KEY

logger = logging.getLogger(__name__)

_BASE = "https://api.isthereanydeal.com"


def _get(path: str, params: dict | None = None) -> Optional[dict | list]:
    if not ITAD_API_KEY:
        logger.debug("ITAD_API_KEY not set — skipping ITAD fetch")
        return None
    try:
        resp = requests.get(
            f"{_BASE}{path}",
            params={"key": ITAD_API_KEY, **(params or {})},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("ITAD request failed: %s", exc)
        return None


def _post(path: str, body: list, params: dict | None = None) -> Optional[list]:
    if not ITAD_API_KEY:
        return None
    try:
        resp = requests.post(
            f"{_BASE}{path}",
            json=body,
            params={"key": ITAD_API_KEY, **(params or {})},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("ITAD request failed: %s", exc)
        return None


def lookup_game(steam_app_id: int) -> Optional[dict]:
    """Resolve a Steam app ID to an ITAD game record {id, slug, title}."""
    data = _get("/games/lookup/v1", {"appid": steam_app_id})
    if not data or not data.get("found"):
        return None
    return data.get("game")


def get_price_history(itad_id: str, country: str = "US", since: str = "2012-01-01T00:00:00Z") -> list[dict]:
    """
    Fetch all historical price points for a game across every store ITAD tracks.

    Returns a flat list of dicts: {shop, price, discount_pct, recorded_at}.
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
            "price": (deal.get("price") or {}).get("amount"),
            "discount_pct": deal.get("cut", 0),
            "recorded_at": recorded_at,
        })
    return results
