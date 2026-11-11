#!/usr/bin/env python3
"""
Steam price data collector — runs one of the collection jobs.

Run "alembic upgrade head" once before first use to create the tables.

Usage
-----
  python -m collector.cli catalog                 # same as python -m collector.jobs.catalog
  python -m collector.cli prices --limit 50       # same as python -m collector.jobs.prices
  python -m collector.cli itad --limit 100        # same as python -m collector.jobs.itad

Each job takes its own options; see e.g. "python -m collector.cli prices --help".
"""
import argparse

from collector.jobs import catalog, itad, prices

JOBS = {
    "catalog": (catalog.main, "add new games and update hot tiers (daily)"),
    "prices": (prices.main, "re-check due games and save price changes (every 3 hours)"),
    "itad": (itad.main, "backfill Steam-store price history from IsThereAnyDeal"),
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m collector.cli",
        description="Steam price data collector",
        epilog="Run 'python -m collector.cli JOB --help' for a job's options.",
    )
    sub = parser.add_subparsers(dest="job", required=True, metavar="JOB")
    for name, (_, help_text) in JOBS.items():
        sub.add_parser(name, help=help_text, add_help=False)
    args, rest = parser.parse_known_args(argv)
    JOBS[args.job][0](rest)


if __name__ == "__main__":
    main()
