#!/usr/bin/env python3
"""
Experiment: does Steam's appdetails endpoint accept many app IDs in one call?

Usage
-----
  python scripts/appdetails_batch_experiment.py data/v2.db

Reads real game IDs from the given SQLite file (read-only), then calls
store.steampowered.com/api/appdetails with growing batches, with and without
filters=price_overview, and reports what came back. Makes about 10 requests,
spaced out to stay well under the store rate limit.
"""
import argparse
import sqlite3
import time
from pathlib import Path

import requests

URL = "https://store.steampowered.com/api/appdetails"
DELAY = 3.0


def load_ids(db: Path, n: int) -> list[int]:
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT app_id FROM games WHERE is_free = 0 ORDER BY total_reviews DESC LIMIT ?", (n,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def probe(ids: list[int], price_filter: bool) -> str:
    params = {"appids": ",".join(map(str, ids)), "cc": "us"}
    if price_filter:
        params["filters"] = "price_overview"
    resp = requests.get(URL, params=params, timeout=30)
    try:
        body = resp.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return f"HTTP {resp.status_code}, body={str(body)[:40]!r}"
    ok = sum(1 for v in body.values() if v.get("success"))
    priced = sum(1 for v in body.values() if v.get("success") and isinstance(v.get("data"), dict)
                 and "price_overview" in v["data"])
    return f"HTTP {resp.status_code}, entries={len(body)}, success={ok}, with price={priced}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("db", type=Path)
    args = parser.parse_args()

    ids = load_ids(args.db, 1000)
    trials = [(2, False), (1, True), (10, True), (50, True), (100, True),
              (200, True), (500, True), (1000, True)]
    print(f"{'ids':>5}  {'filter':<15} result")
    for n, price_filter in trials:
        result = probe(ids[:n], price_filter)
        print(f"{n:>5}  {'price_overview' if price_filter else 'none':<15} {result}")
        time.sleep(DELAY)


if __name__ == "__main__":
    main()
