#!/usr/bin/env python3
"""
Steam price data collector — Phase 1 entry point.

Usage
-----
  python collect.py                  # collect one batch of 200 games
  python collect.py --seed           # first run: seed with top-played games
  python collect.py --limit 500      # collect up to 500 games per run
  python collect.py --no-itad        # skip ITAD history (faster, Steam only)
  python collect.py --schedule       # run daily at 09:00 (runs once immediately too)
"""
import argparse
import logging
import random
import time
from datetime import datetime

import schedule

from collectors.itad import get_price_history, lookup_game
from collectors.steam import get_all_app_ids, get_app_details, get_top_played_app_ids, parse_game
from collectors.steamspy import get_top_app_ids
from config import Session, engine
from db.models import Base, Game, ITADGame, ITADPriceHistory, PriceSnapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def init_db() -> None:
    Base.metadata.create_all(engine)
    logger.info("Database ready at %s", engine.url)


def _upsert_game(session, parsed: dict) -> None:
    game = session.get(Game, parsed["app_id"]) or Game(app_id=parsed["app_id"])
    for field in ("title", "developer", "publisher", "genres", "release_date",
                  "metacritic_score", "total_reviews", "is_free"):
        setattr(game, field, parsed.get(field))
    game.last_updated = datetime.utcnow()
    session.merge(game)


def _add_price_snapshot(session, parsed: dict) -> None:
    if parsed.get("current_price") is None and not parsed.get("is_free"):
        return  # price data unavailable (region-locked, unreleased, etc.)
    session.add(PriceSnapshot(
        app_id=parsed["app_id"],
        current_price=parsed.get("current_price"),
        original_price=parsed.get("original_price"),
        discount_pct=parsed.get("discount_pct", 0),
        on_sale=parsed.get("on_sale", False),
    ))


def _fetch_itad_history(session, app_id: int) -> None:
    itad_record = session.get(ITADGame, app_id)
    if not itad_record:
        game_info = lookup_game(app_id)
        if not game_info:
            return
        itad_record = ITADGame(
            app_id=app_id,
            itad_id=game_info["id"],
            slug=game_info.get("slug"),
        )
        session.merge(itad_record)

    for entry in get_price_history(itad_record.itad_id):
        session.add(ITADPriceHistory(
            app_id=app_id,
            shop=entry["shop"],
            price=entry["price"],
            discount_pct=entry["discount_pct"],
            recorded_at=entry["recorded_at"],
        ))


# ---------------------------------------------------------------------------
# Collection logic
# ---------------------------------------------------------------------------

def collect_batch(app_ids: list[int], fetch_itad: bool = True) -> None:
    session = Session()
    saved = skipped = 0

    try:
        for i, app_id in enumerate(app_ids, start=1):
            raw = get_app_details(app_id)
            if raw is None:
                skipped += 1
                continue

            parsed = parse_game(app_id, raw)
            if parsed is None:
                skipped += 1
                continue

            _upsert_game(session, parsed)
            _add_price_snapshot(session, parsed)

            if fetch_itad:
                _fetch_itad_history(session, app_id)

            session.commit()
            saved += 1
            logger.info("[%d/%d] %s (id=%d, discount=%d%%)",
                        i, len(app_ids), parsed["title"], app_id, parsed["discount_pct"])

    except KeyboardInterrupt:
        logger.info("Interrupted — committing progress")
        session.commit()
    finally:
        session.close()

    logger.info("Batch complete: %d saved, %d skipped", saved, skipped)


def backfill_itad(limit: int = 0, full_history: bool = False) -> None:
    """
    Fetch ITAD history for games that are missing it.

    full_history=True  — wipe existing itad_price_history rows and re-fetch
                         everything from 2012 onwards. Use this once after the
                         'since' parameter bug was fixed.
    full_history=False — only process games that have zero history rows (default).
    """
    session = Session()

    if full_history:
        logger.info("Clearing existing ITAD price history for full re-fetch...")
        session.query(ITADPriceHistory).delete()
        session.commit()
        all_game_ids = [row[0] for row in session.query(Game.app_id).all()]
    else:
        have_history = {row[0] for row in session.query(ITADPriceHistory.app_id).distinct().all()}
        all_game_ids = [row[0] for row in session.query(Game.app_id).all()]
        all_game_ids = [aid for aid in all_game_ids if aid not in have_history]

    session.close()

    if limit:
        all_game_ids = all_game_ids[:limit]

    logger.info("=== ITAD back-fill: %d games to process ===", len(all_game_ids))
    session = Session()
    done = 0
    try:
        for i, app_id in enumerate(all_game_ids, start=1):
            _fetch_itad_history(session, app_id)
            session.commit()
            done += 1
            if i % 25 == 0:
                logger.info("Back-fill progress: %d/%d", i, len(all_game_ids))
    except KeyboardInterrupt:
        logger.info("Interrupted — committing progress")
        session.commit()
    finally:
        session.close()
    logger.info("Back-fill complete: %d games processed", done)


def _get_unprocessed_ids(limit: int, popular: bool = False) -> list[int]:
    """
    Return app IDs not yet in our DB.

    popular=True  → prioritise by SteamSpy player-count rank (top ~5000 games first)
    popular=False → random sample from the full Steam catalogue
    """
    session = Session()
    existing = {row[0] for row in session.query(Game.app_id).all()}
    session.close()

    if popular:
        all_ids = get_top_app_ids(pages=5)
        # preserve SteamSpy rank order — most popular first
        new_ids = [aid for aid in all_ids if aid not in existing]
    else:
        all_ids = get_all_app_ids()
        new_ids = [aid for aid in all_ids if aid not in existing]
        random.shuffle(new_ids)

    return new_ids[:limit]


def run(limit: int = 200, seed: bool = False, popular: bool = False, fetch_itad: bool = True) -> None:
    logger.info("=== Collection run started (limit=%d, seed=%s, popular=%s) ===", limit, seed, popular)

    if seed:
        app_ids = get_top_played_app_ids()
        logger.info("Seeding with %d top-played games", len(app_ids))
    else:
        app_ids = _get_unprocessed_ids(limit, popular=popular)
        logger.info("%d new app IDs to process", len(app_ids))

    if not app_ids:
        logger.info("Nothing to collect")
        return

    collect_batch(app_ids[:limit], fetch_itad=fetch_itad)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Steam price data collector")
    parser.add_argument("--seed", action="store_true",
                        help="Seed DB with top-played games (use on first run)")
    parser.add_argument("--schedule", action="store_true",
                        help="Keep running and collect daily at 09:00")
    parser.add_argument("--limit", type=int, default=200,
                        help="Max games to process per run (default: 200)")
    parser.add_argument("--popular", action="store_true",
                        help="Prioritise top games by SteamSpy player count instead of random sampling")
    parser.add_argument("--no-itad", action="store_true",
                        help="Skip ITAD history fetching")
    parser.add_argument("--backfill-itad", action="store_true",
                        help="Fetch ITAD history for games already in DB that have none")
    parser.add_argument("--full-history", action="store_true",
                        help="With --backfill-itad: wipe and re-fetch all history from 2012 (fixes the missing 'since' param)")
    args = parser.parse_args()

    init_db()
    fetch_itad = not args.no_itad

    if args.backfill_itad:
        backfill_itad(
            limit=args.limit if args.limit != 200 else 0,
            full_history=args.full_history,
        )
        return

    if args.schedule:
        logger.info("Scheduling daily run at 09:00 — press Ctrl+C to stop")
        schedule.every().day.at("09:00").do(
            run, limit=args.limit, seed=False, popular=args.popular, fetch_itad=fetch_itad
        )
        run(limit=args.limit, seed=args.seed, popular=args.popular, fetch_itad=fetch_itad)
        while True:
            schedule.run_pending()
            time.sleep(60)
    else:
        run(limit=args.limit, seed=args.seed, popular=args.popular, fetch_itad=fetch_itad)


if __name__ == "__main__":
    main()
