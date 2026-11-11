import logging

import pytest
import requests

from collector.sources import steam
from tests.conftest import FakeResponse, load_fixture


def test_get_prices_parses_batch(steam_http):
    steam_http.responses.append(FakeResponse(200, load_fixture("steam_prices_batch.json")))

    prices = steam.get_prices([620, 570, 400, 730, 1])

    assert prices == {
        620: {"initial_cents": 999, "final_cents": 999, "discount_pct": 0},
        570: None,   # free: success with empty data
        400: {"initial_cents": 999, "final_cents": 999, "discount_pct": 0},
        730: None,
    }                # 1 is left out: success=false
    url, params = steam_http.calls[0]
    assert params == {"appids": "620,570,400,730,1", "filters": "price_overview", "cc": "us"}


def test_get_prices_rejects_oversized_batch():
    with pytest.raises(ValueError):
        steam.get_prices(list(range(steam.PRICE_BATCH_SIZE + 1)))


def test_429_waits_for_retry_after_then_succeeds(steam_http):
    steam_http.responses += [
        FakeResponse(429, headers={"Retry-After": "7"}),
        FakeResponse(200, load_fixture("steam_prices_batch.json")),
    ]

    assert 620 in steam.get_prices([620])
    assert steam_http.sleeps == [7]


def test_429_without_header_backs_off_exponentially(steam_http):
    steam_http.responses += [
        FakeResponse(429),
        FakeResponse(429),
        FakeResponse(200, load_fixture("steam_prices_batch.json")),
    ]

    steam.get_prices([620])

    assert steam_http.sleeps == [30, 60]


def test_persistent_429_raises_after_retries(steam_http):
    steam_http.responses += [FakeResponse(429) for _ in range(4)]

    with pytest.raises(steam.SteamError, match="429"):
        steam.get_prices([620])
    assert steam_http.sleeps == [30, 60, 120]   # no pointless wait after the last attempt
    assert len(steam_http.calls) == 4


def test_errors_never_log_the_api_key(steam_http, monkeypatch, caplog):
    monkeypatch.setattr(steam, "STEAM_API_KEY", "SECRET-KEY-123")
    steam_http.responses += [
        requests.ConnectionError("failed for url ...?key=SECRET-KEY-123"),
        FakeResponse(500),
        FakeResponse(500),
        FakeResponse(500),
    ]

    with caplog.at_level(logging.WARNING), pytest.raises(steam.SteamError) as err:
        steam.get_app_list()

    assert "SECRET-KEY-123" not in caplog.text
    assert "SECRET-KEY-123" not in str(err.value)


def test_get_app_list_follows_pages(steam_http, monkeypatch):
    monkeypatch.setattr(steam, "STEAM_API_KEY", "k")
    steam_http.responses += [
        FakeResponse(200, load_fixture("steam_applist_page1.json")),
        FakeResponse(200, load_fixture("steam_applist_page2.json")),
    ]

    apps = steam.get_app_list()

    assert apps == [
        {"appid": 10, "name": "Counter-Strike"},
        {"appid": 20, "name": "Team Fortress Classic"},
        {"appid": 620, "name": "Portal 2"},
    ]
    assert steam_http.calls[1][1]["last_appid"] == 20


def test_get_app_list_requires_key(monkeypatch):
    monkeypatch.setattr(steam, "STEAM_API_KEY", "")

    with pytest.raises(steam.SteamError, match="STEAM_API_KEY"):
        steam.get_app_list()


def test_rate_limiter_spaces_calls():
    now = [100.0]
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    limiter = steam.RateLimiter(1.5, clock=lambda: now[0], sleep=sleep)
    limiter.wait()          # first call: no wait
    now[0] += 0.5
    limiter.wait()          # 0.5s later: wait the remaining 1.0s
    now[0] += 2.0
    limiter.wait()          # 2s later: no wait

    assert slept == [1.0]
