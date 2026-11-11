from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from collector.db.models import Game, PriceChange
from tests.conftest import alembic_upgrade


def test_migrations_create_every_table(migrated_engine):
    tables = set(inspect(migrated_engine).get_table_names())

    assert tables >= {
        "games", "genres", "game_genres", "price_changes",
        "poll_state", "itad_games", "sale_calendar", "alembic_version",
    }


def test_migrations_refuse_old_schema_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE price_snapshots (id INTEGER PRIMARY KEY)"))

    with engine.connect() as conn, pytest.raises(RuntimeError, match="old-schema"):
        alembic_upgrade(conn)
    assert "games" not in inspect(engine).get_table_names()


def test_observed_at_round_trips_as_utc(migrated_engine):
    eastern = timezone(timedelta(hours=-5))
    with Session(migrated_engine) as session:
        session.add(Game(app_id=1, title="Test"))
        session.add(PriceChange(
            app_id=1, observed_at=datetime(2025, 1, 1, 7, 0, tzinfo=eastern),
            initial_cents=999, final_cents=499, discount_pct=50, source="steam",
        ))
        session.commit()

        stored = session.query(PriceChange).one().observed_at

    assert stored == datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert stored.tzinfo == timezone.utc


def test_naive_datetimes_are_rejected(migrated_engine):
    with Session(migrated_engine) as session:
        session.add(Game(app_id=1, title="Test"))
        session.add(PriceChange(
            app_id=1, observed_at=datetime(2025, 1, 1), final_cents=499, source="steam",
        ))
        with pytest.raises(Exception, match="timezone-aware"):
            session.commit()


def test_price_changes_unique_per_app_time_source(migrated_engine):
    at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with Session(migrated_engine) as session:
        session.add(Game(app_id=1, title="Test"))
        session.add(PriceChange(app_id=1, observed_at=at, final_cents=499, source="steam"))
        session.add(PriceChange(app_id=1, observed_at=at, final_cents=499, source="itad"))
        session.commit()

        session.add(PriceChange(app_id=1, observed_at=at, final_cents=299, source="steam"))
        with pytest.raises(IntegrityError):
            session.commit()
