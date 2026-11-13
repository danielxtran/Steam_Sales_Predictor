import logging

import pytest
import requests
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from collector.db.models import Game, PollState, PriceChange
from collector.jobs import itad as itad_job
from collector.sources import itad
from tests.conftest import FakeResponse, load_fixture


@pytest.fixture
def db(migrated_engine):
    with sessionmaker(migrated_engine)() as session:
        for app_id, tier, reviews in ((10, "normal", 50), (20, "normal", 5000), (30, "hot", 1), (40, "normal", None)):
            session.add(Game(app_id=app_id, title=f"Game {app_id}", total_reviews=reviews))
            session.add(PollState(app_id=app_id, tier=tier))
        session.commit()
    return sessionmaker(migrated_engine)


@pytest.fixture
def itad_http(monkeypatch):
    """Queue fake ITAD responses; no real requests, no waiting."""
    queue, calls = [], []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(itad, "ITAD_API_KEY", "SECRET-ITAD-KEY")
    monkeypatch.setattr(itad.requests, "get", fake_get)
    monkeypatch.setattr(itad, "itad_limiter", itad.RateLimiter(0))
    return queue, calls


def found():
    return FakeResponse(200, load_fixture("itad_lookup_portal2.json"))


def history():
    return FakeResponse(200, load_fixture("itad_history_portal2.json"))


def checked(db):
    with db() as session:
        return {s.app_id: s.itad_checked_at is not None for s in session.query(PollState)}


def test_pending_games_hot_then_most_reviewed(db):
    with db() as session:
        assert [s.app_id for s in itad_job.pending_games(session, limit=10)] == [30, 20, 10, 40]


def test_backfill_stores_steam_history_and_marks_games(db, itad_http):
    queue, calls = itad_http
    queue += [found(), history(),                          # 30: found, has history
              FakeResponse(200, {"found": False})]         # 20: ITAD doesn't know it

    summary = itad_job.run(limit=2, session_factory=db)

    assert (summary.checked, summary.changed, summary.failed) == (2, 1, 0)
    assert checked(db) == {10: False, 20: True, 30: True, 40: False}
    with db() as session:
        rows = session.scalars(select(PriceChange).order_by(PriceChange.observed_at)).all()
        assert {r.app_id for r in rows} == {30}
        assert [r.final_cents for r in rows] == [199, 999]   # Steam only; GOG dropped
        assert rows[0].initial_cents == 999                   # regular price is kept now

    # Games already looked up (found or not) are not looked up again.
    queue += [FakeResponse(200, {"found": False})] * 2
    itad_job.run(limit=10, session_factory=db)
    assert [p["appid"] for _, p in calls if "appid" in p] == [30, 20, 10, 40]


def test_failed_request_is_retried_next_run(db, itad_http):
    queue, _ = itad_http
    queue += [FakeResponse(500), FakeResponse(200, {"found": False})]

    summary = itad_job.run(limit=2, session_factory=db)

    assert (summary.checked, summary.failed) == (2, 1)
    assert checked(db)[30] is False      # failed: still pending
    assert checked(db)[20] is True


def test_rate_limit_stops_the_run(db, itad_http):
    queue, calls = itad_http
    queue += [FakeResponse(429)]

    summary = itad_job.run(limit=10, session_factory=db)

    assert summary.extra["stopped"] == "rate limited"
    assert len(calls) == 1
    assert not any(checked(db).values())


def test_repeated_errors_stop_the_run(db, itad_http, monkeypatch):
    monkeypatch.setattr(itad_job, "MAX_CONSECUTIVE_FAILURES", 2)
    queue, calls = itad_http
    queue += [FakeResponse(503), FakeResponse(503)]

    summary = itad_job.run(limit=10, session_factory=db)

    assert summary.extra["stopped"] == "too many errors"
    assert summary.failed == 2


def test_errors_never_include_the_api_key(db, itad_http, caplog):
    queue, _ = itad_http
    queue += [requests.ConnectionError("failed: ...?key=SECRET-ITAD-KEY&appid=30")] * 5

    with caplog.at_level(logging.WARNING):
        itad_job.run(limit=10, session_factory=db)

    assert "ConnectionError" in caplog.text
    assert "SECRET-ITAD-KEY" not in caplog.text


def test_missing_key_raises_instead_of_silently_skipping(monkeypatch):
    monkeypatch.setattr(itad, "ITAD_API_KEY", "")

    with pytest.raises(itad.ITADError, match="ITAD_API_KEY"):
        itad.lookup_game(620)


def test_full_history_resets_lookups(db, itad_http):
    queue, _ = itad_http
    queue += [found(), history()]
    itad_job.run(limit=1, session_factory=db)
    assert checked(db)[30] is True

    itad_job.run(limit=0, full_history=True, session_factory=db)

    assert not any(checked(db).values())
    with db() as session:
        assert session.query(PriceChange).filter_by(source="itad").count() == 0
