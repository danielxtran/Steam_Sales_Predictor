import json
import os
from pathlib import Path

import pytest
import requests

# Point the engine at an in-memory database before any collector module is imported,
# so tests can never touch data/steam_prices.db.
os.environ["DATABASE_URL"] = "sqlite://"

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Fail loudly if any test tries to make a real HTTP request."""
    def _blocked(*args, **kwargs):
        raise RuntimeError("Tests must not call real APIs; use tests/fixtures/ instead")

    monkeypatch.setattr(requests.sessions.Session, "request", _blocked)
