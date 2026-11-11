# Design decisions

One dated line per decision, newest at the bottom.

- 2026-11-09: Use Python 3.12.
- 2026-11-09: Use SQLAlchemy for all database access.
- 2026-11-09: Collect US prices only (`cc=us`).
- 2026-11-09: Use free tiers only for all services and APIs.
- 2026-11-09: Run scheduled jobs with GitHub Actions.
- 2026-11-09: Use Neon Postgres in production and SQLite locally, selected by a `DATABASE_URL` setting.
- 2026-11-09: Backfill price history from the IsThereAnyDeal API; do not scrape SteamDB.
- 2026-11-09: Store money as integer cents.
- 2026-11-09: Store a price row only when the price changes, plus a last-checked time per game.
- 2026-11-09: Keep only Steam-store history from ITAD.
- 2026-11-10: Organize code as one installable `collector` package: API clients in `collector/sources/`, tables in `collector/db/`, config in `collector/settings.py`, CLI in `collector/cli.py`.
- 2026-11-10: When `DATABASE_URL` is unset, default to `data/steam_prices.db` resolved from the project root (not the current directory), so notebooks and scripts run from any folder use the same file.
- 2026-11-10: Declare dependencies in `pyproject.toml` (setuptools backend) with `notebooks` and `dev` optional extras; runtime pins carried over from `requirements.txt`.
- 2026-11-10: Lint with ruff using an explicit rule set (`E4`, `E7`, `E9`, `F`, `I`) so ruff upgrades do not change what CI enforces; notebooks are exploratory and excluded from lint.
- 2026-11-10: Tests run against an in-memory SQLite database and block all HTTP requests; fixtures are saved API responses (the Steam one is a real response trimmed to the fields we parse).
- 2026-11-10: Resolve every relative SQLite path in `DATABASE_URL` from the project root, not just the default, so notebooks and scripts open the same file.
- 2026-11-10: Manage the schema with Alembic (`migrations/`); the collector no longer calls `create_all` and exits with a hint to run `alembic upgrade head`.
- 2026-11-10: `migrations/env.py` refuses to run on a database that has old-schema tables, so the original SQLite file can't be upgraded in place by accident.
- 2026-11-10: Store all timestamps as timezone-aware UTC through a `UTCDateTime` column type, which normalizes to UTC on SQLite (no native timezone support) and maps to `timestamptz` on Postgres.
- 2026-11-10: One `price_changes` table for both sources, unique on `(app_id, observed_at, source)`; `initial_cents` is nullable because old ITAD data lacks the regular price.
- 2026-11-10: Genres are a separate `genres` table linked by `game_genres` instead of a comma-separated string.
- 2026-11-10: Release dates are stored as dates only when Steam gives an exact day; quarters, months, and "Coming soon" become NULL.
- 2026-11-10: Steam and ITAD parsers return integer cents (Steam's own cent values; ITAD's `amountInt`) so no float dollars enter the database.
- 2026-11-10: Migrate old data by copying into a fresh, empty database in one transaction rather than upgrading in place; ITAD timestamps from the old file are treated as UTC because their offset was never stored.
- 2026-11-11: Fetch prices with batched `appdetails?filters=price_overview` calls of 100 IDs: the experiment showed 200 work and 500 fail, so 100 leaves a margin.
- 2026-11-11: Track full-details freshness in a separate `games.details_checked_at` column, so catalog-added games (name only) are distinguishable from fully fetched ones.
- 2026-11-11: Use psycopg 3 for Postgres and rewrite plain `postgresql://` URLs (as Neon provides them) to `postgresql+psycopg://`.
