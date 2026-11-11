"""Helpers shared by the collection jobs."""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import func, inspect, select

from collector.db.models import Game, GameGenre, Genre, PriceChange
from collector.settings import engine
from collector.sources.steam import parse_game

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def check_db() -> bool:
    """Tables are created by Alembic migrations, not by the jobs."""
    if not inspect(engine).has_table(PriceChange.__tablename__):
        logger.error("Database at %s has no schema. Run 'alembic upgrade head' first.", engine.url)
        return False
    return True


@dataclass
class RunSummary:
    """Counts for the one-line summary each job logs when it finishes."""
    job: str
    checked: int = 0
    changed: int = 0
    failed: int = 0
    extra: dict = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)

    def line(self) -> str:
        extras = "".join(f" {k}={v}" for k, v in self.extra.items())
        elapsed = time.monotonic() - self.started
        return (f"{self.job}: checked={self.checked} changed={self.changed} "
                f"failed={self.failed}{extras} in {elapsed:.1f}s")


def _get_or_create_genre(session, name: str) -> Genre:
    genre = session.query(Genre).filter_by(name=name).one_or_none()
    if genre is None:
        genre = Genre(name=name)
        session.add(genre)
        session.flush()
    return genre


def apply_details(session, game: Game, raw: dict, now: datetime) -> Optional[dict]:
    """
    Update a game's metadata and genres from a full appdetails response.
    Returns the parsed fields, or None if the app is not a game (its type is still saved
    so the price job stops picking it).
    """
    game.details_checked_at = now
    parsed = parse_game(game.app_id, raw)
    if parsed is None:
        game.type = raw.get("type") or "unknown"
        return None
    for name in ("title", "developer", "publisher", "release_date",
                 "metacritic_score", "total_reviews", "is_free", "type"):
        setattr(game, name, parsed.get(name))
    game.updated_at = now
    session.flush()

    session.query(GameGenre).filter_by(app_id=game.app_id).delete()
    for name in dict.fromkeys(parsed.get("genres") or []):
        session.add(GameGenre(app_id=game.app_id, genre_id=_get_or_create_genre(session, name).id))
    return parsed


def latest_prices(session, app_ids: list[int]) -> dict[int, PriceChange]:
    """The most recent price_changes row (from any source) for each game, in one query."""
    if not app_ids:
        return {}
    rank = func.row_number().over(
        partition_by=PriceChange.app_id,
        order_by=(PriceChange.observed_at.desc(), PriceChange.id.desc()),
    ).label("rank")
    ranked = select(PriceChange.id, rank).where(PriceChange.app_id.in_(app_ids)).subquery()
    rows = session.scalars(select(PriceChange).join(ranked, PriceChange.id == ranked.c.id).where(ranked.c.rank == 1))
    return {row.app_id: row for row in rows}


def record_price(session, app_id: int, price: Optional[dict], now: datetime,
                 last: Optional[PriceChange]) -> bool:
    """
    Insert a Steam price row if initial_cents, final_cents, or discount_pct differ from
    `last`, the game's latest price_changes row. Returns True if a row was added.
    """
    if price is None:
        return False  # no price: free, or not sold right now
    fields = ("initial_cents", "final_cents", "discount_pct")
    if last and all(getattr(last, f) == price[f] for f in fields):
        return False
    session.add(PriceChange(app_id=app_id, observed_at=now, source="steam",
                            **{f: price[f] for f in fields}))
    return True
