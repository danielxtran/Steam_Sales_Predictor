from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """
    Timezone-aware UTC datetime on every backend.

    SQLite has no timezone support, so values are normalised to UTC on the way in
    and tagged as UTC on the way out. Postgres stores them as timestamptz.
    """
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Game(Base):
    """Static metadata for a Steam game — updated on each collection run."""
    __tablename__ = "games"

    app_id = Column(Integer, primary_key=True, autoincrement=False)
    title = Column(String, nullable=False)
    developer = Column(String)
    publisher = Column(String)
    release_date = Column(Date)       # None when Steam gives no exact day ("Q1 2027", "Coming soon")
    metacritic_score = Column(Integer)
    total_reviews = Column(Integer)   # Steam recommendations count
    is_free = Column(Boolean, nullable=False, default=False)
    type = Column(String, nullable=False, default="game")   # Steam app type
    created_at = Column(UTCDateTime, nullable=False, default=utcnow)
    updated_at = Column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    # Last full appdetails fetch; None means only the catalog's app ID and name are known.
    details_checked_at = Column(UTCDateTime)


class Genre(Base):
    __tablename__ = "genres"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)


class GameGenre(Base):
    """Many-to-many link between games and genres."""
    __tablename__ = "game_genres"

    app_id = Column(Integer, ForeignKey("games.app_id"), primary_key=True)
    genre_id = Column(Integer, ForeignKey("genres.id"), primary_key=True)


class PriceChange(Base):
    """One row each time a game's price changes — the core time-series table."""
    __tablename__ = "price_changes"
    __table_args__ = (
        UniqueConstraint("app_id", "observed_at", "source", name="uq_price_changes_app_observed_source"),
        Index("ix_price_changes_app_observed", "app_id", "observed_at"),
    )

    id = Column(Integer, primary_key=True)
    app_id = Column(Integer, ForeignKey("games.app_id"), nullable=False)
    observed_at = Column(UTCDateTime, nullable=False)
    initial_cents = Column(Integer)   # full price; None when the source doesn't report it
    final_cents = Column(Integer, nullable=False)   # price actually charged
    discount_pct = Column(Integer, nullable=False, default=0)   # 0–100
    source = Column(String, nullable=False)   # "steam" or "itad"


class PollState(Base):
    """When each game was last checked and when it is due next."""
    __tablename__ = "poll_state"
    __table_args__ = (Index("ix_poll_state_next_check_at", "next_check_at"),)

    app_id = Column(Integer, ForeignKey("games.app_id"), primary_key=True)
    tier = Column(String, nullable=False, default="normal")   # "hot" or "normal"
    last_checked_at = Column(UTCDateTime)
    next_check_at = Column(UTCDateTime)
    last_error = Column(String)
    # Last time ITAD history was looked up (found or not); None means never tried.
    itad_checked_at = Column(UTCDateTime)


class ITADGame(Base):
    """Maps Steam app_id to IsThereAnyDeal game ID for history lookups."""
    __tablename__ = "itad_games"

    app_id = Column(Integer, ForeignKey("games.app_id"), primary_key=True)
    itad_id = Column(String, nullable=False)
    slug = Column(String)


class SaleCalendar(Base):
    """Known Steam-wide sale events (seasonal sales, fests)."""
    __tablename__ = "sale_calendar"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    starts_on = Column(Date, nullable=False)
    ends_on = Column(Date)
    kind = Column(String)   # e.g. "seasonal" or "fest"
