import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

STEAM_API_KEY: str = os.getenv("STEAM_API_KEY", "")
ITAD_API_KEY: str = os.getenv("ITAD_API_KEY", "")

DB_PATH = Path(__file__).parent / "data" / "steam_prices.db"
DB_PATH.parent.mkdir(exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
Session = sessionmaker(bind=engine)
