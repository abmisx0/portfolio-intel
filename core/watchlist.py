"""
Watchlist: persist candidate ETF tickers for deferred screening.

Stored in the same SQLite cache DB as price/holdings data.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from typing import List, Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CACHE_DB_PATH

_DDL = """
CREATE TABLE IF NOT EXISTS watchlist (
    ticker      TEXT    PRIMARY KEY,
    added_date  TEXT    NOT NULL,
    notes       TEXT    DEFAULT ''
);
"""


@contextmanager
def _db():
    conn = sqlite3.connect(CACHE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(_DDL)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def add(ticker: str, notes: str = "") -> bool:
    """Add ticker to watchlist. Returns False if already present."""
    ticker = ticker.upper()
    with _db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM watchlist WHERE ticker = ?", (ticker,)
        ).fetchone()
        if existing:
            return False
        conn.execute(
            "INSERT INTO watchlist (ticker, added_date, notes) VALUES (?, ?, ?)",
            (ticker, date.today().isoformat(), notes),
        )
        conn.commit()
    return True


def remove(ticker: str) -> bool:
    """Remove ticker from watchlist. Returns False if not found."""
    ticker = ticker.upper()
    with _db() as conn:
        cur = conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
        conn.commit()
        return cur.rowcount > 0


def list_all() -> List[dict]:
    """Return all watchlist entries as [{ticker, added_date, notes}]."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT ticker, added_date, notes FROM watchlist ORDER BY added_date DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def update_notes(ticker: str, notes: str) -> bool:
    """Update notes for an existing watchlist entry."""
    ticker = ticker.upper()
    with _db() as conn:
        cur = conn.execute(
            "UPDATE watchlist SET notes = ? WHERE ticker = ?", (notes, ticker)
        )
        conn.commit()
        return cur.rowcount > 0


def tickers() -> List[str]:
    """Return just the list of watchlist tickers."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT ticker FROM watchlist ORDER BY added_date DESC"
        ).fetchall()
    return [row["ticker"] for row in rows]
