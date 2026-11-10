import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

STEAM_API_KEY: str = os.getenv("STEAM_API_KEY", "")
ITAD_API_KEY: str = os.getenv("ITAD_API_KEY", "")

# Same file as sqlite:///data/steam_prices.db, but anchored to the project root so
# it resolves correctly no matter which directory the code is run from.
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "steam_prices.db"
DATABASE_URL: str = os.getenv("DATABASE_URL") or f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"

if not os.getenv("DATABASE_URL"):
    DEFAULT_DB_PATH.parent.mkdir(exist_ok=True)

engine = create_engine(DATABASE_URL, echo=False)
Session = sessionmaker(bind=engine)
