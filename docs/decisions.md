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
