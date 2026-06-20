"""
Finnhub API client with SQLite caching and rate-limiting.

Free tier: 60 calls/min. All responses cached in SQLite to avoid
redundant network calls. TTLs vary by data type — news 4h, most
fundamentals 24h, ETF holdings 7d.

Requires FINNHUB_API_KEY in .env.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any

import requests

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CACHE_DB_PATH, FINNHUB_API_KEY

logger = logging.getLogger(__name__)

_BASE = "https://finnhub.io/api/v1"
_SESSION = requests.Session()
_CALL_INTERVAL = 1.1  # seconds — keeps calls safely under 60/min
_last_call: float = 0.0
_rate_lock = threading.Lock()

# Cache TTL in hours per endpoint group
_TTL_HOURS: dict[str, int] = {
    "news":         4,
    "sentiment":    4,
    "insider":      24,
    "earnings":     24,
    "economic":     24,
    "etf_holdings": 168,   # 7 days
}

_TRANSACTION_CODES: dict[str, str] = {
    "P": "BUY",
    "S": "SELL",
    "A": "GRANT",
    "D": "DISPOSE",
    "F": "TAX_WITHHOLD",
    "M": "EXERCISE",
    "G": "GIFT",
    "X": "EXERCISE_OTM",
}

_DDL = """
CREATE TABLE IF NOT EXISTS finnhub_cache (
    endpoint    TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    PRIMARY KEY (endpoint, symbol)
);
"""


# ── DB ─────────────────────────────────────────────────────────────────────────

@contextmanager
def _db():
    conn = sqlite3.connect(CACHE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_DDL)
        conn.commit()
        yield conn
    finally:
        conn.close()


def _cache_get(conn: sqlite3.Connection, endpoint: str, symbol: str, ttl_hours: int) -> Any | None:
    row = conn.execute(
        "SELECT fetched_at, payload FROM finnhub_cache WHERE endpoint=? AND symbol=?",
        (endpoint, symbol),
    ).fetchone()
    if not row:
        return None
    fetched = datetime.fromisoformat(row["fetched_at"])
    if datetime.utcnow() - fetched > timedelta(hours=ttl_hours):
        return None
    return json.loads(row["payload"])


def _cache_set(conn: sqlite3.Connection, endpoint: str, symbol: str, data: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO finnhub_cache (endpoint, symbol, fetched_at, payload) VALUES (?,?,?,?)",
        (endpoint, symbol, datetime.utcnow().isoformat(), json.dumps(data)),
    )
    conn.commit()


# ── HTTP ───────────────────────────────────────────────────────────────────────

def _get(path: str, params: dict[str, Any]) -> Any:
    if not FINNHUB_API_KEY:
        raise EnvironmentError("FINNHUB_API_KEY not set — add it to .env")

    global _last_call
    with _rate_lock:
        wait = _CALL_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()

    resp = _SESSION.get(f"{_BASE}{path}", params={**params, "token": FINNHUB_API_KEY}, timeout=15)
    if resp.status_code == 403:
        raise PermissionError(f"Finnhub endpoint {path} requires a paid plan (403 Forbidden)")
    resp.raise_for_status()
    return resp.json()


def _cached(endpoint_key: str, path: str, symbol: str, params: dict[str, Any]) -> Any:
    ttl = _TTL_HOURS[endpoint_key]
    with _db() as conn:
        cached = _cache_get(conn, endpoint_key, symbol, ttl)
        if cached is not None:
            logger.debug("finnhub cache hit: %s/%s", endpoint_key, symbol)
            return cached
        logger.debug("finnhub cache miss: %s/%s — fetching", endpoint_key, symbol)
        data = _get(path, params)
        _cache_set(conn, endpoint_key, symbol, data)
        return data


# ── Public API ─────────────────────────────────────────────────────────────────

def get_insider_transactions(symbol: str) -> list[dict]:
    """
    Return list of insider transactions for symbol, most recent first.
    Each entry: name, role, code (BUY/SELL/GRANT/…), shares, price, value, date.
    """
    raw = _cached("insider", "/stock/insider-transactions", symbol.upper(), {"symbol": symbol.upper()})
    txns = raw.get("data") or []
    result = []
    for t in txns:
        code = _TRANSACTION_CODES.get(t.get("transactionCode", ""), t.get("transactionCode", ""))
        shares = t.get("share", 0) or 0
        price  = t.get("transactionPrice") or 0
        result.append({
            "name":             t.get("name", ""),
            "filing_date":      t.get("filingDate", ""),
            "transaction_date": t.get("transactionDate", ""),
            "code":             code,
            "shares":           int(shares),
            "price":            float(price),
            "value":            round(abs(shares * price)),
            "change":           int(t.get("change", 0) or 0),
        })
    result.sort(key=lambda x: x["transaction_date"], reverse=True)
    return result


def get_news_sentiment(symbol: str) -> dict:
    """
    Return aggregate news sentiment for symbol.
    Keys: bullish_pct, bearish_pct, buzz, articles_week, score, sector_score.
    """
    raw = _cached("sentiment", "/news-sentiment", symbol.upper(), {"symbol": symbol.upper()})
    buzz      = raw.get("buzz") or {}
    sentiment = raw.get("sentiment") or {}
    return {
        "symbol":        symbol.upper(),
        "bullish_pct":   sentiment.get("bullishPercent"),
        "bearish_pct":   sentiment.get("bearishPercent"),
        "buzz":          buzz.get("buzz"),
        "articles_week": buzz.get("articlesInLastWeek"),
        "score":         raw.get("companyNewsScore"),
        "sector_score":  raw.get("sectorAverageSentimentScore"),
    }


def get_company_news(symbol: str, days: int = 7) -> list[dict]:
    """Return up to 10 recent news articles for symbol."""
    to_date   = date.today().isoformat()
    from_date = (date.today() - timedelta(days=days)).isoformat()
    raw = _cached("news", "/company-news", f"{symbol.upper()}_{days}d",
                  {"symbol": symbol.upper(), "from": from_date, "to": to_date})
    articles = raw if isinstance(raw, list) else []
    return [
        {
            "datetime": datetime.fromtimestamp(a["datetime"]).strftime("%Y-%m-%d") if a.get("datetime") else "",
            "headline": a.get("headline", ""),
            "source":   a.get("source", ""),
            "url":      a.get("url", ""),
            "summary":  (a.get("summary") or "")[:200],
        }
        for a in articles[:10]
    ]


def get_earnings_surprises(symbol: str, quarters: int = 8) -> list[dict]:
    """Return historical EPS actual vs estimate for last N quarters."""
    raw = _cached("earnings", "/stock/earnings", symbol.upper(), {"symbol": symbol.upper()})
    items = raw if isinstance(raw, list) else []
    result = []
    for e in items[:quarters]:
        result.append({
            "period":           e.get("period", ""),
            "estimate":         e.get("estimate"),
            "actual":           e.get("actual"),
            "surprise":         e.get("surprise"),
            "surprise_pct":     e.get("surprisePercent"),
        })
    return result


def get_earnings_estimates(symbol: str, freq: str = "quarterly") -> list[dict]:
    """Return forward EPS estimates. freq: 'quarterly' or 'annual'."""
    raw = _cached("earnings", "/stock/eps-estimate", f"{symbol.upper()}_{freq}",
                  {"symbol": symbol.upper(), "freq": freq})
    items = (raw.get("data") or []) if isinstance(raw, dict) else []
    return [
        {
            "period":    e.get("period", ""),
            "eps_avg":   e.get("epsAvg"),
            "eps_high":  e.get("epsHigh"),
            "eps_low":   e.get("epsLow"),
            "analysts":  e.get("numberAnalysts"),
        }
        for e in items[:4]
    ]


def get_economic_indicator(code: str) -> list[dict]:
    """
    Return time series for a FRED-style economic indicator.
    Common codes: FED_FUNDS_RATE, REAL_GDP, CPI, UNEMPLOYMENT_RATE,
                  INFLATION_EXPECTATION, PRODUCER_PRICE_INDEX
    """
    raw = _cached("economic", "/economic", code, {"code": code})
    items = raw.get("data") or []
    return [{"date": d.get("date", ""), "value": d.get("value")} for d in items[:12]]


def get_etf_holdings(symbol: str, snapshot_date: str | None = None) -> list[dict]:
    """
    Return ETF constituent holdings from Finnhub.
    snapshot_date: YYYY-MM-DD (optional — defaults to latest available).
    """
    params: dict[str, Any] = {"symbol": symbol.upper()}
    if snapshot_date:
        params["date"] = snapshot_date
    cache_sym = f"{symbol.upper()}_{snapshot_date or 'latest'}"
    raw = _cached("etf_holdings", "/etf/holdings", cache_sym, params)
    holdings = raw.get("holdings") or []
    return [
        {
            "symbol":  h.get("symbol", ""),
            "name":    h.get("name", ""),
            "pct":     h.get("percent"),
            "shares":  h.get("share"),
            "value":   h.get("value"),
        }
        for h in holdings
        if h.get("symbol")
    ]
