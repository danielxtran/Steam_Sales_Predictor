#!/usr/bin/env python3
"""
Catalog job: add new Steam games and decide which games are "hot".

Usage
-----
  python -m collector.jobs.catalog

Fetches the full game list from IStoreService/GetAppList (needs STEAM_API_KEY) and adds
any new games to games and poll_state, due for a check right away. Games in SteamSpy's
top-100 lists or Steam's featured categories are marked hot; everything else is normal.
"""
import argparse
import logging

from sqlalchemy import insert, or_, select, update

from collector.db.models import Game, PollState, utcnow
from collector.jobs.common import RunSummary, check_db, setup_logging
from collector.settings import Session
from collector.sources.steam import SteamError, get_app_list, get_featured_app_ids
from collector.sources.steamspy import SteamSpyError, get_top_list_app_ids

logger = logging.getLogger(__name__)

INSERT_CHUNK = 5_000


def add_new_games(session, apps: list[dict], summary: RunSummary) -> int:
    known = set(session.scalars(select(Game.app_id)))
    now = utcnow()
    new = [a for a in apps if a["appid"] not in known]
    for i in range(0, len(new), INSERT_CHUNK):
        chunk = new[i:i + INSERT_CHUNK]
        session.execute(insert(Game), [
            {"app_id": a["appid"], "title": a.get("name") or f"App {a['appid']}", "type": "game",
             "is_free": False, "created_at": now, "updated_at": now}
            for a in chunk
        ])
        session.execute(insert(PollState), [
            {"app_id": a["appid"], "tier": "normal", "next_check_at": now} for a in chunk
        ])
    summary.checked = len(apps)
    summary.extra["new"] = len(new)
    return len(new)


def collect_hot_ids(summary: RunSummary) -> tuple[set[int], bool]:
    """Union of the hot sources, and whether every source answered."""
    hot: set[int] = set()
    complete = True
    for name, fetch, errors in (
        ("SteamSpy top lists", get_top_list_app_ids, SteamSpyError),
        ("Steam featured categories", get_featured_app_ids, SteamError),
    ):
        try:
            ids = fetch()
        except errors as exc:
            logger.warning("%s unavailable: %s", name, exc)
            summary.failed += 1
            complete = False
            continue
        logger.info("%s: %d app IDs", name, len(ids))
        hot.update(ids)
    return hot, complete


def update_tiers(session, hot_ids: set[int], demote: bool) -> tuple[int, int]:
    """
    Mark hot games and make newly hot ones due now. Demote games that left the hot
    sources only when every source answered, so an outage can't demote everything.
    """
    now = utcnow()
    current_hot = set(session.scalars(select(PollState.app_id).where(PollState.tier == "hot")))
    known = set(session.scalars(select(PollState.app_id).where(PollState.app_id.in_(hot_ids))))
    promote = known - current_hot
    demoted = current_hot - hot_ids if demote else set()

    if promote:
        session.execute(update(PollState).where(PollState.app_id.in_(promote)).values(tier="hot"))
        session.execute(
            update(PollState)
            .where(PollState.app_id.in_(promote))
            .where(or_(PollState.next_check_at.is_(None), PollState.next_check_at > now))
            .values(next_check_at=now)
        )
    if demoted:
        session.execute(update(PollState).where(PollState.app_id.in_(demoted)).values(tier="normal"))
    return len(promote), len(demoted)


def run(session_factory=Session) -> RunSummary:
    summary = RunSummary("catalog")
    with session_factory() as session:
        try:
            apps = get_app_list()
        except SteamError as exc:
            logger.error("Could not fetch the app list: %s", exc)
            summary.failed += 1
            summary.extra["app_list"] = "failed"
            apps = None
        new = add_new_games(session, apps, summary) if apps else 0
        session.commit()

        hot_ids, complete = collect_hot_ids(summary)
        promoted, demoted = update_tiers(session, hot_ids, demote=complete)
        session.commit()

        summary.changed = new + promoted + demoted
        summary.extra.update(hot=len(hot_ids), promoted=promoted, demoted=demoted)
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m collector.jobs.catalog", description="Add new Steam games and update hot tiers")
    parser.parse_args(argv)

    setup_logging()
    if not check_db():
        raise SystemExit(1)
    summary = run()
    logger.info(summary.line())
    if summary.extra.get("app_list") == "failed":
        raise SystemExit(1)  # make the workflow run show as failed


if __name__ == "__main__":
    main()
