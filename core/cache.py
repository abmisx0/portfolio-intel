"""
Shared SQLite JSON cache for fetched API payloads.

One table per data kind, all with the same shape:
    (ticker TEXT, fetch_date TEXT, payload TEXT, PRIMARY KEY (ticker, fetch_date))

cached_json() is the single read-through helper used by data_fetcher (holdings,
sectors, etf_info), analysts, and valuation. finnhub.py keeps its own cache —
its key is (endpoint, symbol) with per-endpoint TTLs, a different shape.

The read and the write each use a separate connection so the network call in
fetch_fn is never made while a DB connection is held open.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any, Callable, Generator

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CACHE_DB_PATH

logger = logging.getLogger(__name__)

# Tables are created lazily, once per process.
_known_tables: set[str] = set()


@contextmanager
def db(table: str | None = None) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(CACHE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if table and table not in _known_tables:
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {table} (
                    ticker      TEXT NOT NULL,
                    fetch_date  TEXT NOT NULL,
                    payload     TEXT NOT NULL,
                    PRIMARY KEY (ticker, fetch_date)
                )"""  # nosec: table names are internal constants, never user input
            )
            conn.commit()
            _known_tables.add(table)
        yield conn
    finally:
        conn.close()


def cached_json(table: str, ticker: str, fetch_fn: Callable[[str], Any], ttl_days: int = 1) -> Any:
    """Return the cached payload for (table, ticker) or call fetch_fn on miss/expiry."""
    ticker = ticker.upper()
    cutoff = (date.today() - timedelta(days=ttl_days)).isoformat()

    with db(table) as conn:
        row = conn.execute(
            f"SELECT payload FROM {table}"  # nosec
            " WHERE ticker = ? AND fetch_date >= ?"
            " ORDER BY fetch_date DESC LIMIT 1",
            (ticker, cutoff),
        ).fetchone()

    if row:
        logger.debug("cache hit: %s/%s", table, ticker)
        return json.loads(row["payload"])

    logger.debug("cache miss: %s/%s — fetching", table, ticker)
    result = fetch_fn(ticker)

    with db(table) as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO {table} (ticker, fetch_date, payload) VALUES (?, ?, ?)",  # nosec
            (ticker, date.today().isoformat(), json.dumps(result)),
        )
        conn.commit()

    return result
