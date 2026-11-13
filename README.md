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
- `STEAM_API_KEY` — needed by the catalog job to fetch the full game list. Get one at <https://steamcommunity.com/dev/apikey>.
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

Collection is split into jobs. Each ends by logging a one-line summary
(`checked=... changed=... failed=... in ...s`).

```bash
python -m collector.jobs.catalog            # add new games, mark hot games (daily)
python -m collector.jobs.prices --limit 50  # re-check due games, save price changes
python -m collector.jobs.itad --limit 100   # backfill Steam-store history from ITAD (once per game)
python -m collector.cli prices --limit 50   # same jobs through one entry point
```

The prices job checks due games hot tier first. Prices are fetched in batches of 100
per call, and full details are fetched only for new games or games whose details are
over 30 days old (`--details-limit` per run). Requests are spaced to stay under
Steam's limit of about 200 per 5 minutes, and the job backs off on HTTP 429. Hot games
are re-checked every 6 hours, normal games every 2 days. See `docs/data.md`.

### Scheduled runs (GitHub Actions)

`.github/workflows/catalog.yml` runs daily, `.github/workflows/prices.yml` every
3 hours, and `.github/workflows/itad.yml` daily (up to 300 ITAD lookups per run,
most popular games first, until every game has been looked up once). All three run `alembic upgrade head` first and need these repository secrets
(Settings > Secrets and variables > Actions): `DATABASE_URL` (the Neon Postgres URL),
`STEAM_API_KEY`, and `ITAD_API_KEY`. To run one now: Actions tab > pick the workflow >
**Run workflow**.

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
  jobs/           catalog, prices, and itad jobs
  sources/        Steam, SteamSpy, and IsThereAnyDeal API clients
migrations/       Alembic migrations
scripts/          one-off scripts (old database migration, appdetails experiment)
tests/            pytest tests and fixtures
notebooks/        exploratory analysis
docs/data.md      tables, columns, and data sources
docs/decisions.md design decision log
```
