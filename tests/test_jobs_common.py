from datetime import datetime, timezone

from sqlalchemy.orm import Session

from collector.db.models import Game, GameGenre, Genre, PriceChange, utcnow
from collector.jobs import itad as itad_job
from collector.jobs.common import RunSummary, apply_details
from tests.conftest import load_fixture


def test_apply_details_links_genres_without_duplicates(migrated_engine):
    raw = load_fixture("steam_appdetails_620.json")["620"]["data"]
    with Session(migrated_engine) as session:
        game = Game(app_id=620, title="App 620")
        session.add(game)
        apply_details(session, game, raw, utcnow())
        raw["genres"] = [{"id": "1", "description": "Action"}, {"id": "3", "description": "RPG"}]
        apply_details(session, game, raw, utcnow())
        session.commit()

        assert sorted(g.name for g in session.query(Genre)) == ["Action", "Adventure", "RPG"]
        linked = {session.get(Genre, link.genre_id).name for link in session.query(GameGenre)}
        assert linked == {"Action", "RPG"}
        assert game.title == "Portal 2"


def test_apply_details_marks_non_games(migrated_engine):
    raw = load_fixture("steam_appdetails_620.json")["620"]["data"]
    raw["type"] = "dlc"
    with Session(migrated_engine) as session:
        game = Game(app_id=620, title="App 620")
        session.add(game)

        assert apply_details(session, game, raw, utcnow()) is None
        assert game.type == "dlc"
        assert game.details_checked_at is not None


def test_summary_line_has_required_fields():
    summary = RunSummary("prices", checked=50, changed=3, failed=1, extra={"details": 10})

    line = summary.line()

    assert line.startswith("prices: checked=50 changed=3 failed=1 details=10 in ")
    assert line.endswith("s")


def test_itad_history_keeps_steam_only_and_skips_duplicates(migrated_engine, monkeypatch):
    at = datetime(2024, 11, 26, 17, 0, tzinfo=timezone.utc)
    history = [
        {"shop": "Steam", "price_cents": 199, "regular_cents": 999, "discount_pct": 80, "recorded_at": at},
        {"shop": "GOG", "price_cents": 499, "regular_cents": 999, "discount_pct": 50, "recorded_at": at},
    ]
    monkeypatch.setattr(itad_job, "lookup_game", lambda app_id: {"id": "uuid", "slug": "portal-2"})
    monkeypatch.setattr(itad_job, "get_price_history", lambda itad_id: history)

    with Session(migrated_engine) as session:
        session.add(Game(app_id=620, title="Portal 2"))
        assert itad_job.fetch_itad_history(session, 620) == 1
        session.commit()
        assert itad_job.fetch_itad_history(session, 620) == 0
        session.commit()

        rows = session.query(PriceChange).all()
        assert [(r.source, r.final_cents, r.initial_cents) for r in rows] == [("itad", 199, 999)]
