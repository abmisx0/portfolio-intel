"""
Valuation multiples via yfinance, cached in SQLite for 1 day.

Stocks: trailing/forward P/E, P/S, P/B, EV/EBITDA, profit margin, dividend yield.
ETFs:   fund-level P/E / P/B / P/S (inverted from Yahoo's earnings-yield form),
        expense ratio, dividend yield.

Known yfinance unit quirks handled here so formatters never see them:
  - Stock `dividendYield` arrives in percent (6.71 = 6.71%); ETF `yield` arrives
    as a fraction (0.0779 = 7.79%). Both are normalised to fractions.
  - `funds_data.equity_holdings` ratios arrive as reciprocals (earnings yield,
    e.g. 0.0248 → P/E 40.4) and are inverted.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List

import yfinance as yf

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.cache import cached_json

logger = logging.getLogger(__name__)


def _pct_to_fraction(v) -> float | None:
    """yfinance returns stock dividendYield and netExpenseRatio in percent
    (0.35 = 0.35%, 6.71 = 6.71%) — always divide by 100."""
    return float(v) / 100 if v is not None else None


def _invert_ratio(v) -> float | None:
    """Yahoo fund ratios arrive as reciprocals (earnings yield 0.0248 → P/E 40.4)."""
    if v is None or not (0 < float(v) < 1):
        return None
    return 1 / float(v)


def _fund_ratios(t: yf.Ticker) -> dict:
    """Fund-level P/E, P/B, P/S for ETFs from funds_data; {} if unavailable."""
    try:
        eq = t.funds_data.equity_holdings
        if eq is None or eq.empty:
            return {}
        col = eq.iloc[:, 0]  # first column = this fund (second = category average)
        return {
            "fund_pe": _invert_ratio(col.get("Price/Earnings")),
            "fund_pb": _invert_ratio(col.get("Price/Book")),
            "fund_ps": _invert_ratio(col.get("Price/Sales")),
        }
    except Exception:
        return {}


def _fetch_from_yf(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.info or {}
    quote_type = (info.get("quoteType") or "").upper()
    is_fund = quote_type in ("ETF", "MUTUALFUND")

    data = {
        "ticker":         ticker.upper(),
        "name":           info.get("longName") or info.get("shortName") or ticker,
        "quote_type":     quote_type or None,
        "price":          info.get("currentPrice") or info.get("regularMarketPrice"),
        "market_cap":     info.get("marketCap"),
        # ETF `yield` is already a fraction (0.0779 = 7.79%); stock `dividendYield`
        # is in percent. The ETF fallback to dividendYield is percent too.
        "dividend_yield": (info.get("yield") or _pct_to_fraction(info.get("dividendYield")))
                          if is_fund else _pct_to_fraction(info.get("dividendYield")),
        "fetched":        date.today().isoformat(),
    }

    if is_fund:
        data.update({
            "expense_ratio": _pct_to_fraction(info.get("netExpenseRatio")),
            "aum":           info.get("totalAssets"),
            **_fund_ratios(t),
        })
    else:
        data.update({
            "trailing_pe":   info.get("trailingPE"),
            "forward_pe":    info.get("forwardPE"),
            "price_to_sales": info.get("priceToSalesTrailing12Months"),
            "price_to_book": info.get("priceToBook"),
            "ev_to_ebitda":  info.get("enterpriseToEbitda"),
            "peg_ratio":     info.get("trailingPegRatio"),
            "profit_margin": info.get("profitMargins"),
        })
    return data


def get_valuation(ticker: str) -> dict:
    """Return valuation multiples for ticker, using 1-day SQLite cache."""
    return cached_json("valuation_data", ticker, _fetch_from_yf, ttl_days=1)


def get_valuation_multi(tickers: List[str]) -> Dict[str, dict]:
    return {t.upper(): get_valuation(t) for t in tickers}
