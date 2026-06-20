"""
Price-action technicals computed from the SQLite price cache (no extra API calls).

Per ticker: last price, SMA50/SMA200, RSI(14), 52-week high/low, and swing
support/resistance levels (5-day pivot highs/lows over the trailing 6 months).
Used for entry/exit level guidance alongside analyst targets and valuation.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.data_fetcher import get_close_series, prefetch_prices

logger = logging.getLogger(__name__)

_LOOKBACK_DAYS = 420       # calendar days fetched (covers SMA200 + 52w window)
_PIVOT_WINDOW = 5          # bars on each side for a swing high/low
_SWING_SPAN = 126          # trading days scanned for pivots (~6 months)
_MAX_LEVELS = 3


def rsi(prices: pd.Series, period: int = 14) -> Optional[float]:
    """Simple-MA RSI over the last `period` bars; None if not enough data."""
    d = prices.diff()
    if len(d.dropna()) < period:
        return None
    gain = d.clip(lower=0).rolling(period).mean().iloc[-1]
    loss = (-d.clip(upper=0)).rolling(period).mean().iloc[-1]
    if pd.isna(gain) or pd.isna(loss):
        return None
    if loss == 0:
        return 100.0
    return float(100 - 100 / (1 + gain / loss))


def swing_levels(prices: pd.Series) -> tuple[List[float], List[float]]:
    """(support, resistance) from swing pivots below/above the last price."""
    px = float(prices.iloc[-1])
    w = prices[-_SWING_SPAN:]
    piv_hi = w[(w.shift(_PIVOT_WINDOW) < w) & (w.shift(-_PIVOT_WINDOW) < w)].dropna()
    piv_lo = w[(w.shift(_PIVOT_WINDOW) > w) & (w.shift(-_PIVOT_WINDOW) > w)].dropna()
    resistance = sorted({round(float(v), 2) for v in piv_hi if v > px})[:_MAX_LEVELS]
    support = sorted({round(float(v), 2) for v in piv_lo if v < px}, reverse=True)[:_MAX_LEVELS]
    return support, resistance


def get_technicals(ticker: str) -> dict:
    """Technical snapshot for one ticker from cached prices."""
    ticker = ticker.upper()
    start = date.today() - timedelta(days=_LOOKBACK_DAYS)
    s = get_close_series(ticker, start, date.today()).dropna()

    if len(s) < 30:
        return {"ticker": ticker, "error": "insufficient price history"}

    px = float(s.iloc[-1])
    year = s[-252:]
    hi52, lo52 = float(year.max()), float(year.min())
    support, resistance = swing_levels(s)

    return {
        "ticker": ticker,
        "price": round(px, 2),
        "sma50": round(float(s.rolling(50).mean().iloc[-1]), 2) if len(s) >= 50 else None,
        "sma200": round(float(s.rolling(200).mean().iloc[-1]), 2) if len(s) >= 200 else None,
        "rsi14": round(rsi(s), 1) if rsi(s) is not None else None,
        "high_52w": round(hi52, 2),
        "low_52w": round(lo52, 2),
        "pct_from_52w_high": round(px / hi52 - 1, 4) if hi52 else None,
        "support": support,
        "resistance": resistance,
        "as_of": str(s.index[-1].date()),
    }


def get_technicals_multi(tickers: List[str]) -> Dict[str, dict]:
    tickers = [t.upper() for t in tickers]
    prefetch_prices(tickers, date.today() - timedelta(days=_LOOKBACK_DAYS), date.today())
    return {t: get_technicals(t) for t in tickers}
