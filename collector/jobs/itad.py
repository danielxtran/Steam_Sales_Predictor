#!/usr/bin/env python3
"""
ITAD job: backfill Steam-store price history from IsThereAnyDeal.

Usage
-----
  python -m collector.jobs.itad                   # games with no ITAD history yet
  python -m collector.jobs.itad --limit 100
  python -m collector.jobs.itad --full-history    # wipe ITAD rows and re-fetch everything
"""
import argparse
import logging
from datetime import timezone

from collector.db.models import Game, ITADGame, PriceChange
from collector.jobs.common import RunSummary, check_db, setup_logging
from collector.settings import Session
from collector.sources.itad import get_price_history, lookup_game

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


def run(limit: int = 0, full_history: bool = False, session_factory=Session) -> RunSummary:
    """
    Fetch ITAD history for games that are missing it.

    full_history=True  — wipe existing ITAD price_changes rows and re-fetch everything.
    full_history=False — only process games that have no ITAD rows (default).
    """
    summary = RunSummary("itad")
    with session_factory() as session:
        if full_history:
            logger.info("Clearing existing ITAD price history for full re-fetch...")
            session.query(PriceChange).filter_by(source="itad").delete()
            session.commit()
        have_history = {
            row[0] for row in
            session.query(PriceChange.app_id).filter_by(source="itad").distinct().all()
        }
        app_ids = [row[0] for row in session.query(Game.app_id).all() if row[0] not in have_history]
        if limit:
            app_ids = app_ids[:limit]

        logger.info("ITAD back-fill: %d games to process", len(app_ids))
        for i, app_id in enumerate(app_ids, start=1):
            if fetch_itad_history(session, app_id):
                summary.changed += 1
            session.commit()
            summary.checked += 1
            if i % 25 == 0:
                logger.info("Back-fill progress: %d/%d", i, len(app_ids))
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m collector.jobs.itad", description="Backfill Steam-store price history from ITAD")
    parser.add_argument("--limit", type=int, default=0, help="Max games to process (default: all)")
    parser.add_argument("--full-history", action="store_true",
                        help="Wipe ITAD price_changes rows and re-fetch all history")
    args = parser.parse_args(argv)

    setup_logging()
    if not check_db():
        raise SystemExit(1)
    logger.info(run(limit=args.limit, full_history=args.full_history).line())


if __name__ == "__main__":
    main()
