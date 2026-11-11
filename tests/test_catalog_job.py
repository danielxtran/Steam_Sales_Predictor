from datetime import timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from collector.db.models import Game, PollState, utcnow
from collector.jobs import catalog
from collector.sources.steam import SteamError
from collector.sources.steamspy import SteamSpyError


@pytest.fixture
def db(migrated_engine):
    with sessionmaker(migrated_engine)() as session:
        later = utcnow() + timedelta(days=1)
        session.add_all([
            Game(app_id=10, title="Counter-Strike"), PollState(app_id=10, tier="hot", next_check_at=later),
            Game(app_id=20, title="TFC"), PollState(app_id=20, tier="normal", next_check_at=later),
        ])
        session.commit()
    return sessionmaker(migrated_engine)


def sources(monkeypatch, apps=None, top=None, featured=None):
    def answer(value):
        def fetch():
            if isinstance(value, Exception):
                raise value
            return value
        return fetch

    monkeypatch.setattr(catalog, "get_app_list", answer(apps if apps is not None else []))
    monkeypatch.setattr(catalog, "get_top_list_app_ids", answer(top if top is not None else []))
    monkeypatch.setattr(catalog, "get_featured_app_ids", answer(featured if featured is not None else []))


def tiers(db):
    with db() as session:
        return {s.app_id: s.tier for s in session.query(PollState)}


def test_adds_new_games_and_marks_hot_tier(db, monkeypatch):
    apps = [{"appid": a, "name": f"App {a}"} for a in (10, 20, 30, 40)]
    sources(monkeypatch, apps=apps, top=[30, 999], featured=[20])

    summary = catalog.run(session_factory=db)

    assert tiers(db) == {10: "normal", 20: "hot", 30: "hot", 40: "normal"}
    assert summary.checked == 4
    assert summary.extra["new"] == 2
    assert (summary.extra["promoted"], summary.extra["demoted"]) == (2, 1)
    assert summary.changed == 5
    with db() as session:
        new = session.get(PollState, 40)
        assert new.next_check_at <= utcnow()                      # new games are due now
        assert session.get(PollState, 20).next_check_at <= utcnow()   # newly hot: due now
        assert session.get(Game, 30).title == "App 30"
        assert session.get(Game, 30).details_checked_at is None


def test_source_outage_does_not_demote(db, monkeypatch):
    sources(monkeypatch, apps=[], top=SteamSpyError("down"), featured=[20])

    summary = catalog.run(session_factory=db)

    assert tiers(db) == {10: "hot", 20: "hot"}
    assert summary.failed == 1


def test_app_list_failure_is_reported(db, monkeypatch):
    sources(monkeypatch, apps=SteamError("GetAppList: HTTP 500"), top=[10], featured=[])

    summary = catalog.run(session_factory=db)

    assert summary.extra["app_list"] == "failed"
    assert summary.failed == 1
    assert tiers(db) == {10: "hot", 20: "normal"}

    # The command exits non-zero so the scheduled workflow run shows as failed.
    monkeypatch.setattr(catalog, "check_db", lambda: True)
    monkeypatch.setattr(catalog, "run", lambda: summary)
    with pytest.raises(SystemExit):
        catalog.main([])
