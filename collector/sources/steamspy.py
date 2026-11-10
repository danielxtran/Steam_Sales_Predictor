import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE = "https://steamspy.com/api.php"
_PAGE_DELAY = 1.0  # SteamSpy asks for no more than 4 req/sec


def _get(params: dict, retries: int = 3) -> Optional[dict]:
    for attempt in range(retries):
        try:
            resp = requests.get(_BASE, params=params, timeout=15)
            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                logger.warning("SteamSpy rate limited, waiting %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("SteamSpy request failed (attempt %d/%d): %s", attempt + 1, retries, exc)
            if attempt < retries - 1:
                time.sleep(5)
    return None


def get_top_app_ids(pages: int = 5) -> list[int]:
    """
    Return popular app IDs from SteamSpy, sorted by recent player count.

    Each page has ~1000 games. Page 0 = most popular, page 1 = next tier, etc.
    5 pages gives ~5000 games covering essentially everything meaningful on Steam.
    """
    app_ids: list[int] = []
    seen: set[int] = set()

    # Named lists first — guaranteed to include the very top games
    for request_type in ("top100in2weeks", "top100forever", "top100owned"):
        data = _get({"request": request_type})
        if data:
            for aid in data:
                try:
                    aid_int = int(aid)
                    if aid_int not in seen:
                        app_ids.append(aid_int)
                        seen.add(aid_int)
                except ValueError:
                    pass
        time.sleep(_PAGE_DELAY)

    # Paginated "all games" sorted by 2-week player count
    for page in range(pages):
        data = _get({"request": "all", "page": page})
        if not data:
            break
        for aid in data:
            try:
                aid_int = int(aid)
                if aid_int not in seen:
                    app_ids.append(aid_int)
                    seen.add(aid_int)
            except ValueError:
                pass
        logger.info("SteamSpy page %d: %d total popular IDs so far", page, len(app_ids))
        time.sleep(_PAGE_DELAY)

    return app_ids
