from datetime import datetime, timedelta, timezone

from collector.sources import itad
from tests.conftest import load_fixture


def _fake_get(calls):
    def fake(path, params=None):
        calls.append((path, params))
        return load_fixture("itad_history_portal2.json")
    return fake


def test_get_price_history_parses_entries(monkeypatch):
    calls = []
    monkeypatch.setattr(itad, "_get", _fake_get(calls))

    history = itad.get_price_history("game-uuid")

    assert calls == [(
        "/games/history/v2",
        {"id": "game-uuid", "country": "US", "since": "2012-01-01T00:00:00Z"},
    )]
    assert history[0] == {
        "shop": "Steam",
        "price_cents": 199,
        "regular_cents": 999,
        "discount_pct": 80,
        "recorded_at": datetime(2024, 11, 26, 18, 2, 13, tzinfo=timezone(timedelta(hours=1))),
    }
    assert history[1]["price_cents"] == 999
    assert history[1]["discount_pct"] == 0


def test_get_price_history_keeps_all_shops_for_now(monkeypatch):
    # Current behaviour: every shop is returned. Filtering to Steam is a later task.
    monkeypatch.setattr(itad, "_get", _fake_get([]))

    shops = [entry["shop"] for entry in itad.get_price_history("game-uuid")]

    assert shops == ["Steam", "Steam", "GOG", "unknown"]


def test_get_price_history_handles_bad_entry(monkeypatch):
    monkeypatch.setattr(itad, "_get", _fake_get([]))

    bad = itad.get_price_history("game-uuid")[-1]

    assert bad == {
        "shop": "unknown", "price_cents": None, "regular_cents": None,
        "discount_pct": 0, "recorded_at": None,
    }


def test_get_price_history_empty_response(monkeypatch):
    monkeypatch.setattr(itad, "_get", lambda path, params=None: None)

    assert itad.get_price_history("game-uuid") == []


def test_amount_cents_falls_back_to_amount():
    assert itad._amount_cents({"amount": 19.99}) == 1999
    assert itad._amount_cents({"amount": 19.99, "amountInt": 1999}) == 1999
    assert itad._amount_cents(None) is None
