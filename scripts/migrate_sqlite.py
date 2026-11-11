#!/usr/bin/env python3
"""
Copy data from the old (pre-Alembic) SQLite database into the Phase 0 schema.

Usage
-----
  alembic upgrade head                             # create tables in DATABASE_URL first
  python scripts/migrate_sqlite.py data/old_copy.db

The old file is opened read-only. Output goes to whatever DATABASE_URL points to,
which must already be migrated and must not contain any games yet.
"""
import argparse
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, insert, inspect, select
from sqlalchemy.engine import Engine

from collector import settings
from collector.db.models import (
    Game,
    GameGenre,
    Genre,
    ITADGame,
    PollState,
    PriceChange,
)
from collector.sources.steam import _compute_discount, parse_release_date

LEGACY_TABLES = ("games", "price_snapshots", "itad_games", "itad_price_history")
CHUNK = 10_000


@dataclass
class TableStats:
    read: int = 0
    written: int = 0
    skipped: Counter = field(default_factory=Counter)


@dataclass
class Summary:
    tables: dict[str, TableStats] = field(default_factory=lambda: defaultdict(TableStats))
    notes: list[str] = field(default_factory=list)

    def __getitem__(self, name: str) -> TableStats:
        return self.tables[name]

    def render(self) -> str:
        lines = [f"{'table':<22}{'read':>10}{'written':>10}{'skipped':>10}  reasons"]
        for name, stats in self.tables.items():
            total = sum(stats.skipped.values())
            reasons = ", ".join(f"{r}: {n}" for r, n in stats.skipped.most_common())
            lines.append(f"{name:<22}{stats.read:>10}{stats.written:>10}{total:>10}  {reasons}")
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)


def to_cents(dollars: float | None) -> int | None:
    """Convert a float dollar amount from the old schema to integer cents (19.99 -> 1999)."""
    return None if dollars is None else round(dollars * 100)


def parse_utc(text: str | None) -> datetime | None:
    """Old timestamps are naive strings written as UTC; return them as aware UTC datetimes."""
    if not text:
        return None
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SystemExit(f"{path} does not exist")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    missing = [t for t in LEGACY_TABLES if t not in tables]
    if missing:
        raise SystemExit(f"{path} is missing old-schema tables: {missing}")
    return conn


def check_target(engine: Engine, old_path: Path) -> None:
    target = engine.url.database
    if engine.url.get_backend_name() == "sqlite" and target and Path(target).resolve() == old_path.resolve():
        raise SystemExit("DATABASE_URL points at the input file; choose a new database for the output")
    if not inspect(engine).has_table(PriceChange.__tablename__):
        raise SystemExit(f"{engine.url} has no schema yet. Run 'alembic upgrade head' first.")
    with engine.connect() as conn:
        if conn.scalar(select(func.count()).select_from(Game.__table__)):
            raise SystemExit(f"{engine.url} already has games; migrate into an empty database")


def _insert(conn, table, rows: list[dict]) -> None:
    for i in range(0, len(rows), CHUNK):
        conn.execute(insert(table), rows[i:i + CHUNK])


def migrate(old_path: Path, engine: Engine) -> Summary:
    check_target(engine, old_path)
    src = open_readonly(old_path)
    summary = Summary()
    now = datetime.now(timezone.utc)

    # First and last time each game was seen, for created_at and last_checked_at.
    seen: dict[int, list[datetime]] = {}
    for app_id, fetched_at in src.execute("SELECT app_id, fetched_at FROM price_snapshots"):
        ts = parse_utc(fetched_at)
        if ts:
            first, last = seen.get(app_id, (ts, ts))
            seen[app_id] = [min(first, ts), max(last, ts)]

    # games, genres, game_genres
    games, links = [], []
    genre_ids: dict[str, int] = {}
    no_exact_date = 0
    for row in src.execute(
        "SELECT app_id, title, developer, publisher, genres, release_date, metacritic_score,"
        " total_reviews, is_free, last_updated FROM games ORDER BY app_id"
    ):
        app_id, title, dev, pub, genres_text, release_text, metacritic, reviews, is_free, updated = row
        summary["games"].read += 1
        updated_at = parse_utc(updated)
        first_seen = seen.get(app_id, [updated_at or now])[0]
        release_date = parse_release_date(release_text)
        if release_date is None:
            no_exact_date += 1
        games.append({
            "app_id": app_id, "title": title, "developer": dev, "publisher": pub,
            "release_date": release_date, "metacritic_score": metacritic,
            "total_reviews": reviews, "is_free": bool(is_free), "type": "game",
            "created_at": first_seen, "updated_at": updated_at or first_seen,
        })

        linked: set[str] = set()
        for name in (n.strip() for n in (genres_text or "").split(",")):
            summary["game_genres"].read += 1
            if not name:
                summary["game_genres"].skipped["empty"] += 1
                continue
            if name in linked:
                summary["game_genres"].skipped["duplicate"] += 1
                continue
            linked.add(name)
            genre_id = genre_ids.setdefault(name, len(genre_ids) + 1)
            links.append({"app_id": app_id, "genre_id": genre_id})

    game_ids = {g["app_id"] for g in games}
    genres = [{"id": i, "name": n} for n, i in genre_ids.items()]
    summary["genres"].read = summary["genres"].written = len(genres)
    summary.notes.append(f"release_date left empty for {no_exact_date} games with no exact day")

    # itad_games
    itad_games = []
    for app_id, itad_id, slug in src.execute("SELECT app_id, itad_id, slug FROM itad_games"):
        summary["itad_games"].read += 1
        if app_id not in game_ids:
            summary["itad_games"].skipped["unknown game"] += 1
            continue
        itad_games.append({"app_id": app_id, "itad_id": itad_id, "slug": slug})

    # poll_state: every game is due now
    poll_state = [
        {
            "app_id": g["app_id"], "tier": "normal",
            "last_checked_at": seen.get(g["app_id"], [None, g["updated_at"]])[1],
            "next_check_at": now, "last_error": None,
        }
        for g in games
    ]
    summary["poll_state"].read = len(games)

    # price_snapshots -> price_changes (steam), keeping only actual changes
    steam_rows = []
    stats = summary["price_changes (steam)"]
    previous: dict[int, tuple] = {}
    for app_id, fetched_at, current, original in src.execute(
        "SELECT app_id, fetched_at, current_price, original_price FROM price_snapshots"
        " ORDER BY app_id, fetched_at, id"
    ):
        stats.read += 1
        observed_at = parse_utc(fetched_at)
        if app_id not in game_ids:
            stats.skipped["unknown game"] += 1
            continue
        if current is None or observed_at is None:
            stats.skipped["no price"] += 1
            continue
        price = (to_cents(original), to_cents(current))
        if previous.get(app_id) == price:
            stats.skipped["unchanged"] += 1
            continue
        previous[app_id] = price
        steam_rows.append({
            "app_id": app_id, "observed_at": observed_at,
            "initial_cents": price[0], "final_cents": price[1],
            # Recomputed from prices; older rows may hold Steam's sometimes-wrong figure.
            "discount_pct": _compute_discount(*price), "source": "steam",
        })

    # itad_price_history -> price_changes (itad), Steam store only
    itad_rows = []
    stats = summary["price_changes (itad)"]
    stats.read = src.execute("SELECT count(*) FROM itad_price_history").fetchone()[0]
    other_shops = src.execute(
        "SELECT count(*) FROM itad_price_history WHERE shop IS NOT 'Steam'"
    ).fetchone()[0]
    if other_shops:
        stats.skipped["not Steam store"] = other_shops
    last_key = None
    for app_id, price, discount, recorded_at in src.execute(
        "SELECT app_id, price, discount_pct, recorded_at FROM itad_price_history"
        " WHERE shop = 'Steam' ORDER BY app_id, recorded_at, id"
    ):
        observed_at = parse_utc(recorded_at)
        if app_id not in game_ids:
            stats.skipped["unknown game"] += 1
            continue
        if price is None or observed_at is None:
            stats.skipped["no price"] += 1
            continue
        key = (app_id, observed_at)
        if key == last_key:
            stats.skipped["duplicate"] += 1
            continue
        last_key = key
        itad_rows.append({
            "app_id": app_id, "observed_at": observed_at,
            # The old table never stored ITAD's regular price, so it is unknown here.
            "initial_cents": None, "final_cents": to_cents(price),
            "discount_pct": discount or 0, "source": "itad",
        })
    src.close()

    # Write everything in one transaction so a failure leaves the target empty.
    with engine.begin() as conn:
        _insert(conn, Game.__table__, games)
        _insert(conn, Genre.__table__, genres)
        _insert(conn, GameGenre.__table__, links)
        _insert(conn, ITADGame.__table__, itad_games)
        _insert(conn, PollState.__table__, poll_state)
        _insert(conn, PriceChange.__table__, steam_rows)
        _insert(conn, PriceChange.__table__, itad_rows)

    summary["games"].written = len(games)
    summary["game_genres"].written = len(links)
    summary["itad_games"].written = len(itad_games)
    summary["poll_state"].written = len(poll_state)
    summary["price_changes (steam)"].written = len(steam_rows)
    summary["price_changes (itad)"].written = len(itad_rows)
    # Print in a stable, readable order.
    order = ["games", "genres", "game_genres", "itad_games", "poll_state",
             "price_changes (steam)", "price_changes (itad)"]
    summary.tables = {name: summary.tables[name] for name in order}
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy the old SQLite database into the Phase 0 schema")
    parser.add_argument("old_db", type=Path, help="Path to the old .db file (opened read-only)")
    args = parser.parse_args()

    print(f"Reading {args.old_db} (read-only) -> writing {settings.engine.url}")
    print(migrate(args.old_db, settings.engine).render())


if __name__ == "__main__":
    main()
