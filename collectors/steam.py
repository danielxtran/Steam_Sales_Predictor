import logging
import time
from typing import Optional

import requests

from config import STEAM_API_KEY

logger = logging.getLogger(__name__)

_STORE_API = "https://store.steampowered.com/api"
_STEAM_API = "https://api.steampowered.com"

# Steam's store API rate limit is roughly 200 requests/5 min.
# At 1.0s delay we use ~60 req/min — well under the limit.
REQUEST_DELAY = 1.0


def _get(url: str, params: dict | None = None, retries: int = 3) -> Optional[dict]:
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                logger.warning("Rate limited by Steam, waiting %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("Request failed (attempt %d/%d): %s", attempt + 1, retries, exc)
            if attempt < retries - 1:
                time.sleep(5)
    return None


def get_top_played_app_ids() -> list[int]:
    """
    Return a seed list of popular app IDs using Steam's featured categories endpoint.
    Pulls top sellers, current specials, and new releases — no API key needed.
    """
    data = _get(f"{_STORE_API}/featuredcategories", params={"cc": "us", "l": "en"})
    if not data:
        return []

    app_ids: list[int] = []
    seen: set[int] = set()
    for category in ("top_sellers", "specials", "new_releases", "coming_soon"):
        for item in data.get(category, {}).get("items", []):
            aid = item.get("id")
            if aid and aid not in seen:
                app_ids.append(aid)
                seen.add(aid)

    logger.info("Seed: found %d app IDs from featured categories", len(app_ids))
    return app_ids


def get_all_app_ids() -> list[int]:
    """
    Return every app ID on Steam.

    With a STEAM_API_KEY uses the paginated IStoreService endpoint (games only).
    Without a key falls back to the public ISteamApps list (includes DLC, software, etc.)
    and relies on parse_game() to filter non-games at detail-fetch time.
    """
    if STEAM_API_KEY:
        app_ids: list[int] = []
        last_appid = 0
        while True:
            data = _get(
                f"{_STEAM_API}/IStoreService/GetAppList/v1/",
                params={
                    "key": STEAM_API_KEY,
                    "include_games": 1,
                    "include_dlc": 0,
                    "include_software": 0,
                    "include_videos": 0,
                    "include_hardware": 0,
                    "max_results": 50000,
                    "last_appid": last_appid,
                },
            )
            if not data:
                break
            apps = data.get("response", {}).get("apps", [])
            app_ids.extend(a["appid"] for a in apps)
            if not data.get("response", {}).get("have_more_results"):
                break
            last_appid = app_ids[-1]
        return app_ids

    data = _get(f"{_STEAM_API}/ISteamApps/GetAppList/v2/")
    if not data:
        return []
    return [a["appid"] for a in data.get("applist", {}).get("apps", [])]


def get_app_details(app_id: int) -> Optional[dict]:
    """Fetch full app details for one game. Enforces REQUEST_DELAY before each call."""
    time.sleep(REQUEST_DELAY)
    data = _get(
        f"{_STORE_API}/appdetails",
        params={"appids": app_id, "cc": "us", "l": "en"},
    )
    if not data or str(app_id) not in data:
        return None
    entry = data[str(app_id)]
    if not entry.get("success"):
        return None
    return entry.get("data")



def _compute_discount(initial_cents: int | None, final_cents: int | None) -> int:
    """Return the real discount % from raw cent values, ignoring Steam's reported figure."""
    if not initial_cents or initial_cents <= 0:
        return 0
    final = final_cents if final_cents is not None else initial_cents
    return round((1 - final / initial_cents) * 100)


def parse_game(app_id: int, raw: dict) -> Optional[dict]:
    """
    Extract structured fields from a raw appdetails response.
    Returns None for non-game types (DLC, soundtracks, demos, etc.).
    """
    if raw.get("type") != "game":
        return None

    price = raw.get("price_overview") or {}
    genres = [g["description"] for g in raw.get("genres", [])]
    devs = raw.get("developers") or []
    pubs = raw.get("publishers") or []

    return {
        "app_id": app_id,
        "title": raw.get("name"),
        "developer": devs[0] if devs else None,
        "publisher": pubs[0] if pubs else None,
        "genres": ",".join(genres),
        "release_date": raw.get("release_date", {}).get("date"),
        "metacritic_score": (raw.get("metacritic") or {}).get("score"),
        "total_reviews": (raw.get("recommendations") or {}).get("total", 0),
        "is_free": raw.get("is_free", False),
        # price_overview is absent for free games; prices are in cents
        "current_price": price["final"] / 100 if price.get("final") is not None else None,
        "original_price": price["initial"] / 100 if price.get("initial") is not None else None,
        # Compute discount from actual prices — Steam's reported discount_percent is
        # sometimes wrong (e.g. returns 100 when prices haven't changed).
        "discount_pct": _compute_discount(price.get("initial"), price.get("final")),
        "on_sale": (price.get("initial", 0) or 0) > (price.get("final", 0) or 0),
    }
