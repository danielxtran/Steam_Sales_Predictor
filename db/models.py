from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Game(Base):
    """Static metadata for a Steam game — updated on each collection run."""
    __tablename__ = "games"

    app_id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    developer = Column(String)
    publisher = Column(String)
    genres = Column(String)           # comma-separated list
    release_date = Column(String)
    metacritic_score = Column(Integer)
    total_reviews = Column(Integer)   # Steam recommendations count
    is_free = Column(Boolean, default=False)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PriceSnapshot(Base):
    """One price reading per game per collection run — the core time-series table."""
    __tablename__ = "price_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    app_id = Column(Integer, nullable=False, index=True)
    fetched_at = Column(DateTime, default=datetime.utcnow, index=True)
    current_price = Column(Float)     # USD, None means not available / region-locked
    original_price = Column(Float)    # full price before any discount
    discount_pct = Column(Integer, default=0)   # 0–100
    on_sale = Column(Boolean, default=False)


class ITADGame(Base):
    """Maps Steam app_id to IsThereAnyDeal game ID for history lookups."""
    __tablename__ = "itad_games"

    app_id = Column(Integer, primary_key=True)
    itad_id = Column(String, nullable=False)
    slug = Column(String)


class ITADPriceHistory(Base):
    """Historical price points from ITAD across all stores."""
    __tablename__ = "itad_price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    app_id = Column(Integer, nullable=False, index=True)
    shop = Column(String)
    price = Column(Float)             # USD
    discount_pct = Column(Integer)
    recorded_at = Column(DateTime)    # when this price was active according to ITAD
    fetched_at = Column(DateTime, default=datetime.utcnow)
