"""
Insights: persists sync-generated analysis notes and metrics snapshots to cache.db.

Tables:
  insights        — log of analysis notes (manual sync, weekly auto)
  sync_snapshots  — last known metrics per portfolio (for delta comparison)
  sync_log        — timestamp of each sync run
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CACHE_DB_PATH

_DDL = """
CREATE TABLE IF NOT EXISTS insights (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,
    trigger      TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    body         TEXT    NOT NULL,
    portfolio    TEXT,
    metrics_json TEXT
);
CREATE TABLE IF NOT EXISTS sync_snapshots (
    portfolio    TEXT    PRIMARY KEY,
    timestamp    TEXT    NOT NULL,
    metrics_json TEXT    NOT NULL
);
"""


@contextmanager
def _conn():
    con = sqlite3.connect(str(CACHE_DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_DDL)
        yield con
        con.commit()
    finally:
        con.close()


def save_insight(trigger: str, title: str, body: str,
                 portfolio: str | None, metrics: dict | None) -> int:
    ts = datetime.utcnow().isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO insights (timestamp, trigger, title, body, portfolio, metrics_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ts, trigger, title, body, portfolio,
             json.dumps(metrics) if metrics else None),
        )
        return cur.lastrowid


def get_insights(limit: int = 50) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT id, timestamp, trigger, title, body, portfolio"
            " FROM insights ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_last_sync_metrics(portfolio: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT metrics_json FROM sync_snapshots WHERE portfolio = ?",
            (portfolio,),
        ).fetchone()
    if row and row[0]:
        return json.loads(row[0])
    return None


def save_sync_metrics(portfolio: str, metrics: dict) -> None:
    ts = datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO sync_snapshots (portfolio, timestamp, metrics_json)"
            " VALUES (?, ?, ?)",
            (portfolio, ts, json.dumps(metrics)),
        )


def days_since_last_sync() -> int | None:
    """Days since last manual sync, or None if never synced."""
    with _conn() as con:
        row = con.execute(
            "SELECT timestamp FROM insights WHERE trigger = 'manual_sync'"
            " ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    last = datetime.fromisoformat(row[0]).date()
    return (date.today() - last).days
