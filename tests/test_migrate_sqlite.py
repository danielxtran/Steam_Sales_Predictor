import hashlib
import sqlite3
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from collector.db.models import Game, GameGenre, Genre, ITADGame, PollState, PriceChange
from scripts.migrate_sqlite import migrate

# The pre-Alembic schema, as created by the old collector.
OLD_SCHEMA = """
CREATE TABLE games (
    app_id INTEGER NOT NULL PRIMARY KEY, title VARCHAR NOT NULL, developer VARCHAR,
    publisher VARCHAR, genres VARCHAR, release_date VARCHAR, metacritic_score INTEGER,
    total_reviews INTEGER, is_free BOOLEAN, last_updated DATETIME
);
CREATE TABLE price_snapshots (
    id INTEGER NOT NULL PRIMARY KEY, app_id INTEGER NOT NULL, fetched_at DATETIME,
    current_price FLOAT, original_price FLOAT, discount_pct INTEGER, on_sale BOOLEAN
);
CREATE TABLE itad_games (app_id INTEGER NOT NULL PRIMARY KEY, itad_id VARCHAR NOT NULL, slug VARCHAR);
CREATE TABLE itad_price_history (
    id INTEGER NOT NULL PRIMARY KEY, app_id INTEGER NOT NULL, shop VARCHAR, price FLOAT,
    discount_pct INTEGER, recorded_at DATETIME, fetched_at DATETIME
);
"""


@pytest.fixture
def old_db(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (10, "Counter-Strike", "Valve", "Valve", "Action", "Nov 1, 2000", 88, 100, 0,
         "2026-05-26 20:25:21.458041"),
        (20, "Indie Thing", "Dev", "Pub", "Action, Indie,Indie", "Q1 2027", None, 5, 0,
         "2026-05-26 21:00:00.000000"),
        (30, "Free Game", "Dev", "Pub", "", "Coming soon", None, 0, 1,
         "2026-05-26 22:00:00.000000"),
    ])
    conn.executemany("INSERT INTO price_snapshots VALUES (?,?,?,?,?,?,?)", [
        (1, 10, "2026-05-26 19:00:00.000000", 9.99, 9.99, 0, 0),
        (2, 10, "2026-05-27 19:00:00.000000", 9.99, 9.99, 0, 0),     # unchanged
        (3, 10, "2026-05-28 19:00:00.000000", 1.99, 9.99, 100, 1),   # sale; wrong reported pct
        (4, 20, "2026-05-26 19:30:00.000000", 16.99, 16.99, 0, 0),
        (5, 30, "2026-05-26 19:45:00.000000", None, None, 0, 0),     # free: no price
    ])
    conn.executemany("INSERT INTO itad_games VALUES (?,?,?)", [
        (10, "uuid-10", "counter-strike"),
        (99, "uuid-99", "not-in-games"),
    ])
    conn.executemany("INSERT INTO itad_price_history VALUES (?,?,?,?,?,?,?)", [
        (1, 10, "Steam", 1.99, 80, "2020-12-22 20:01:33.000000", "2026-05-27 17:00:00"),
        (2, 10, "Steam", 9.99, 0, "2021-01-05 19:57:06.000000", "2026-05-27 17:00:00"),
        (3, 10, "Steam", 1.99, 80, "2020-12-22 20:01:33.000000", "2026-05-28 17:00:00"),  # dup
        (4, 10, "GOG", 4.99, 50, "2020-12-23 10:00:00.000000", "2026-05-27 17:00:00"),
        (5, 10, "Fanatical", 5.99, 40, "2020-12-24 10:00:00.000000", "2026-05-27 17:00:00"),
    ])
    conn.commit()
    conn.close()
    return path


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_migrate_copies_and_converts(old_db, migrated_engine):
    summary = migrate(old_db, migrated_engine)

    with Session(migrated_engine) as session:
        cs = session.get(Game, 10)
        assert cs.release_date == date(2000, 11, 1)
        assert cs.created_at == _utc(2026, 5, 26, 19)
        assert session.get(Game, 20).release_date is None
        assert session.get(Game, 30).is_free is True

        steam = session.scalars(
            select(PriceChange).filter_by(source="steam").order_by(PriceChange.app_id, PriceChange.observed_at)
        ).all()
        assert [(p.app_id, p.initial_cents, p.final_cents, p.discount_pct) for p in steam] == [
            (10, 999, 999, 0),
            (10, 999, 199, 80),
            (20, 1699, 1699, 0),
        ]

        itad = session.scalars(select(PriceChange).filter_by(source="itad").order_by(PriceChange.observed_at)).all()
        assert [(p.observed_at, p.final_cents, p.discount_pct, p.initial_cents) for p in itad] == [
            (_utc(2020, 12, 22, 20, 1, 33), 199, 80, None),
            (_utc(2021, 1, 5, 19, 57, 6), 999, 0, None),
        ]

        genres = {g.id: g.name for g in session.scalars(select(Genre))}
        links = {(link.app_id, genres[link.genre_id]) for link in session.scalars(select(GameGenre))}
        assert links == {(10, "Action"), (20, "Action"), (20, "Indie")}

        assert [g.app_id for g in session.scalars(select(ITADGame))] == [10]

        states = session.scalars(select(PollState).order_by(PollState.app_id)).all()
        assert [s.app_id for s in states] == [10, 20, 30]
        assert states[0].last_checked_at == _utc(2026, 5, 28, 19)
        assert all(s.tier == "normal" and s.next_check_at is not None for s in states)

    t = summary.tables
    assert (t["games"].read, t["games"].written) == (3, 3)
    assert (t["price_changes (steam)"].read, t["price_changes (steam)"].written) == (5, 3)
    assert t["price_changes (steam)"].skipped == {"unchanged": 1, "no price": 1}
    assert (t["price_changes (itad)"].read, t["price_changes (itad)"].written) == (5, 2)
    assert t["price_changes (itad)"].skipped == {"not Steam store": 2, "duplicate": 1}
    assert t["itad_games"].skipped == {"unknown game": 1}
    assert t["game_genres"].skipped == {"duplicate": 1, "empty": 1}
    assert "price_changes (itad)" in summary.render()


def test_migrate_does_not_modify_input(old_db, migrated_engine):
    before = hashlib.sha256(old_db.read_bytes()).hexdigest()

    migrate(old_db, migrated_engine)

    assert hashlib.sha256(old_db.read_bytes()).hexdigest() == before


def test_migrate_refuses_non_empty_target(old_db, migrated_engine):
    migrate(old_db, migrated_engine)

    with pytest.raises(SystemExit, match="already has games"):
        migrate(old_db, migrated_engine)


def test_migrate_refuses_unmigrated_target(old_db, tmp_path):
    with pytest.raises(SystemExit, match="alembic upgrade head"):
        migrate(old_db, create_engine(f"sqlite:///{tmp_path / 'empty.db'}"))
