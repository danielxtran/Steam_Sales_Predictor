# Changelog

- Moved the code into a `collector` package (`collector/sources/`, `collector/db/`, `collector/settings.py`, `collector/cli.py`); run it with `python -m collector.cli`. `DATABASE_URL` is now read from the environment.
