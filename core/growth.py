"""
Consensus forward revenue & earnings growth estimates via yfinance, cached 1 day.

Stocks: consensus revenue/EPS estimates for the current (FY0) and next (FY1)
fiscal year with YoY growth and analyst counts, long-term growth (LTG) when
published, the S&P 500's LTG as a baseline, and a PEG computed from forward
P/E against LTG (falling back to FY1 EPS growth — `peg_basis` records which).

Info-derived fields (name, quote type, forward P/E, profit margin) are reused
from core.valuation's 1-day cache, so `growth` and `valuation` share a single
.info fetch per ticker per day and can never disagree within a TTL window.

ETFs carry no consensus estimates — they are returned with quote_type so the
formatter can list them separately (screen their top holdings instead).

All growth figures are stored as fractions (0.14 = 14%).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List

import requests
import yfinance as yf

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.cache import cached_json
from core.valuation import get_valuation

logger = logging.getLogger(__name__)


def _row(df, period: str, field: str):
    """Safe lookup of df.loc[period, field]; None on any miss (incl. df=None/NaN)."""
    try:
        v = df.loc[period, field]
        return None if v != v else float(v)  # NaN check
    except Exception:
        return None


def _estimate_table(t: yf.Ticker, attr: str):
    """One estimate DataFrame, or None when the ticker has no analyst coverage.

    Transient failures (rate limit, network) are re-raised so cached_json never
    persists an all-None payload for a ticker that does have coverage — a cached
    failure would otherwise show as blank data until the TTL expires.
    """
    try:
        return getattr(t, attr)
    except Exception as exc:
        if (isinstance(exc, requests.exceptions.RequestException)
                or "ratelimit" in type(exc).__name__.lower()):
            raise
        logger.warning("No %s for %s: %s", attr, t.ticker, exc)
        return None


def _fetch_from_yf(ticker: str) -> dict:
    val = get_valuation(ticker)  # shared 1-day cache with the valuation command
    quote_type = (val.get("quote_type") or "").upper()

    data = {
        "ticker":     ticker.upper(),
        "name":       val.get("name") or ticker,
        "quote_type": quote_type or None,
        "fetched":    date.today().isoformat(),
    }
    if quote_type in ("ETF", "MUTUALFUND"):
        return data

    t = yf.Ticker(ticker)
    rev = _estimate_table(t, "revenue_estimate")
    eps = _estimate_table(t, "earnings_estimate")
    gro = _estimate_table(t, "growth_estimates")

    # Negative forward P/E (loss-making next year) is meaningless — drop it.
    forward_pe = val.get("forward_pe")
    if forward_pe is not None and forward_pe <= 0:
        forward_pe = None
    eps_growth_1y = _row(eps, "+1y", "growth")
    ltg = _row(gro, "LTG", "stockTrend")

    # PEG against LTG when published, else next-year EPS growth.
    peg_basis = ltg if ltg is not None else eps_growth_1y
    peg = (forward_pe / (peg_basis * 100)
           if forward_pe and peg_basis and peg_basis > 0 else None)

    data.update({
        "rev_growth_0y": _row(rev, "0y", "growth"),
        "rev_growth_1y": _row(rev, "+1y", "growth"),
        "rev_est_0y":    _row(rev, "0y", "avg"),
        "rev_est_1y":    _row(rev, "+1y", "avg"),
        "eps_growth_0y": _row(eps, "0y", "growth"),
        "eps_growth_1y": eps_growth_1y,
        "eps_est_0y":    _row(eps, "0y", "avg"),
        "eps_est_1y":    _row(eps, "+1y", "avg"),
        "analysts":      _row(eps, "0y", "numberOfAnalysts"),
        "ltg":           ltg,
        "index_ltg":     _row(gro, "LTG", "indexTrend"),
        "forward_pe":    forward_pe,
        "peg":           peg,
        "peg_basis":     (("ltg" if ltg is not None else "eps_fy1")
                          if peg is not None else None),
        "profit_margin": val.get("profit_margin"),
    })
    return data


def get_growth(ticker: str) -> dict:
    """Forward growth estimates for ticker, using 1-day SQLite cache."""
    return cached_json("growth_estimates", ticker, _fetch_from_yf, ttl_days=1)


def get_growth_multi(tickers: List[str]) -> Dict[str, dict]:
    """Estimates per ticker; a failed fetch yields {"ticker", "error"} (uncached)
    so one rate-limited name never aborts a whole-portfolio run."""
    out: Dict[str, dict] = {}
    for t in tickers:
        t = t.upper()
        try:
            out[t] = get_growth(t)
        except Exception as exc:
            logger.warning("Growth fetch failed for %s: %s", t, exc)
            out[t] = {"ticker": t, "error": str(exc)}
    return out
