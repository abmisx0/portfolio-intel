"""
yfinance wrapper with SQLite-backed caching and exponential-backoff retries.

Price data:
  - Daily OHLCV stored in SQLite table `prices`.
  - On request, only the missing date range is fetched (delta fetching).
  - Never re-fetches full history if partial cache exists.

Holdings data:
  - Stored as a JSON blob per (ticker, fetch_date) in table `holdings`.
  - TTL enforced at read time: stale entries trigger a fresh fetch.
"""
from __future__ import annotations

import io
import json
import logging
import sqlite3
import sys
import os
import time
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from datetime import date, timedelta
from typing import Generator, List, Optional, Tuple

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    CACHE_DB_PATH,
    HOLDINGS_CACHE_TTL_DAYS,
    YFINANCE_BACKOFF_BASE,
    YFINANCE_MAX_RETRIES,
)

logger = logging.getLogger(__name__)

# ── Schema ─────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS prices (
    ticker      TEXT    NOT NULL,
    date        TEXT    NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL    NOT NULL,
    adj_close   REAL,
    volume      INTEGER,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS holdings (
    ticker      TEXT    NOT NULL,
    fetch_date  TEXT    NOT NULL,
    payload     TEXT    NOT NULL,
    PRIMARY KEY (ticker, fetch_date)
);

CREATE TABLE IF NOT EXISTS sectors (
    ticker      TEXT    NOT NULL,
    fetch_date  TEXT    NOT NULL,
    payload     TEXT    NOT NULL,
    PRIMARY KEY (ticker, fetch_date)
);

CREATE TABLE IF NOT EXISTS etf_info (
    ticker      TEXT    NOT NULL,
    fetch_date  TEXT    NOT NULL,
    payload     TEXT    NOT NULL,
    PRIMARY KEY (ticker, fetch_date)
);
"""


# ── DB Connection ──────────────────────────────────────────────────────────────

@contextmanager
def _db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(CACHE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_DDL)
        conn.commit()
        yield conn
    finally:
        conn.close()


# ── Retry Helper ───────────────────────────────────────────────────────────────

def _with_retry(fn, *args, **kwargs):
    """Call fn(*args, **kwargs) with exponential backoff on exception."""
    last_exc = None
    for attempt in range(YFINANCE_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            wait = YFINANCE_BACKOFF_BASE ** attempt
            logger.warning(
                "yfinance call failed (attempt %d/%d): %s. Retrying in %.1fs…",
                attempt + 1,
                YFINANCE_MAX_RETRIES,
                exc,
                wait,
            )
            time.sleep(wait)
    raise RuntimeError(
        f"yfinance call failed after {YFINANCE_MAX_RETRIES} attempts"
    ) from last_exc


# ── yfinance Noise Suppression ─────────────────────────────────────────────────

def _yf_download(ticker: str, start: str, end: str) -> "pd.DataFrame":
    """
    Call yf.download with stdout/stderr suppressed.

    yfinance 1.x prints 'possibly delisted; no price data found' directly to
    stdout when a date range has no trading days (weekends, holidays). We
    capture that output and re-emit it at DEBUG level so it doesn't pollute
    the CLI table output.
    """
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        raw = _with_retry(
            yf.download,
            ticker,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
        )
    captured = buf.getvalue().strip()
    if captured:
        logger.debug("yfinance output for %s [%s→%s]: %s", ticker, start, end, captured)
    return raw


# ── Trading-Day Heuristic ──────────────────────────────────────────────────────

def _has_weekday(start: date, end: date) -> bool:
    """Return True if [start, end] (inclusive) contains at least one Mon–Fri."""
    current = start
    while current <= end:
        if current.weekday() < 5:  # 0=Mon … 4=Fri
            return True
        current += timedelta(days=1)
    return False


# ── stooq Fallback ─────────────────────────────────────────────────────────────

def _fetch_stooq(ticker: str, start: date, end: date) -> "pd.DataFrame":
    """
    Fetch daily OHLCV from stooq.com (free, no API key).

    stooq CSV columns: Date, Open, High, Low, Close, Volume
    Returns a DataFrame with the same shape as yf.download output,
    or an empty DataFrame on failure.
    """
    import requests as _req

    url = (
        f"https://stooq.com/q/d/l/"
        f"?s={ticker.lower()}.us"
        f"&d1={start.strftime('%Y%m%d')}"
        f"&d2={end.strftime('%Y%m%d')}"
        f"&i=d"
    )
    try:
        resp = _req.get(url, timeout=15, headers={"User-Agent": "portfolio-intel/1.0"})
        resp.raise_for_status()
        text = resp.text.strip()
        if not text or "No data" in text or text.startswith("<!"):
            logger.debug("stooq: no data for %s [%s→%s]", ticker, start, end)
            return pd.DataFrame()
        df = pd.read_csv(io.StringIO(text), index_col=0, parse_dates=True)
        df.index = pd.DatetimeIndex(df.index)
        # Normalise column names to match yfinance (Open, High, Low, Close, Volume)
        df.columns = [c.strip().title() for c in df.columns]
        df = df.sort_index()
        logger.info("stooq: fetched %d rows for %s [%s→%s]", len(df), ticker, start, end)
        return df
    except Exception as exc:
        logger.warning("stooq fetch failed for %s: %s", ticker, exc)
        return pd.DataFrame()


# ── Price Fetching ─────────────────────────────────────────────────────────────

def _cached_date_range(conn: sqlite3.Connection, ticker: str) -> Tuple[Optional[date], Optional[date]]:
    """Return (min_date, max_date) for cached price rows, or (None, None)."""
    row = conn.execute(
        "SELECT MIN(date), MAX(date) FROM prices WHERE ticker = ?", (ticker,)
    ).fetchone()
    if row[0] is None:
        return None, None
    return date.fromisoformat(row[0]), date.fromisoformat(row[1])


def _insert_prices(conn: sqlite3.Connection, ticker: str, df: pd.DataFrame) -> int:
    """Insert price rows from a yfinance OHLCV DataFrame. Returns inserted count."""
    if df is None or df.empty:
        return 0

    df = df.reindex(columns=["Open", "High", "Low", "Close", "Volume"]).fillna(0)
    dates = [
        (idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])).isoformat()
        for idx in df.index
    ]
    rows = [
        (ticker, d, float(o), float(h), float(l), float(c), float(c), int(v))
        for d, (o, h, l, c, v) in zip(dates, df.values)
    ]

    conn.executemany(
        """INSERT OR REPLACE INTO prices
           (ticker, date, open, high, low, close, adj_close, volume)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def get_prices(
    ticker: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> pd.DataFrame:
    """
    Return a DataFrame of daily adjusted closes for `ticker` over [start, end].

    Fetches only missing data (delta fetch).
    Returns columns: open, high, low, close, volume (indexed by date).
    """
    if isinstance(start, str):
        start = date.fromisoformat(start)
    if isinstance(end, str):
        end = date.fromisoformat(end)

    today = date.today()
    if end is None:
        end = today
    if start is None:
        start = today - timedelta(days=365 * 5 + 10)

    with _db() as conn:
        min_cached, max_cached = _cached_date_range(conn, ticker)

        fetch_ranges: List[Tuple[date, date]] = []

        if min_cached is None:
            fetch_ranges.append((start, end))
        else:
            if start < min_cached:
                fetch_ranges.append((start, min_cached - timedelta(days=1)))
            if end > max_cached and (end - max_cached).days > 3:
                fetch_ranges.append((max_cached + timedelta(days=1), end))

        for fetch_start, fetch_end in fetch_ranges:
            # Skip windows that contain no weekdays (pure weekends / holiday boundaries).
            # These generate spurious yfinance "possibly delisted" noise.
            if not _has_weekday(fetch_start, fetch_end):
                logger.debug(
                    "Skipping %s [%s→%s]: no weekdays in range",
                    ticker, fetch_start, fetch_end,
                )
                continue

            logger.info("Fetching %s from %s to %s", ticker, fetch_start, fetch_end)
            raw = _yf_download(
                ticker,
                start=fetch_start.isoformat(),
                end=(fetch_end + timedelta(days=1)).isoformat(),
            )

            # If yfinance returns empty, try stooq fallback.
            if raw.empty:
                raw = _fetch_stooq(ticker, fetch_start, fetch_end)

            if raw.empty:
                # Small windows (≤ 5 days) that return no data from any provider
                # are almost certainly market holidays — log at DEBUG only.
                window_days = (fetch_end - fetch_start).days + 1
                log_fn = logger.debug if window_days <= 5 else logger.warning
                log_fn(
                    "No price data for %s [%s→%s] from any provider (window=%dd)",
                    ticker, fetch_start, fetch_end, window_days,
                )

            if not raw.empty:
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.get_level_values(0)
                _insert_prices(conn, ticker, raw)

        rows = conn.execute(
            """SELECT date, open, high, low, close, volume
               FROM prices
               WHERE ticker = ? AND date >= ? AND date <= ?
               ORDER BY date""",
            (ticker, start.isoformat(), end.isoformat()),
        ).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
    return df


def get_close_series(
    ticker: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> pd.Series:
    """Convenience: return just the daily close Series."""
    df = get_prices(ticker, start=start, end=end)
    if df.empty:
        return pd.Series(dtype=float, name=ticker)
    return df["close"].rename(ticker)


# ── Holdings Fetching ──────────────────────────────────────────────────────────

def get_holdings(ticker: str) -> List[dict]:
    """
    Return ETF top holdings as a list of {symbol, weight, name}.

    Cached for HOLDINGS_CACHE_TTL_DAYS. Falls back to empty list on failure.
    """
    today = date.today()
    cutoff = (today - timedelta(days=HOLDINGS_CACHE_TTL_DAYS)).isoformat()

    with _db() as conn:
        row = conn.execute(
            """SELECT payload FROM holdings
               WHERE ticker = ? AND fetch_date >= ?
               ORDER BY fetch_date DESC LIMIT 1""",
            (ticker, cutoff),
        ).fetchone()

        if row:
            logger.debug("Holdings cache hit for %s", ticker)
            return json.loads(row["payload"])

        logger.info("Fetching holdings for %s", ticker)
        holdings = _fetch_holdings_yfinance(ticker)

        conn.execute(
            "INSERT OR REPLACE INTO holdings (ticker, fetch_date, payload) VALUES (?, ?, ?)",
            (ticker, today.isoformat(), json.dumps(holdings)),
        )
        conn.commit()

    return holdings


def _fetch_holdings_yfinance(ticker: str) -> List[dict]:
    """
    Attempt to pull top holdings from yfinance.
    Returns list of {symbol, weight, name}, may be empty.
    """
    try:
        t = yf.Ticker(ticker)
        fund_data = getattr(t, "funds_data", None)
        if fund_data is not None:
            top = getattr(fund_data, "top_holdings", None)
            if top is not None and not top.empty:
                results = []
                for sym, row in top.iterrows():
                    results.append({
                        "symbol": str(sym),
                        "weight": float(row.get("Holding Percent", 0)),
                        "name": str(row.get("Name", sym)),
                    })
                return results
        info = t.info or {}
        holdings_raw = info.get("holdings", [])
        return [
            {
                "symbol": h.get("symbol", ""),
                "weight": float(h.get("holdingPercent", 0)),
                "name": h.get("holdingName", h.get("symbol", "")),
            }
            for h in holdings_raw
            if h.get("symbol")
        ]
    except Exception as exc:
        logger.warning("Could not fetch holdings for %s: %s", ticker, exc)
        return []


# ── Sector Fetching ────────────────────────────────────────────────────────────

_SECTOR_NAMES: dict[str, str] = {
    "realestate":             "Real Estate",
    "consumer_cyclical":      "Consumer Cyclical",
    "basic_materials":        "Basic Materials",
    "consumer_defensive":     "Consumer Defensive",
    "technology":             "Technology",
    "communication_services": "Communication Services",
    "financial_services":     "Financial Services",
    "utilities":              "Utilities",
    "industrials":            "Industrials",
    "energy":                 "Energy",
    "healthcare":             "Healthcare",
}


def get_etf_sectors(ticker: str) -> List[dict]:
    """
    Return ETF sector weights as [{sector, weight}], sorted by weight descending.

    Cached for HOLDINGS_CACHE_TTL_DAYS. Falls back to empty list on failure.
    Weight values are fractions (e.g. 0.95 = 95%).
    """
    ticker = ticker.upper()
    today = date.today()
    cutoff = (today - timedelta(days=HOLDINGS_CACHE_TTL_DAYS)).isoformat()

    with _db() as conn:
        row = conn.execute(
            """SELECT payload FROM sectors
               WHERE ticker = ? AND fetch_date >= ?
               ORDER BY fetch_date DESC LIMIT 1""",
            (ticker, cutoff),
        ).fetchone()

        if row:
            logger.debug("Sectors cache hit for %s", ticker)
            return json.loads(row["payload"])

        logger.info("Fetching sectors for %s", ticker)
        sectors = _fetch_sectors_yfinance(ticker)

        conn.execute(
            "INSERT OR REPLACE INTO sectors (ticker, fetch_date, payload) VALUES (?, ?, ?)",
            (ticker, today.isoformat(), json.dumps(sectors)),
        )
        conn.commit()

    return sectors


def _fetch_sectors_yfinance(ticker: str) -> List[dict]:
    """
    Fetch ETF sector breakdown from yfinance funds_data.
    Returns [{sector, weight}] sorted by weight desc, weights as fractions.
    """
    try:
        t = yf.Ticker(ticker)
        fd = getattr(t, "funds_data", None)
        if fd is None:
            return []
        sw = getattr(fd, "sector_weightings", None)
        if not sw:
            return []
        result = []
        for key, weight in sw.items():
            if weight and float(weight) > 0:
                name = _SECTOR_NAMES.get(key, key.replace("_", " ").title())
                result.append({"sector": name, "weight": float(weight)})
        result.sort(key=lambda x: x["weight"], reverse=True)
        logger.debug("Sectors for %s: %d non-zero sectors", ticker, len(result))
        return result
    except Exception as exc:
        logger.warning("Could not fetch sectors for %s: %s", ticker, exc)
        return []


# ── ETF Info Fetching ──────────────────────────────────────────────────────────

def get_etf_info(ticker: str) -> dict:
    """
    Return ETF metadata: name, expense_ratio, aum, dividend_yield, category, fund_family.

    Cached for HOLDINGS_CACHE_TTL_DAYS. Falls back to empty dict on failure.
    Eliminates redundant yfinance HTTP calls when the same ticker is queried
    multiple times in a session or across runs on the same day.
    """
    ticker = ticker.upper()
    today = date.today()
    cutoff = (today - timedelta(days=HOLDINGS_CACHE_TTL_DAYS)).isoformat()

    with _db() as conn:
        row = conn.execute(
            """SELECT payload FROM etf_info
               WHERE ticker = ? AND fetch_date >= ?
               ORDER BY fetch_date DESC LIMIT 1""",
            (ticker, cutoff),
        ).fetchone()

        if row:
            logger.debug("ETF info cache hit for %s", ticker)
            return json.loads(row["payload"])

        logger.info("Fetching ETF info for %s", ticker)
        info = _fetch_etf_info_yf(ticker)

        conn.execute(
            "INSERT OR REPLACE INTO etf_info (ticker, fetch_date, payload) VALUES (?, ?, ?)",
            (ticker, today.isoformat(), json.dumps(info)),
        )
        conn.commit()

    return info


def _fetch_etf_info_yf(ticker: str) -> dict:
    """Fetch ETF metadata from yfinance. Returns {} on failure."""
    try:
        info = yf.Ticker(ticker).info or {}
        return {
            "name": info.get("longName") or info.get("shortName", ticker),
            "expense_ratio": info.get("annualReportExpenseRatio") or info.get("expenseRatio"),
            "aum": info.get("totalAssets"),
            "dividend_yield": info.get("yield") or info.get("dividendYield"),
            "category": info.get("category"),
            "fund_family": info.get("fundFamily"),
        }
    except Exception as exc:
        logger.warning("Could not fetch ETF info for %s: %s", ticker, exc)
        return {}


# ── Freshness Helper ───────────────────────────────────────────────────────────

def price_map_freshness(price_map) -> Optional[str]:
    """Return the most recent date string across all series in a price_map (dict or list)."""
    series = price_map.values() if isinstance(price_map, dict) else price_map
    dates = [s.index[-1] for s in series if not s.empty]
    return max(dates).strftime("%Y-%m-%d") if dates else None
