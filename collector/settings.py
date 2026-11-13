import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

STEAM_API_KEY: str = os.getenv("STEAM_API_KEY", "")
ITAD_API_KEY: str = os.getenv("ITAD_API_KEY", "")


def resolve_database_url(url: str | None) -> str:
    """
    Return the SQLAlchemy URL to use, defaulting to sqlite:///data/steam_prices.db.

    Relative SQLite paths are anchored to the project root so they resolve to the same
    file no matter which directory the code is run from (e.g. notebooks/).
    Plain postgresql:// URLs (as Neon gives them) use the psycopg 3 driver.
    """
    url = url or "sqlite:///data/steam_prices.db"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    parsed = make_url(url)
    if parsed.drivername == "postgresql":
        parsed = parsed.set(drivername="postgresql+psycopg")
    if parsed.get_backend_name() == "sqlite" and parsed.database and parsed.database != ":memory:":
        path = Path(parsed.database)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
            path.parent.mkdir(exist_ok=True)
            parsed = parsed.set(database=path.as_posix())
    return parsed.render_as_string(hide_password=False)


DATABASE_URL: str = resolve_database_url(os.getenv("DATABASE_URL"))

engine = create_engine(DATABASE_URL, echo=False)
# Jobs commit often (every batch) and keep using the rows they loaded. Without
# expire_on_commit=False, every commit would make the next access re-read each row,
# one query per row, which is slow over the network to Neon.
Session = sessionmaker(bind=engine, expire_on_commit=False)
