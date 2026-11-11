import json
import os
from pathlib import Path

import pytest
import requests
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

# Point the engine at an in-memory database before any collector module is imported,
# so tests can never touch data/steam_prices.db.
os.environ["DATABASE_URL"] = "sqlite://"

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).parent.parent


def alembic_upgrade(connection) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.attributes["connection"] = connection
    command.upgrade(cfg, "head")


@pytest.fixture
def migrated_engine(tmp_path):
    """A fresh SQLite file with the schema built by the real Alembic migrations."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    with engine.connect() as connection:
        alembic_upgrade(connection)
    yield engine
    engine.dispose()


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Fail loudly if any test tries to make a real HTTP request."""
    def _blocked(*args, **kwargs):
        raise RuntimeError("Tests must not call real APIs; use tests/fixtures/ instead")

    monkeypatch.setattr(requests.sessions.Session, "request", _blocked)
