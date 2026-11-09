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
  (Neither is set up yet — the first task that adds tests should add `ruff` and
  `pytest` as dev dependencies and create `tests/`.)
- Add one line to `CHANGELOG.md` for each change.
- Add a dated line to `docs/decisions.md` for any design decision.
- When done, summarize what changed and why in plain language for a learner, and
  list how the user can check it themselves.

## Current files

| Path | What it does |
| --- | --- |
| `collect.py` | CLI entry point. Creates tables, picks app IDs (seed / SteamSpy popular / random), fetches Steam details, writes games and price snapshots, optionally fetches ITAD history. Flags: `--seed`, `--limit`, `--popular`, `--no-itad`, `--backfill-itad`, `--full-history`, `--schedule`. |
| `config.py` | Loads `.env`, exposes `STEAM_API_KEY` / `ITAD_API_KEY`, builds the SQLAlchemy `engine` and `Session`. |
| `db/models.py` | SQLAlchemy tables: `games`, `price_snapshots`, `itad_games` (Steam app ID → ITAD ID), `itad_price_history`. |
| `collectors/steam.py` | Steam Store / Web API client: featured-category seed list, full app list, `appdetails` fetch with rate-limit delay and retries, `parse_game()` to normalize a response. |
| `collectors/itad.py` | ITAD API client: `lookup_game()` (Steam app ID → ITAD game) and `get_price_history()`. |
| `collectors/steamspy.py` | SteamSpy client returning popular app IDs ranked by player count. |
| `notebooks/data_exploration.ipynb` | Exploratory analysis and draft ML features from the local DB. |
| `notebooks/model_creation.ipynb` | Empty placeholder for future modelling. |
| `requirements.txt` | Pinned runtime dependencies (`requests`, `sqlalchemy`, `python-dotenv`, `schedule`). |
| `.env.example` | Template listing the settings `.env` should define. |
| `data/` | Local SQLite database (gitignored; do not touch `steam_prices.db`). |
| `CHANGELOG.md` | One line per change. |
| `docs/decisions.md` | Dated log of design decisions. |

## Known gaps between current code and the rules above

The existing code predates some fixed decisions. Fix these only in tasks that ask
for them, one at a time:

- `config.py` hardcodes a SQLite path instead of reading `DATABASE_URL`.
- Prices are stored as `Float` dollars, not integer cents (`price_snapshots`,
  `itad_price_history`, and `parse_game()` divides by 100).
- `price_snapshots` gets a new row every run, even when the price is unchanged;
  there is no per-game last-checked time.
- `itad_price_history` stores every shop, not just Steam.
- Scheduling uses the `schedule` library in a long-running loop, not GitHub Actions.
- No linter config, no `tests/` directory, no fixtures, and no GitHub Actions
  workflows exist yet.
