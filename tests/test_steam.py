import copy
from datetime import date

import pytest

from collector.sources.steam import _compute_discount, parse_game, parse_release_date
from tests.conftest import load_fixture


@pytest.fixture
def portal2():
    return copy.deepcopy(load_fixture("steam_appdetails_620.json")["620"]["data"])


def test_parse_game_full_price(portal2):
    parsed = parse_game(620, portal2)

    assert parsed == {
        "app_id": 620,
        "title": "Portal 2",
        "developer": "Valve",
        "publisher": "Valve",
        "genres": ["Action", "Adventure"],
        "release_date": date(2011, 4, 18),
        "metacritic_score": 95,
        "total_reviews": 392931,
        "is_free": False,
        "type": "game",
        "final_cents": 999,
        "initial_cents": 999,
        "discount_pct": 0,
        "on_sale": False,
    }


def test_parse_game_on_sale(portal2):
    portal2["price_overview"].update(final=199, discount_percent=80)

    parsed = parse_game(620, portal2)

    assert parsed["final_cents"] == 199
    assert parsed["initial_cents"] == 999
    assert parsed["discount_pct"] == 80
    assert parsed["on_sale"] is True


def test_parse_game_ignores_wrong_reported_discount(portal2):
    # Steam sometimes reports a discount even though the prices are unchanged.
    portal2["price_overview"]["discount_percent"] = 100

    parsed = parse_game(620, portal2)

    assert parsed["discount_pct"] == 0
    assert parsed["on_sale"] is False


def test_parse_game_free_game_has_no_price(portal2):
    portal2["is_free"] = True
    del portal2["price_overview"]

    parsed = parse_game(620, portal2)

    assert parsed["is_free"] is True
    assert parsed["final_cents"] is None
    assert parsed["initial_cents"] is None
    assert parsed["discount_pct"] == 0
    assert parsed["on_sale"] is False


def test_parse_game_skips_non_games(portal2):
    portal2["type"] = "dlc"

    assert parse_game(620, portal2) is None


@pytest.mark.parametrize(
    ("initial", "final", "expected"),
    [
        (999, 999, 0),
        (999, 199, 80),
        (1999, 499, 75),
        (999, None, 0),   # missing final price means no discount
        (None, 499, 0),   # missing initial price
        (0, 0, 0),        # free
    ],
)
def test_compute_discount(initial, final, expected):
    assert _compute_discount(initial, final) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Oct 21, 2020", date(2020, 10, 21)),
        ("21 Oct, 2020", date(2020, 10, 21)),
        ("August 3, 2021", date(2021, 8, 3)),
        ("Q1 2027", None),
        ("August 2026", None),
        ("2026", None),
        ("Coming soon", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_release_date(text, expected):
    assert parse_release_date(text) == expected
