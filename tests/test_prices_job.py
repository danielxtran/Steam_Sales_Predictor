from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from collector.db.models import Game, GameGenre, PollState, PriceChange, utcnow
from collector.jobs import prices
from collector.jobs.common import latest_prices, record_price
from tests.conftest import FakeResponse, load_fixture


@pytest.fixture
def db(migrated_engine):
    return sessionmaker(migrated_engine)


def add_game(session, app_id, tier="normal", due_in=timedelta(hours=-1), details_age=timedelta(days=1),
             price=None):
    now = utcnow()
    session.add(Game(app_id=app_id, title=f"Game {app_id}",
                     details_checked_at=None if details_age is None else now - details_age))
    session.add(PollState(app_id=app_id, tier=tier, next_check_at=now + due_in))
    if price:
        initial, final, pct = price
        session.add(PriceChange(app_id=app_id, observed_at=now - timedelta(days=3), source="itad",
                                initial_cents=initial, final_cents=final, discount_pct=pct))


def steam_rows(session):
    return session.scalars(select(PriceChange).filter_by(source="steam").order_by(PriceChange.app_id)).all()


# --- change detection ------------------------------------------------------

def test_same_price_adds_no_row_and_new_price_adds_one(migrated_engine):
    now = utcnow()
    with Session(migrated_engine) as session:
        session.add(Game(app_id=620, title="Portal 2"))
        same = {"initial_cents": 999, "final_cents": 999, "discount_pct": 0}
        sale = {"initial_cents": 999, "final_cents": 199, "discount_pct": 80}

        assert record_price(session, 620, same, now, None) is True
        session.flush()
        last = latest_prices(session, [620])[620]
        assert record_price(session, 620, same, now + timedelta(hours=1), last) is False
        assert record_price(session, 620, sale, now + timedelta(hours=2), last) is True
        session.flush()

        assert [r.final_cents for r in steam_rows(session)] == [999, 199]


def test_discount_change_alone_counts_as_change(migrated_engine):
    now = utcnow()
    with Session(migrated_engine) as session:
        session.add(Game(app_id=620, title="Portal 2"))
        last = PriceChange(app_id=620, observed_at=now, source="itad",
                           initial_cents=999, final_cents=999, discount_pct=10)
        session.add(last)
        assert record_price(session, 620, {"initial_cents": 999, "final_cents": 999, "discount_pct": 0},
                            now, last) is True


def test_price_job_saves_only_changes(db, steam_http):
    with db() as session:
        add_game(session, 620, price=(999, 999, 0))    # unchanged
        add_game(session, 400, price=(999, 249, 75))   # sale ended -> change
        add_game(session, 570)                         # free: no price
        add_game(session, 1)                           # success=false
        session.commit()
    steam_http.responses.append(FakeResponse(200, load_fixture("steam_prices_batch.json")))

    summary = prices.run(limit=10, session_factory=db)

    assert (summary.checked, summary.changed, summary.failed) == (4, 1, 1)
    with db() as session:
        assert [(r.app_id, r.final_cents) for r in steam_rows(session)] == [(400, 999)]
        assert "success=false" in session.get(PollState, 1).last_error
        assert session.get(PollState, 620).last_error is None

    # Nothing is due right after a run, so a second run checks nothing.
    assert prices.run(limit=10, session_factory=db).checked == 0


# --- tier scheduling -------------------------------------------------------

def test_next_check_depends_on_tier(db, steam_http):
    with db() as session:
        add_game(session, 620, tier="hot")
        add_game(session, 400, tier="normal")
        session.commit()
    steam_http.responses.append(FakeResponse(200, load_fixture("steam_prices_batch.json")))

    prices.run(limit=10, session_factory=db)

    with db() as session:
        hot, normal = session.get(PollState, 620), session.get(PollState, 400)
        assert hot.next_check_at - hot.last_checked_at == timedelta(hours=6)
        assert normal.next_check_at - normal.last_checked_at == timedelta(days=2)


def test_due_games_hot_first_and_skips_not_due(db):
    with db() as session:
        add_game(session, 1, tier="normal", due_in=timedelta(days=-3))
        add_game(session, 2, tier="hot", due_in=timedelta(minutes=-5))
        add_game(session, 3, tier="hot", due_in=timedelta(hours=2))      # not due yet
        add_game(session, 4, tier="normal", due_in=timedelta(days=-1))
        session.commit()

        picked = [g.app_id for _, g in prices.due_games(session, utcnow(), limit=10)]
        assert picked == [2, 1, 4]
        assert [g.app_id for _, g in prices.due_games(session, utcnow(), limit=1)] == [2]


def test_failed_check_still_reschedules_and_saves_error(db, steam_http):
    with db() as session:
        add_game(session, 620, tier="hot")
        session.commit()
    steam_http.responses += [FakeResponse(500)] * 4

    summary = prices.run(limit=10, session_factory=db)

    assert (summary.checked, summary.failed) == (1, 1)
    with db() as session:
        state = session.get(PollState, 620)
        assert "HTTP 500" in state.last_error
        assert state.next_check_at - state.last_checked_at == timedelta(hours=6)


# --- details for new / stale games ----------------------------------------

def test_details_fetched_only_for_new_or_stale_games(db, steam_http):
    with db() as session:
        add_game(session, 620, details_age=None)                  # new: never fetched
        add_game(session, 400, details_age=timedelta(days=31))    # stale
        add_game(session, 570, details_age=timedelta(days=5))     # fresh: price only
        session.commit()
    details = load_fixture("steam_appdetails_620.json")

    def appdetails(params):
        app_id = str(params["appids"])
        return FakeResponse(200, {app_id: details["620"]})

    steam_http.responses += [appdetails, appdetails,
                             FakeResponse(200, load_fixture("steam_prices_batch.json"))]

    summary = prices.run(limit=10, details_limit=5, session_factory=db)

    assert summary.extra["details"] == 2
    detail_calls = [p for _, p in steam_http.calls if "filters" not in p]
    assert sorted(p["appids"] for p in detail_calls) == [400, 620]
    with db() as session:
        game = session.get(Game, 620)
        assert game.title == "Portal 2"
        assert game.details_checked_at is not None
        assert session.query(GameGenre).filter_by(app_id=620).count() == 2
        assert {r.app_id for r in steam_rows(session)} == {400, 620}


def test_details_limit_falls_back_to_price_check(db, steam_http):
    with db() as session:
        add_game(session, 620, details_age=None)
        add_game(session, 400, details_age=None)
        session.commit()
    details = load_fixture("steam_appdetails_620.json")
    steam_http.responses += [
        lambda params: FakeResponse(200, {str(params["appids"]): details["620"]}),
        FakeResponse(200, load_fixture("steam_prices_batch.json")),
    ]

    summary = prices.run(limit=10, details_limit=1, session_factory=db)

    assert summary.extra["details"] == 1
    assert summary.checked == 2          # the other game still got a price check
    with db() as session:
        fetched = [session.get(Game, a).details_checked_at is not None for a in (400, 620)]
        assert sorted(fetched) == [False, True]   # the other is picked up on a later run


# --- 429 handling ------------------------------------------------------------

def test_persistent_rate_limit_stops_the_run(db, steam_http, monkeypatch):
    monkeypatch.setattr(prices, "PRICE_BATCH_SIZE", 1)
    with db() as session:
        for app_id in (620, 400, 570):
            add_game(session, app_id)
        session.commit()
    steam_http.responses += [FakeResponse(429) for _ in range(4)]

    summary = prices.run(limit=10, session_factory=db)

    assert summary.extra["stopped"] == "rate limited"
    assert (summary.checked, summary.failed) == (1, 1)    # the other two stay due
    assert steam_http.sleeps == [30, 60, 120]
    with db() as session:
        assert len(prices.due_games(session, utcnow(), limit=10)) == 2
        errors = [s.last_error for s in session.query(PollState) if s.last_error]
        assert len(errors) == 1 and "429" in errors[0]
