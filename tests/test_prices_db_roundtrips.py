"""
The prices job runs against Neon over the internet, where every SQL statement costs a
network round trip. These tests count statements so a regression shows up locally.
"""
from datetime import timedelta

from sqlalchemy import event

from collector.db.models import Game, PollState, utcnow
from collector.jobs import prices
from collector.settings import Session
from tests.conftest import FakeResponse

BATCHES = 3


def _seed(engine, n):
    now = utcnow()
    with Session(bind=engine) as session:
        for app_id in range(1, n + 1):
            session.add(Game(app_id=app_id, title=f"Game {app_id}", details_checked_at=now))
            session.add(PollState(app_id=app_id, tier="normal", next_check_at=now - timedelta(hours=1)))
        session.commit()


def _batch_response(params):
    ids = params["appids"].split(",")
    return FakeResponse(200, {
        i: {"success": True, "data": {"price_overview": {"initial": 999, "final": 499}}} for i in ids
    })


def test_statements_per_batch_do_not_grow_with_batch_size(migrated_engine, steam_http):
    _seed(migrated_engine, BATCHES * prices.PRICE_BATCH_SIZE)
    steam_http.responses += [_batch_response] * BATCHES
    statements = []
    event.listen(migrated_engine, "before_cursor_execute",
                 lambda conn, cursor, sql, *args: statements.append(sql))

    summary = prices.run(limit=BATCHES * prices.PRICE_BATCH_SIZE,
                         session_factory=lambda: Session(bind=migrated_engine))

    assert summary.checked == BATCHES * prices.PRICE_BATCH_SIZE
    assert summary.changed == BATCHES * prices.PRICE_BATCH_SIZE
    # A handful of statements per batch (latest prices, batched inserts and updates),
    # never one or more per game.
    per_batch = len(statements) / BATCHES
    assert per_batch < 15, f"{per_batch:.0f} statements per batch of {prices.PRICE_BATCH_SIZE}"
