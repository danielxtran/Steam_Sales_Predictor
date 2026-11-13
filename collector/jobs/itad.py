#!/usr/bin/env python3
"""
ITAD job: backfill Steam-store price history from IsThereAnyDeal.

Usage
-----
  python -m collector.jobs.itad                   # up to 300 games never looked up on ITAD
  python -m collector.jobs.itad --limit 100 --max-minutes 10
  python -m collector.jobs.itad --full-history    # wipe ITAD rows and look every game up again

Each game is looked up once (poll_state.itad_checked_at), hot and most-reviewed games
first. Games ITAD doesn't know are marked too, so they aren't retried every run; games
whose request failed are left for the next run.
"""
import argparse
import logging
import time
from datetime import timezone

from sqlalchemy import case, select, update

from collector.db.models import Game, ITADGame, PollState, PriceChange, utcnow
from collector.jobs.common import RunSummary, check_db, setup_logging
from collector.settings import ITAD_API_KEY, Session
from collector.sources.itad import ITADError, get_price_history, lookup_game

# Stop a run after this many failures in a row: ITAD is probably down.
MAX_CONSECUTIVE_FAILURES = 5

logger = logging.getLogger(__name__)


def fetch_itad_history(session, app_id: int) -> int:
    """Store a game's Steam-store history from ITAD. Returns the number of rows added."""
    itad_record = session.get(ITADGame, app_id)
    if not itad_record:
        game_info = lookup_game(app_id)
        if not game_info:
            return 0
        itad_record = ITADGame(
            app_id=app_id,
            itad_id=game_info["id"],
            slug=game_info.get("slug"),
        )
        session.merge(itad_record)

    existing = {
        row[0] for row in
        session.query(PriceChange.observed_at).filter_by(app_id=app_id, source="itad")
    }
    added = 0
    for entry in get_price_history(itad_record.itad_id):
        # Only Steam-store history is kept; other shops' prices don't apply to Steam sales.
        if entry["shop"] != "Steam" or entry["recorded_at"] is None or entry["price_cents"] is None:
            continue
        observed_at = entry["recorded_at"].astimezone(timezone.utc)
        if observed_at in existing:
            continue
        existing.add(observed_at)
        session.add(PriceChange(
            app_id=app_id,
            observed_at=observed_at,
            initial_cents=entry["regular_cents"],
            final_cents=entry["price_cents"],
            discount_pct=entry["discount_pct"] or 0,
            source="itad",
        ))
        added += 1
    return added


def pending_games(session, limit: int) -> list[PollState]:
    """Games never looked up on ITAD: hot first, then the most-reviewed."""
    query = (
        select(PollState)
        .join(Game, Game.app_id == PollState.app_id)
        .where(Game.type == "game", PollState.itad_checked_at.is_(None))
        .order_by(
            case((PollState.tier == "hot", 0), else_=1),
            Game.total_reviews.desc().nulls_last(),
            PollState.app_id,
        )
        .limit(limit)
    )
    return list(session.scalars(query))


def run(limit: int = 300, full_history: bool = False, max_seconds: float | None = None,
        session_factory=Session) -> RunSummary:
    """
    Look up ITAD history for games that haven't been looked up yet.

    full_history=True wipes existing ITAD price_changes rows and marks every game as not
    looked up, so history is re-fetched from scratch over the following runs.
    """
    summary = RunSummary("itad")
    deadline = time.monotonic() + max_seconds if max_seconds else None
    with session_factory() as session:
        if full_history:
            logger.info("Clearing existing ITAD price history for full re-fetch...")
            session.query(PriceChange).filter_by(source="itad").delete()
            session.execute(update(PollState).values(itad_checked_at=None))
            session.commit()

        pending = pending_games(session, limit)
        logger.info("ITAD back-fill: %d games to look up", len(pending))
        failures_in_a_row = 0
        for i, state in enumerate(pending, start=1):
            if deadline and time.monotonic() >= deadline:
                summary.extra["stopped"] = "time limit"
                break
            app_id = state.app_id
            try:
                added = fetch_itad_history(session, app_id)
            except ITADError as exc:
                session.rollback()
                summary.checked += 1
                summary.failed += 1
                failures_in_a_row += 1
                logger.warning("ITAD lookup for %d failed: %s", app_id, exc)
                if "429" in str(exc):
                    summary.extra["stopped"] = "rate limited"
                    break
                if failures_in_a_row >= MAX_CONSECUTIVE_FAILURES:
                    summary.extra["stopped"] = "too many errors"
                    break
                continue
            failures_in_a_row = 0
            session.get(PollState, app_id).itad_checked_at = utcnow()
            session.commit()
            summary.checked += 1
            if added:
                summary.changed += 1
            if i % 25 == 0:
                logger.info("Back-fill progress: %d/%d", i, len(pending))
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m collector.jobs.itad", description="Backfill Steam-store price history from ITAD")
    parser.add_argument("--limit", type=int, default=300, help="Max games to look up (default: 300)")
    parser.add_argument("--max-minutes", type=float, default=None,
                        help="Stop starting new lookups after this many minutes")
    parser.add_argument("--full-history", action="store_true",
                        help="Wipe ITAD price_changes rows and look every game up again")
    args = parser.parse_args(argv)

    setup_logging()
    if not ITAD_API_KEY:
        logger.error("ITAD_API_KEY is not set")
        raise SystemExit(1)
    if not check_db():
        raise SystemExit(1)
    summary = run(
        limit=args.limit,
        full_history=args.full_history,
        max_seconds=args.max_minutes * 60 if args.max_minutes else None,
    )
    logger.info(summary.line())


if __name__ == "__main__":
    main()
