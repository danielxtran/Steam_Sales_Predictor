import logging
import time
from datetime import date, datetime
from typing import Optional
from urllib.parse import urlsplit

import requests

from collector.settings import STEAM_API_KEY

logger = logging.getLogger(__name__)

_STORE_API = "https://store.steampowered.com/api"
_STEAM_API = "https://api.steampowered.com"

# The store API allows roughly 200 requests per 5 minutes: one every 1.5 seconds.
STORE_REQUEST_INTERVAL = 300 / 200
# appdetails accepts up to ~200 IDs per call with filters=price_overview (500 fails);
# see scripts/appdetails_batch_experiment.py. 100 leaves a safety margin.
PRICE_BATCH_SIZE = 100
# Waits after HTTP 429 when Steam sends no Retry-After header: 30s, 60s, 120s, ...
BACKOFF_BASE = 30


class SteamError(Exception):
    """A Steam request failed after all retries."""


class RateLimiter:
    """Spaces calls at least `interval` seconds apart."""

    def __init__(self, interval: float, clock=time.monotonic, sleep=time.sleep):
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None and now - self._last < self.interval:
            self._sleep(self.interval - (now - self._last))
            now = self._clock()
        self._last = now


store_limiter = RateLimiter(STORE_REQUEST_INTERVAL)


def _backoff_seconds(resp, attempt: int) -> float:
    retry_after = resp.headers.get("Retry-After", "")
    if retry_after.isdigit():
        return int(retry_after)
    return BACKOFF_BASE * 2 ** attempt


def _request(url: str, params: dict | None = None, retries: int = 4,
             limiter: RateLimiter | None = None) -> dict:
    """
    GET a Steam endpoint and return its JSON, retrying on errors and backing off on 429.
    Raises SteamError when every attempt fails. Log messages never include query
    parameters, so the API key can't leak into logs.
    """
    where = urlsplit(url).path
    problem = "no attempts made"
    for attempt in range(retries):
        if limiter:
            limiter.wait()
        try:
            resp = requests.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            problem = type(exc).__name__
            logger.warning("%s failed (attempt %d/%d): %s", where, attempt + 1, retries, problem)
            if attempt < retries - 1:
                time.sleep(5)
            continue
        if resp.status_code == 429:
            problem = "rate limited (HTTP 429)"
            if attempt == retries - 1:
                logger.warning("%s rate limited (attempt %d/%d), giving up", where, attempt + 1, retries)
                continue
            wait = _backoff_seconds(resp, attempt)
            logger.warning("%s rate limited (attempt %d/%d), waiting %ds", where, attempt + 1, retries, wait)
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            problem = f"HTTP {resp.status_code}"
            logger.warning("%s returned %s (attempt %d/%d)", where, problem, attempt + 1, retries)
            if attempt < retries - 1:
                time.sleep(5)
            continue
        try:
            return resp.json()
        except ValueError:
            problem = "invalid JSON"
            logger.warning("%s returned invalid JSON (attempt %d/%d)", where, attempt + 1, retries)
    raise SteamError(f"{where}: {problem}")


def get_featured_app_ids() -> list[int]:
    """
    Return app IDs from Steam's featured categories: top sellers, specials,
    new releases, and coming soon. No API key needed. Raises SteamError on failure.
    """
    data = _request(f"{_STORE_API}/featuredcategories", params={"cc": "us", "l": "en"},
                    limiter=store_limiter)

    app_ids: list[int] = []
    seen: set[int] = set()
    for category in ("top_sellers", "specials", "new_releases", "coming_soon"):
        for item in data.get(category, {}).get("items", []):
            aid = item.get("id")
            if aid and aid not in seen:
                app_ids.append(aid)
                seen.add(aid)
    return app_ids


def get_app_list() -> list[dict]:
    """
    Return every game on Steam as {appid, name} dicts, using the paginated
    IStoreService/GetAppList endpoint. Requires STEAM_API_KEY. Raises SteamError on failure,
    so a partial list is never mistaken for the full catalog.
    """
    if not STEAM_API_KEY:
        raise SteamError("STEAM_API_KEY is not set; it is required for IStoreService/GetAppList")
    apps: list[dict] = []
    last_appid = 0
    while True:
        data = _request(
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
        response = data.get("response", {})
        apps.extend({"appid": a["appid"], "name": a.get("name")} for a in response.get("apps", []))
        if not response.get("have_more_results"):
            return apps
        last_appid = response.get("last_appid") or apps[-1]["appid"]


def parse_price(entry: dict | None) -> Optional[dict]:
    """Return {initial_cents, final_cents, discount_pct} from a price_overview, or None if absent."""
    if not entry:
        return None
    initial, final = entry.get("initial"), entry.get("final")
    if final is None:
        return None
    return {"initial_cents": initial, "final_cents": final, "discount_pct": _compute_discount(initial, final)}


def get_prices(app_ids: list[int]) -> dict[int, Optional[dict]]:
    """
    Fetch current US prices for up to PRICE_BATCH_SIZE games in one call.

    Returns {app_id: price or None}, where None means the game has no price (free, or
    not sold). Games Steam reports as unavailable (success=false) are left out.
    Raises SteamError if the request fails.
    """
    if len(app_ids) > PRICE_BATCH_SIZE:
        raise ValueError(f"at most {PRICE_BATCH_SIZE} app IDs per call")
    data = _request(
        f"{_STORE_API}/appdetails",
        params={"appids": ",".join(map(str, app_ids)), "filters": "price_overview", "cc": "us"},
        limiter=store_limiter,
    )
    prices: dict[int, Optional[dict]] = {}
    for key, entry in data.items():
        if not entry.get("success"):
            continue
        body = entry.get("data")
        prices[int(key)] = parse_price(body.get("price_overview")) if isinstance(body, dict) else None
    return prices


def get_app_details(app_id: int) -> Optional[dict]:
    """
    Fetch full app details for one game, or None if Steam reports it unavailable.
    Raises SteamError if the request fails.
    """
    data = _request(
        f"{_STORE_API}/appdetails",
        params={"appids": app_id, "cc": "us", "l": "en"},
        limiter=store_limiter,
    )
    entry = data.get(str(app_id)) or {}
    if not entry.get("success"):
        return None
    return entry.get("data")


def _compute_discount(initial_cents: int | None, final_cents: int | None) -> int:
    """Return the real discount % from raw cent values, ignoring Steam's reported figure."""
    if not initial_cents or initial_cents <= 0:
        return 0
    final = final_cents if final_cents is not None else initial_cents
    return round((1 - final / initial_cents) * 100)


_RELEASE_DATE_FORMATS = ("%b %d, %Y", "%d %b, %Y", "%B %d, %Y", "%d %B, %Y")


def parse_release_date(text: str | None) -> Optional[date]:
    """
    Parse Steam's release date text ("Oct 21, 2020", "21 Oct, 2020") into a date.
    Returns None for anything without an exact day ("Q1 2027", "August 2026", "Coming soon").
    """
    if not text:
        return None
    for fmt in _RELEASE_DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


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
        "genres": genres,
        "release_date": parse_release_date(raw.get("release_date", {}).get("date")),
        "metacritic_score": (raw.get("metacritic") or {}).get("score"),
        "total_reviews": (raw.get("recommendations") or {}).get("total", 0),
        "is_free": raw.get("is_free", False),
        "type": raw.get("type"),
        # price_overview is absent for free games; Steam reports prices in cents
        "final_cents": price.get("final"),
        "initial_cents": price.get("initial"),
        # Compute discount from actual prices — Steam's reported discount_percent is
        # sometimes wrong (e.g. returns 100 when prices haven't changed).
        "discount_pct": _compute_discount(price.get("initial"), price.get("final")),
        "on_sale": (price.get("initial", 0) or 0) > (price.get("final", 0) or 0),
    }
