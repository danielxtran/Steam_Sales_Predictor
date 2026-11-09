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
