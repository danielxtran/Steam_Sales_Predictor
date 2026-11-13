#!/usr/bin/env python3
"""
Price job: re-check games that are due and save only price changes.

Usage
-----
  python -m collector.jobs.prices                     # check up to 500 due games
  python -m collector.jobs.prices --limit 50
  python -m collector.jobs.prices --max-minutes 10    # stop starting new requests after 10 min

Due games come from poll_state (next_check_at <= now), hot tier first. Prices are fetched
in batches of up to 100 per appdetails call. Games that are new or whose details are over
30 days old get one full appdetails call instead (up to --details-limit per run), which
also refreshes title, genres, and so on.
"""
import argparse
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import case, or_, select

from collector.db.models import Game, PollState, utcnow
from collector.jobs.common import (
    RunSummary,
    apply_details,
    check_db,
    insert_price_rows,
    latest_prices,
    price_change_row,
    setup_logging,
)
from collector.settings import Session
from collector.sources.steam import (
    PRICE_BATCH_SIZE,
    SteamError,
    get_app_details,
    get_prices,
    parse_price,
)

logger = logging.getLogger(__name__)

CHECK_INTERVALS = {"hot": timedelta(hours=6), "normal": timedelta(days=2)}
DETAILS_MAX_AGE = timedelta(days=30)


def next_check_at(tier: str, now: datetime) -> datetime:
    return now + CHECK_INTERVALS.get(tier, CHECK_INTERVALS["normal"])


def due_games(session, now: datetime, limit: int) -> list[tuple[PollState, Game]]:
    """Games whose next check is due, hot tier first, then the longest-waiting."""
    query = (
        select(PollState, Game)
        .join(Game, Game.app_id == PollState.app_id)
        .where(Game.type == "game")
        .where(or_(PollState.next_check_at.is_(None), PollState.next_check_at <= now))
        .order_by(
            case((PollState.tier == "hot", 0), else_=1),
            PollState.next_check_at.asc().nulls_first(),
            PollState.app_id,
        )
        .limit(limit)
    )
    return [tuple(row) for row in session.execute(query)]


def needs_details(game: Game, now: datetime) -> bool:
    return game.details_checked_at is None or game.details_checked_at < now - DETAILS_MAX_AGE


def _finish(state: PollState, summary: RunSummary, error: str | None = None) -> None:
    now = utcnow()
    state.last_checked_at = now
    state.next_check_at = next_check_at(state.tier, now)
    state.last_error = error[:500] if error else None
    summary.checked += 1
    if error:
        summary.failed += 1


def _is_rate_limited(exc: SteamError) -> bool:
    return "429" in str(exc)


def run(limit: int = 500, details_limit: int = 50, max_seconds: float | None = None,
        session_factory=Session) -> RunSummary:
    summary = RunSummary("prices")
    deadline = time.monotonic() + max_seconds if max_seconds else None

    def out_of_time() -> bool:
        if deadline and time.monotonic() >= deadline:
            summary.extra["stopped"] = "time limit"
            return True
        return False

    with session_factory() as session:
        now = utcnow()
        due = due_games(session, now, limit)
        detail_jobs = [(s, g) for s, g in due if needs_details(g, now)][:details_limit]
        detail_ids = {g.app_id for _, g in detail_jobs}
        price_jobs = [(s, g) for s, g in due if g.app_id not in detail_ids]
        logger.info("%d games due: %d need details, %d price-only", len(due), len(detail_jobs), len(price_jobs))
        summary.extra["details"] = 0

        # New or stale games: one full call each, which includes the price.
        for state, game in detail_jobs:
            if out_of_time():
                break
            try:
                raw = get_app_details(game.app_id)
            except SteamError as exc:
                _finish(state, summary, str(exc))
                session.commit()
                if _is_rate_limited(exc):
                    summary.extra["stopped"] = "rate limited"
                    break
                continue
            if raw is None:
                _finish(state, summary, "not available in the US store (success=false)")
            else:
                checked_at = utcnow()
                parsed = apply_details(session, game, raw, checked_at)
                summary.extra["details"] += 1
                price = parse_price(raw.get("price_overview"))
                last = latest_prices(session, [game.app_id]).get(game.app_id)
                row = price_change_row(game.app_id, price, checked_at, last) if parsed else None
                if row:
                    insert_price_rows(session, [row])
                    summary.changed += 1
                _finish(state, summary)
            session.commit()

        # Everything else: batched price-only calls.
        for i in range(0, len(price_jobs), PRICE_BATCH_SIZE):
            if summary.extra.get("stopped") or out_of_time():
                break
            batch = price_jobs[i:i + PRICE_BATCH_SIZE]
            try:
                prices = get_prices([g.app_id for _, g in batch])
            except SteamError as exc:
                for state, _ in batch:
                    _finish(state, summary, str(exc))
                session.commit()
                if _is_rate_limited(exc):
                    summary.extra["stopped"] = "rate limited"
                continue
            checked_at = utcnow()
            latest = latest_prices(session, [g.app_id for _, g in batch])
            new_rows = []
            for state, game in batch:
                if game.app_id not in prices:
                    _finish(state, summary, "not available in the US store (success=false)")
                    continue
                row = price_change_row(game.app_id, prices[game.app_id], checked_at, latest.get(game.app_id))
                if row:
                    new_rows.append(row)
                _finish(state, summary)
            insert_price_rows(session, new_rows)
            summary.changed += len(new_rows)
            session.commit()

    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m collector.jobs.prices", description="Re-check due games and save price changes")
    parser.add_argument("--limit", type=int, default=500, help="Max games to check (default: 500)")
    parser.add_argument("--details-limit", type=int, default=50,
                        help="Max full detail fetches for new/stale games (default: 50)")
    parser.add_argument("--max-minutes", type=float, default=None,
                        help="Stop starting new requests after this many minutes")
    args = parser.parse_args(argv)

    setup_logging()
    if not check_db():
        raise SystemExit(1)
    summary = run(
        limit=args.limit,
        details_limit=args.details_limit,
        max_seconds=args.max_minutes * 60 if args.max_minutes else None,
    )
    logger.info(summary.line())


if __name__ == "__main__":
    main()
