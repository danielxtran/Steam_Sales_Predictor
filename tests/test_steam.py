import copy

import pytest

from collector.sources.steam import _compute_discount, parse_game
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
        "genres": "Action,Adventure",
        "release_date": "Apr 18, 2011",
        "metacritic_score": 95,
        "total_reviews": 392931,
        "is_free": False,
        "current_price": 9.99,
        "original_price": 9.99,
        "discount_pct": 0,
        "on_sale": False,
    }


def test_parse_game_on_sale(portal2):
    portal2["price_overview"].update(final=199, discount_percent=80)

    parsed = parse_game(620, portal2)

    assert parsed["current_price"] == 1.99
    assert parsed["original_price"] == 9.99
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
    assert parsed["current_price"] is None
    assert parsed["original_price"] is None
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
