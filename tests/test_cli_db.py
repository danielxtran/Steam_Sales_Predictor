from datetime import datetime, timezone

from sqlalchemy.orm import Session

from collector import cli
from collector.db.models import Game, GameGenre, Genre, PollState, PriceChange
from collector.sources.steam import _compute_discount


def _parsed(final_cents=999, initial_cents=999, genres=("Action", "Indie")):
    return {
        "app_id": 620, "title": "Portal 2", "developer": "Valve", "publisher": "Valve",
        "genres": list(genres), "release_date": None, "metacritic_score": 95,
        "total_reviews": 10, "is_free": False, "type": "game",
        "final_cents": final_cents, "initial_cents": initial_cents,
        "discount_pct": _compute_discount(initial_cents, final_cents),
    }


def _collect(session, parsed):
    cli._upsert_game(session, parsed)
    cli._record_price(session, parsed)
    session.commit()


def test_price_row_only_written_when_price_changes(migrated_engine):
    with Session(migrated_engine) as session:
        _collect(session, _parsed(final_cents=999))
        _collect(session, _parsed(final_cents=999))
        _collect(session, _parsed(final_cents=499))

        rows = session.query(PriceChange).order_by(PriceChange.observed_at).all()
        assert [r.final_cents for r in rows] == [999, 499]
        assert all(r.source == "steam" for r in rows)


def test_every_check_updates_poll_state(migrated_engine):
    with Session(migrated_engine) as session:
        _collect(session, _parsed())
        first = session.get(PollState, 620).last_checked_at
        _collect(session, _parsed())

        assert session.get(PollState, 620).last_checked_at > first


def test_genres_are_linked_not_duplicated(migrated_engine):
    with Session(migrated_engine) as session:
        _collect(session, _parsed(genres=("Action", "Indie")))
        _collect(session, _parsed(genres=("Action", "RPG")))

        assert sorted(g.name for g in session.query(Genre)) == ["Action", "Indie", "RPG"]
        linked = {
            session.get(Genre, link.genre_id).name
            for link in session.query(GameGenre).filter_by(app_id=620)
        }
        assert linked == {"Action", "RPG"}


def test_itad_history_keeps_steam_only_and_skips_duplicates(migrated_engine, monkeypatch):
    at = datetime(2024, 11, 26, 17, 0, tzinfo=timezone.utc)
    history = [
        {"shop": "Steam", "price_cents": 199, "regular_cents": 999, "discount_pct": 80, "recorded_at": at},
        {"shop": "GOG", "price_cents": 499, "regular_cents": 999, "discount_pct": 50, "recorded_at": at},
    ]
    monkeypatch.setattr(cli, "lookup_game", lambda app_id: {"id": "uuid", "slug": "portal-2"})
    monkeypatch.setattr(cli, "get_price_history", lambda itad_id: history)

    with Session(migrated_engine) as session:
        session.add(Game(app_id=620, title="Portal 2"))
        cli._fetch_itad_history(session, 620)
        session.commit()
        cli._fetch_itad_history(session, 620)
        session.commit()

        rows = session.query(PriceChange).all()
        assert [(r.source, r.final_cents, r.initial_cents) for r in rows] == [("itad", 199, 999)]
