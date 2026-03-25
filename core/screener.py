"""
ETF Screener: given a candidate ticker, produce a full analytical profile
against the current portfolio.

Combines:
  - Trailing returns (standard windows)
  - Risk metrics (Sharpe, Sortino, vol, drawdown, beta vs VOO)
  - Correlation with each portfolio position
  - Holdings overlap analysis
  - Effective concentration if added at a given allocation
  - ETF metadata (expense ratio, AUM, dividend yield from yfinance info)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import PORTFOLIOS, RISK_FREE_RATE, LOOKBACK_5Y, BENCHMARK_TICKER
from core.data_fetcher import get_close_series, price_map_freshness, get_etf_sectors, get_etf_info
from core.analytics import (
    compute_metrics,
    trailing_return_windows,
    correlation,
    correlation_matrix,
    _round,
)
from core.macro import commodity_context
from core.holdings import (
    overlap_analysis,
    effective_concentration,
    get_etf_holdings,
)

logger = logging.getLogger(__name__)

_5Y_START = LOOKBACK_5Y



# ── Main screener entry point ─────────────────────────────────────────────────

def screen(
    candidate_ticker: str,
    portfolio_name: str = "proposed",
    candidate_allocation: float = 0.05,
) -> dict:
    """
    Full screener analysis of candidate_ticker against portfolio_name.

    Returns a structured dict ready for JSON serialisation.
    """
    candidate_ticker = candidate_ticker.upper()
    positions = PORTFOLIOS.get(portfolio_name)
    if not positions:
        raise ValueError(f"Portfolio '{portfolio_name}' not found in config")

    # ── Fetch price data ───────────────────────────────────────────────────────
    candidate_series = get_close_series(candidate_ticker, start=_5Y_START)
    benchmark_series = get_close_series(BENCHMARK_TICKER, start=_5Y_START)

    portfolio_series: Dict[str, pd.Series] = {}
    for pos in positions:
        t = pos["ticker"].upper()
        s = get_close_series(t, start=_5Y_START)
        if not s.empty:
            portfolio_series[t] = s

    all_series = [candidate_series, benchmark_series] + list(portfolio_series.values())

    # ── Candidate metrics ──────────────────────────────────────────────────────
    candidate_metrics = compute_metrics(
        candidate_series,
        benchmark=benchmark_series,
        rfr=RISK_FREE_RATE,
        label=candidate_ticker,
    )
    candidate_trailing = trailing_return_windows(candidate_series)

    # ── ETF metadata ───────────────────────────────────────────────────────────
    etf_info = get_etf_info(candidate_ticker)

    # ── Correlation with each portfolio position ───────────────────────────────
    correlations = {}
    for ticker, series in portfolio_series.items():
        corr = correlation(candidate_series, series)
        correlations[ticker] = _round(corr)

    # ── Holdings overlap ───────────────────────────────────────────────────────
    overlap = overlap_analysis(candidate_ticker, portfolio_name)

    # ── Effective concentration at proposed allocation ─────────────────────────
    concentration = effective_concentration(
        candidate_ticker,
        portfolio_name,
        candidate_allocation=candidate_allocation,
    )

    # ── Top holdings of candidate ──────────────────────────────────────────────
    candidate_holdings = get_etf_holdings(candidate_ticker)

    # ── Sector breakdown ───────────────────────────────────────────────────────
    candidate_sectors = get_etf_sectors(candidate_ticker)

    return {
        "candidate_ticker": candidate_ticker,
        "portfolio": portfolio_name,
        "candidate_allocation_assumed": candidate_allocation,
        "etf_info": etf_info,
        "trailing_returns": candidate_trailing,
        "risk_metrics": candidate_metrics,
        "correlations_to_portfolio": correlations,
        "overlap": overlap,
        "effective_concentration": concentration,
        "top_holdings": candidate_holdings[:10],
        "sectors": candidate_sectors,
        "data_freshness": price_map_freshness(all_series),
    }


# ── Head-to-head comparison ───────────────────────────────────────────────────

def compare_multi(tickers: List[str]) -> dict:
    """
    Multi-ticker comparison for 3+ ETFs: per-ticker metrics + correlation matrix.
    For exactly 2 tickers use compare() instead (includes cross-overlap analysis).
    """
    tickers = [t.upper() for t in tickers]
    benchmark = get_close_series(BENCHMARK_TICKER, start=_5Y_START)

    price_map: Dict[str, pd.Series] = {}
    metrics_map, trailing_map, info_map, commodity_map, sectors_map = {}, {}, {}, {}, {}

    for t in tickers:
        s = get_close_series(t, start=_5Y_START)
        if not s.empty:
            price_map[t] = s
            metrics_map[t] = compute_metrics(s, benchmark=benchmark, label=t)
            trailing_map[t] = trailing_return_windows(s)
            info_map[t] = get_etf_info(t)
            commodity_map[t] = commodity_context(t, s)
            sectors_map[t] = get_etf_sectors(t)

    corr = correlation_matrix(price_map) if len(price_map) >= 2 else {}

    return {
        "tickers": tickers,
        "metrics": metrics_map,
        "trailing_returns": trailing_map,
        "etf_info": info_map,
        "commodity_context": commodity_map,
        "sectors": sectors_map,
        "correlation_matrix": corr,
        "data_freshness": price_map_freshness(list(price_map.values()) + [benchmark]),
    }


def compare(ticker_a: str, ticker_b: str) -> dict:
    """
    Side-by-side comparison of two ETFs.

    Returns metrics for both plus correlation between them.
    """
    ticker_a, ticker_b = ticker_a.upper(), ticker_b.upper()

    series_a = get_close_series(ticker_a, start=_5Y_START)
    series_b = get_close_series(ticker_b, start=_5Y_START)
    benchmark = get_close_series(BENCHMARK_TICKER, start=_5Y_START)

    metrics_a = compute_metrics(series_a, benchmark=benchmark, label=ticker_a)
    metrics_b = compute_metrics(series_b, benchmark=benchmark, label=ticker_b)

    trailing_a = trailing_return_windows(series_a)
    trailing_b = trailing_return_windows(series_b)

    info_a = get_etf_info(ticker_a)
    info_b = get_etf_info(ticker_b)

    corr_ab = _round(correlation(series_a, series_b))

    holdings_a = get_etf_holdings(ticker_a)
    holdings_b = get_etf_holdings(ticker_b)
    sectors_a = get_etf_sectors(ticker_a)
    sectors_b = get_etf_sectors(ticker_b)

    # Cross-overlap: what's in A that's also in B
    map_a = {h["symbol"].upper(): h["weight"] for h in holdings_a}
    map_b = {h["symbol"].upper(): h["weight"] for h in holdings_b}
    shared = set(map_a) & set(map_b)
    cross_overlap = sum(min(map_a[s], map_b[s]) for s in shared)

    result = {
        "ticker_a": ticker_a,
        "ticker_b": ticker_b,
        "correlation": corr_ab,
        "cross_overlap_coefficient": round(cross_overlap, 6),
        "shared_holdings_count": len(shared),
        ticker_a: {
            "etf_info": info_a,
            "trailing_returns": trailing_a,
            "risk_metrics": metrics_a,
            "top_holdings": holdings_a[:10],
            "sectors": sectors_a,
            "commodity_context": commodity_context(ticker_a, series_a),
        },
        ticker_b: {
            "etf_info": info_b,
            "trailing_returns": trailing_b,
            "risk_metrics": metrics_b,
            "top_holdings": holdings_b[:10],
            "sectors": sectors_b,
            "commodity_context": commodity_context(ticker_b, series_b),
        },
        "data_freshness": price_map_freshness([series_a, series_b, benchmark]),
    }
    return result
