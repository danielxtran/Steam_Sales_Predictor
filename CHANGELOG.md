# Changelog

- Moved the code into a `collector` package (`collector/sources/`, `collector/db/`, `collector/settings.py`, `collector/cli.py`); run it with `python -m collector.cli`. `DATABASE_URL` is now read from the environment.
- Replaced `requirements.txt` with `pyproject.toml`; notebook packages are in the `notebooks` extra and pytest/ruff in the `dev` extra.
- Deleted the empty `notebooks/model_creation.ipynb`.
- Added `DATABASE_URL` to `.env.example`, explaining the SQLite default.
