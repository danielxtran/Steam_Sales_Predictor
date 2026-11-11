# Steam Sales Predictor

A Steam price tracker that records US prices for Steam games and will use that
history to predict sales: when the next sale starts, how deep the discount will be,
when a stuck discount deepens, and how long until a game hits 75% off.

Current status: **Phase 0 — data collection.** The collector pulls game details and
prices from the Steam store, popular-game lists from SteamSpy, and historical prices
from [IsThereAnyDeal](https://isthereanydeal.com/). A website, API, and alerts come
later.

## Setup

Requires Python 3.12.

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -e ".[dev,notebooks]"
cp .env.example .env
```

Then fill in `.env`:

- `ITAD_API_KEY` — needed for price history. Get one at <https://isthereanydeal.com/dev/app/>.
- `STEAM_API_KEY` — optional; lets the full app list be filtered to games only.
- `DATABASE_URL` — leave blank to use the local SQLite file `data/steam_prices.db`.
  Relative SQLite paths like `sqlite:///data/v2.db` are resolved from the project root.

Create the tables:

```bash
alembic upgrade head
```

### Moving data from the old database

Databases made before the Alembic schema can't be upgraded in place. Copy one into a
new database instead (the old file is only read):

```bash
# with DATABASE_URL=sqlite:///data/v2.db in .env
alembic upgrade head
python scripts/migrate_sqlite.py data/old_copy.db
```

## Running the collector

```bash
python -m collector.cli --help             # list all options
python -m collector.cli --seed             # first run: seed with top-played games
python -m collector.cli --limit 500        # collect up to 500 new games
python -m collector.cli --popular          # pick games by SteamSpy popularity
python -m collector.cli --no-itad          # Steam prices only (faster)
python -m collector.cli --backfill-itad    # fetch ITAD history for games missing it
```

## Running tests and lint

```bash
pytest
ruff check .
```

Tests use saved API responses in `tests/fixtures/` and never call real APIs.
CI runs both on every pull request and on pushes to `main`.

## Layout

```
collector/
  cli.py          command-line entry point
  settings.py     reads .env and DATABASE_URL, creates the database engine
  db/models.py    database tables (see docs/data.md)
  sources/        Steam, SteamSpy, and IsThereAnyDeal API clients
migrations/       Alembic migrations
scripts/          one-off scripts (old database migration)
tests/            pytest tests and fixtures
notebooks/        exploratory analysis
docs/data.md      tables, columns, and data sources
docs/decisions.md design decision log
```
