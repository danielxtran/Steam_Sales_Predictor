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


class SteamSpyError(Exception):
    """A SteamSpy request failed after all retries."""


TOP_LISTS = ("top100in2weeks", "top100forever", "top100owned")


def get_top_list_app_ids() -> list[int]:
    """
    Return app IDs from SteamSpy's top-100 lists (by recent players, all-time players,
    and owners), most popular first. Raises SteamSpyError if any list can't be fetched,
    so callers never treat a partial result as complete.
    """
    app_ids: list[int] = []
    seen: set[int] = set()
    for request_type in TOP_LISTS:
        data = _get({"request": request_type})
        if data is None:
            raise SteamSpyError(f"could not fetch SteamSpy {request_type}")
        for aid in data:
            try:
                aid_int = int(aid)
            except ValueError:
                continue
            if aid_int not in seen:
                app_ids.append(aid_int)
                seen.add(aid_int)
        time.sleep(_PAGE_DELAY)
    return app_ids
