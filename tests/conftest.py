import json
import os
from pathlib import Path
from types import SimpleNamespace

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


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no JSON body")
        return self._body


@pytest.fixture
def steam_http(monkeypatch):
    """
    Replace HTTP calls in the Steam client with queued fake responses, and record sleeps
    instead of waiting. Queue responses with `steam_http.responses.append(...)`; a callable
    entry is called with the request params so it can answer based on them.
    """
    from collector.sources import steam

    rec = SimpleNamespace(responses=[], calls=[], sleeps=[])

    def fake_get(url, params=None, timeout=None):
        rec.calls.append((url, params))
        if not rec.responses:
            raise AssertionError(f"unexpected request to {url}")
        response = rec.responses.pop(0)
        if callable(response):
            response = response(params)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(steam.requests, "get", fake_get)
    monkeypatch.setattr(steam.time, "sleep", rec.sleeps.append)
    monkeypatch.setattr(steam, "store_limiter", steam.RateLimiter(0, sleep=rec.sleeps.append))
    return rec
