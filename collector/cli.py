#!/usr/bin/env python3
"""
Steam price data collector — command-line entry point.

Run "alembic upgrade head" once before first use to create the tables.

Usage
-----
  python -m collector.cli                  # collect one batch of 200 games
  python -m collector.cli --seed           # first run: seed with top-played games
  python -m collector.cli --limit 500      # collect up to 500 games per run
  python -m collector.cli --no-itad        # skip ITAD history (faster, Steam only)
  python -m collector.cli --schedule       # run daily at 09:00 (runs once immediately too)
"""
import argparse
import logging
import random
import time
from datetime import timezone

import schedule
from sqlalchemy import inspect

from collector.db.models import (
    Game,
    GameGenre,
    Genre,
    ITADGame,
    PollState,
    PriceChange,
    utcnow,
)
from collector.settings import Session, engine
from collector.sources.itad import get_price_history, lookup_game
from collector.sources.steam import (
    get_all_app_ids,
    get_app_details,
    get_top_played_app_ids,
    parse_game,
)
from collector.sources.steamspy import get_top_app_ids

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def check_db() -> bool:
    """Tables are created by Alembic migrations, not by this script."""
    if not inspect(engine).has_table(PriceChange.__tablename__):
        logger.error("Database at %s has no schema. Run 'alembic upgrade head' first.", engine.url)
        return False
    logger.info("Database ready at %s", engine.url)
    return True


def _get_or_create_genre(session, name: str) -> Genre:
    genre = session.query(Genre).filter_by(name=name).one_or_none()
    if genre is None:
        genre = Genre(name=name)
        session.add(genre)
        session.flush()
    return genre


def _upsert_game(session, parsed: dict) -> None:
    game = session.get(Game, parsed["app_id"])
    if game is None:
        game = Game(app_id=parsed["app_id"])
        session.add(game)
    for field in ("title", "developer", "publisher", "release_date",
                  "metacritic_score", "total_reviews", "is_free", "type"):
        setattr(game, field, parsed.get(field))
    game.updated_at = utcnow()
    session.flush()

    session.query(GameGenre).filter_by(app_id=game.app_id).delete()
    for name in dict.fromkeys(parsed.get("genres") or []):
        session.add(GameGenre(app_id=game.app_id, genre_id=_get_or_create_genre(session, name).id))


def _record_price(session, parsed: dict) -> None:
    """Store a price row only if it differs from the last Steam price, and mark the game checked."""
    now = utcnow()
    state = session.get(PollState, parsed["app_id"]) or PollState(app_id=parsed["app_id"])
    state.last_checked_at = now
    state.last_error = None
    session.merge(state)

    if parsed.get("final_cents") is None:
        return  # no price (free, region-locked, unreleased, etc.)
    last = (
        session.query(PriceChange)
        .filter_by(app_id=parsed["app_id"], source="steam")
        .order_by(PriceChange.observed_at.desc())
        .first()
    )
    if last and (last.initial_cents, last.final_cents) == (parsed["initial_cents"], parsed["final_cents"]):
        return
    session.add(PriceChange(
        app_id=parsed["app_id"],
        observed_at=now,
        initial_cents=parsed["initial_cents"],
        final_cents=parsed["final_cents"],
        discount_pct=parsed.get("discount_pct", 0),
        source="steam",
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

    existing = {
        row[0] for row in
        session.query(PriceChange.observed_at).filter_by(app_id=app_id, source="itad")
    }
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


# ---------------------------------------------------------------------------
# Collection logic
# ---------------------------------------------------------------------------

def collect_batch(app_ids: list[int], fetch_itad: bool = True) -> None:
    session = Session()
    saved = skipped = 0
    total = len(app_ids)

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
            _record_price(session, parsed)

            if fetch_itad:
                _fetch_itad_history(session, app_id)

            session.commit()
            saved += 1
            logger.info("[%d/%d] %s (id=%d, discount=%d%%)",
                        i, total, parsed["title"], app_id, parsed["discount_pct"])

    except KeyboardInterrupt:
        logger.info("Interrupted — committing progress")
        session.commit()
    finally:
        session.close()

    logger.info("Batch complete: %d saved, %d skipped", saved, skipped)


def backfill_itad(limit: int = 0, full_history: bool = False) -> None:
    """
    Fetch ITAD history for games that are missing it.

    full_history=True  — wipe existing ITAD price_changes rows and re-fetch
                         everything from 2012 onwards. Use this once after the
                         'since' parameter bug was fixed.
    full_history=False — only process games that have zero history rows (default).
    """
    session = Session()

    if full_history:
        logger.info("Clearing existing ITAD price history for full re-fetch...")
        session.query(PriceChange).filter_by(source="itad").delete()
        session.commit()
        all_game_ids = [row[0] for row in session.query(Game.app_id).all()]
    else:
        have_history = {
            row[0] for row in
            session.query(PriceChange.app_id).filter_by(source="itad").distinct().all()
        }
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

    if not check_db():
        raise SystemExit(1)
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
