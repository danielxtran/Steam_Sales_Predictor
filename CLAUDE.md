# CLAUDE.md

Guidance for Claude (and any other contributor) working in this repository.

## What this project is

A Steam price tracker, similar in spirit to SteamDB, that will add machine-learning
predictions about sales:

- when a game's next sale will start,
- how deep the next discount will be,
- when a discount that has been stuck at one level will deepen,
- how long until the game reaches 75% off.

Later phases add a Next.js website, a FastAPI API, Steam sign-in, wishlist sync,
and email alerts.

**Current phase: Phase 0 — reliable data collection.** Do not start ML, API, or
website work unless a task explicitly asks for it.

## Fixed decisions

These are settled. Do not change them without being asked; record any change in
`docs/decisions.md`.

- **Python 3.12.**
- **SQLAlchemy** for all database access.
- **US prices only** — always request Steam data with `cc=us` (ITAD with `country=US`).
- **Free tiers only** — no paid services, APIs, or hosting.
- **GitHub Actions** runs scheduled jobs (collection, backfills).
- **Database:** Neon Postgres in production, SQLite locally. The choice is made by a
  single `DATABASE_URL` setting; code must work with both.
- **Price history backfill** comes from the IsThereAnyDeal (ITAD) API.
- **Do not scrape SteamDB.**

## Data rules

- **Money is stored as integer cents** (e.g. `1999`, never `19.99`).
- **Store a price row only when the price changes.** Track a separate
  last-checked time per game so we know a price was confirmed unchanged.
- **From ITAD, keep only Steam-store history.** Discard other shops.

## Working rules

- One branch and one focused change per task. Small, clearly named commits.
- Never read, print, or commit `.env` or any API key. Use `.env.example` to learn
  which settings exist.
- Never commit anything in `data/` or any `.db` file.
- Never modify or delete `data/steam_prices.db`. If you need real data, work on a
  copy outside the repo (or a gitignored path) and point `DATABASE_URL` at the copy.
- Tests must never call real APIs. Use saved sample responses in `tests/fixtures/`.
- Before finishing a task, run the linter and the tests and fix what fails:
  ```bash
  ruff check .
  pytest
  ```
  Install them with `pip install -e ".[dev]"` (use a `.venv`). CI runs both.
- Add one line to `CHANGELOG.md` for each change.
- Add a dated line to `docs/decisions.md` for any design decision.
- When done, summarize what changed and why in plain language for a learner, and
  list how the user can check it themselves.

## Current files

| Path | What it does |
| --- | --- |
| `collector/cli.py` | CLI entry point (`python -m collector.cli`). Checks the schema exists, picks app IDs (seed / SteamSpy popular / random), fetches Steam details, upserts games and genres, writes a `price_changes` row only when the price changed, updates `poll_state`, optionally fetches Steam-store ITAD history. Flags: `--seed`, `--limit`, `--popular`, `--no-itad`, `--backfill-itad`, `--full-history`, `--schedule`. |
| `collector/settings.py` | Loads `.env`, exposes `STEAM_API_KEY` / `ITAD_API_KEY`, reads `DATABASE_URL` (default `sqlite:///data/steam_prices.db`; relative SQLite paths resolve from the project root), builds the SQLAlchemy `engine` and `Session`. |
| `collector/db/models.py` | SQLAlchemy tables: `games`, `genres`, `game_genres`, `price_changes`, `poll_state`, `itad_games`, `sale_calendar`; `UTCDateTime` type. Documented in `docs/data.md`. |
| `alembic.ini`, `migrations/` | Alembic config and migrations. `env.py` reads `DATABASE_URL` from settings and refuses to run on an old-schema database. Create tables with `alembic upgrade head`. |
| `scripts/migrate_sqlite.py` | One-off copy of an old-schema SQLite file (opened read-only) into the database at `DATABASE_URL`. |
| `collector/sources/steam.py` | Steam Store / Web API client: featured-category seed list, full app list, `appdetails` fetch with rate-limit delay and retries, `parse_game()` to normalize a response (prices in cents), `parse_release_date()`. |
| `collector/sources/itad.py` | ITAD API client: `lookup_game()` (Steam app ID → ITAD game) and `get_price_history()` (prices in cents). |
| `collector/sources/steamspy.py` | SteamSpy client returning popular app IDs ranked by player count. |
| `tests/` | pytest tests. `conftest.py` blocks HTTP and uses an in-memory DB; `fixtures/` holds saved API responses. |
| `notebooks/data_exploration.ipynb` | Exploratory analysis and draft ML features from the local DB. |
| `pyproject.toml` | Package metadata, pinned runtime dependencies, `notebooks` and `dev` extras, ruff and pytest config. |
| `.github/workflows/ci.yml` | Runs ruff and pytest on pull requests and pushes to `main`. |
| `.env.example` | Template listing the settings `.env` should define. |
| `data/` | Local SQLite databases (gitignored). `steam_prices.db` is the old-schema original: never touch it. `v2.db` is the migrated Phase 0 database. |
| `CHANGELOG.md` | One line per change. |
| `docs/decisions.md` | Dated log of design decisions. |
| `docs/data.md` | Every table and column, and where its data comes from. |

## Known gaps between current code and the rules above

The existing code predates some fixed decisions. Fix these only in tasks that ask
for them, one at a time:

- `DATABASE_URL` is read, but no Postgres driver is installed yet, so a Neon URL
  will not work until one is added.
- `poll_state.tier` and `next_check_at` are stored but not used: the collector still
  picks only games it hasn't seen yet.
- `sale_calendar` exists but nothing fills it.
- `notebooks/data_exploration.ipynb` still queries the old tables
  (`price_snapshots`, `itad_price_history`) and needs updating for the new schema.
- ITAD rows migrated from the old DB have no `initial_cents` and timestamps that may
  be off by ITAD's UTC offset (see `docs/data.md`).
- Scheduling uses the `schedule` library in a long-running loop, not GitHub Actions.
- `Optional[...]` type hints could be modernized to `X | None`.
