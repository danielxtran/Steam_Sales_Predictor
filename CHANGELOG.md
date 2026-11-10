# Changelog

- Moved the code into a `collector` package (`collector/sources/`, `collector/db/`, `collector/settings.py`, `collector/cli.py`); run it with `python -m collector.cli`. `DATABASE_URL` is now read from the environment.
- Replaced `requirements.txt` with `pyproject.toml`; notebook packages are in the `notebooks` extra and pytest/ruff in the `dev` extra.
- Deleted the empty `notebooks/model_creation.ipynb`.
- Added `DATABASE_URL` to `.env.example`, explaining the SQLite default.
- Configured ruff (explicit rule set, notebooks excluded) and sorted imports to match.
- Added pytest tests for `parse_game`, `_compute_discount`, and the ITAD history parser, using saved responses in `tests/fixtures/`; network calls are blocked in tests.
- Added a GitHub Actions CI workflow that runs ruff and pytest on pull requests and pushes to `main`.
- Rewrote `README.md` with project overview, setup, collector usage, and how to run tests.
- Updated `CLAUDE.md` file map and known gaps for the new layout.
