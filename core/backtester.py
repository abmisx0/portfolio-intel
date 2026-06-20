"""
Backtesting engine: compare two portfolio allocations over a historical period.

Methodology:
  - Constant-weight daily rebalancing (standard for ETF portfolio comparisons).
    Each day's portfolio return = sum(weight_i * daily_return_i) for all tickers.
  - Weights normalised to sum to 1 across tickers with available price data.
  - Benchmark: VOO (configurable).

Outputs per portfolio:
  - Cumulative return series (daily, indexed by date)
  - Annualized return, volatility, Sharpe, Sortino, max drawdown, Calmar
  - Calendar year returns table
  - Rolling 12-month return series
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import PORTFOLIOS, RISK_FREE_RATE, BENCHMARK_TICKER, BENCHMARKS, resolve_portfolio
from core.data_fetcher import get_close_series, prefetch_prices
from core.analytics import (
    compute_metrics,
    _round,
)

logger = logging.getLogger(__name__)

ROLLING_WINDOW_DAYS = 252  # ~12 months of trading days


# ── Portfolio daily return series ─────────────────────────────────────────────

def _build_portfolio_series(
    positions: list[dict],
    start: date,
    end: date,
) -> pd.Series:
    """
    Build a daily portfolio price-index series (starting at 1.0) from a list
    of {ticker, weight} positions over [start, end].

    Weights are normalised across tickers that have data in the period.
    Returns a pd.Series indexed by date.
    """
    price_data: dict[str, pd.Series] = {}
    weights: dict[str, float] = {}

    for pos in positions:
        ticker = pos["ticker"].upper()
        w = float(pos["weight"])
        series = get_close_series(ticker, start=start, end=end)
        if not series.empty:
            price_data[ticker] = series
            weights[ticker] = w
        else:
            logger.warning("No price data for %s in [%s, %s] — excluded", ticker, start, end)

    if not price_data:
        raise ValueError("No price data available for any ticker in portfolio")

    # Align all series to common dates (inner join)
    price_df = pd.concat(price_data.values(), axis=1, keys=price_data.keys()).dropna()

    if price_df.empty:
        raise ValueError("No overlapping dates across all tickers")

    # Normalise weights
    total_w = sum(weights[t] for t in price_df.columns)
    norm_weights = {t: weights[t] / total_w for t in price_df.columns}

    # Daily returns
    returns_df = price_df.pct_change().dropna()

    # Weighted daily portfolio return
    w_series = pd.Series(norm_weights)
    port_daily = (returns_df * w_series).sum(axis=1)

    # Cumulative price index (starts at 1.0)
    port_index = (1 + port_daily).cumprod()
    port_index.name = "portfolio"
    return port_index


# ── Calendar year returns ─────────────────────────────────────────────────────

def _calendar_year_returns(price_index: pd.Series) -> dict[str, float]:
    """
    Given a cumulative price-index series, compute calendar year returns.
    Returns {year_str: return_float} e.g. {"2021": 0.182, "2022": -0.143}.
    """
    results = {}
    for year, group in price_index.groupby(price_index.index.year):
        if len(group) < 2:
            continue
        yr_return = float(group.iloc[-1] / group.iloc[0] - 1)
        results[str(year)] = round(yr_return, 6)
    return results


# ── Rolling 12-month returns ──────────────────────────────────────────────────

def _rolling_returns(
    price_index: pd.Series,
    window: int = ROLLING_WINDOW_DAYS,
    sample_freq: int = 21,  # monthly sample to keep output manageable
) -> list[dict]:
    """
    Rolling window return series, sampled every `sample_freq` trading days.
    Returns [{date, return}].
    """
    results = []
    prices = price_index.values
    dates = price_index.index

    for i in range(window, len(prices), sample_freq):
        r = float(prices[i] / prices[i - window] - 1)
        results.append({
            "date": dates[i].strftime("%Y-%m-%d"),
            "return": round(r, 6),
        })
    return results


# ── Metrics bundle for a price-index series ───────────────────────────────────

def _portfolio_metrics(price_index: pd.Series, label: str) -> dict:
    """Compute all standard metrics from a cumulative price-index series."""
    return compute_metrics(price_index, rfr=RISK_FREE_RATE, label=label)


# ── Main backtest entry point ─────────────────────────────────────────────────

def backtest(
    portfolio_a: str,
    portfolio_b: str,
    start: date | str,
    end: Optional[date | str] = None,
    include_benchmark: bool = True,
    benchmark: str = "voo",
    portfolio_a_override: Optional[list[dict]] = None,
    portfolio_b_override: Optional[list[dict]] = None,
) -> dict:
    """
    Compare two portfolio allocations over a historical period.

    Args:
        portfolio_a: Name of first portfolio (key in config.PORTFOLIOS).
        portfolio_b: Name of second portfolio.
        start:       Start date (ISO string or date).
        end:         End date (default: today).
        include_benchmark: Also compute VOO buy-and-hold.
        portfolio_a_override: Custom list of {ticker, weight} to use instead of config.
        portfolio_b_override: Custom list of {ticker, weight}.

    Returns a structured dict with metrics, calendar years, rolling returns,
    and a sampled cumulative return series for each portfolio + benchmark.
    """
    if isinstance(start, str):
        start = date.fromisoformat(start)
    if end is None:
        end = date.today()
    elif isinstance(end, str):
        end = date.fromisoformat(end)

    positions_a = portfolio_a_override or resolve_portfolio(portfolio_a)
    positions_b = portfolio_b_override or resolve_portfolio(portfolio_b)

    if not positions_a:
        raise ValueError(f"Portfolio '{portfolio_a}' not found")
    if not positions_b:
        raise ValueError(f"Portfolio '{portfolio_b}' not found")

    logger.info("Backtesting %s vs %s from %s to %s", portfolio_a, portfolio_b, start, end)

    bm_short = benchmark.upper()
    bm_ticker = BENCHMARKS.get(benchmark.lower(), BENCHMARK_TICKER)
    all_tickers = list({pos["ticker"].upper() for positions in (positions_a, positions_b) for pos in positions})
    if include_benchmark:
        all_tickers.append(bm_ticker)
    prefetch_prices(all_tickers, start, end)

    # Build price-index series for each portfolio
    idx_a = _build_portfolio_series(positions_a, start, end)
    idx_b = _build_portfolio_series(positions_b, start, end)

    # Align to common date range so comparison is apples-to-apples
    common_dates = idx_a.index.intersection(idx_b.index)
    idx_a = idx_a.loc[common_dates]
    idx_b = idx_b.loc[common_dates]

    # Normalise both to start at 1.0
    idx_a = idx_a / idx_a.iloc[0]
    idx_b = idx_b / idx_b.iloc[0]

    result: dict = {
        "portfolio_a": portfolio_a,
        "portfolio_b": portfolio_b,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "actual_start": idx_a.index[0].strftime("%Y-%m-%d"),
        "actual_end": idx_a.index[-1].strftime("%Y-%m-%d"),
    }

    def _build_result(label: str, idx: pd.Series) -> dict:
        return {
            "metrics": _portfolio_metrics(idx, label),
            "calendar_year_returns": _calendar_year_returns(idx),
            "rolling_12m_returns": _rolling_returns(idx),
            "cumulative_series": _sample_series(idx),
        }

    result[portfolio_a] = _build_result(portfolio_a, idx_a)
    result[portfolio_b] = _build_result(portfolio_b, idx_b)

    bm_key = f"benchmark_{bm_short}"

    if include_benchmark:
        bm_series = get_close_series(bm_ticker, start=start, end=end)
        bm_common = bm_series.loc[bm_series.index.intersection(common_dates)]
        if not bm_common.empty:
            bm_idx = bm_common / bm_common.iloc[0]
            result[bm_key] = _build_result(bm_key, bm_idx)

    result["benchmark"] = bm_short  # "VOO" or "SPX"
    result["summary"] = _comparison_summary(
        portfolio_a, portfolio_b,
        result[portfolio_a]["metrics"],
        result[portfolio_b]["metrics"],
        result.get(bm_key, {}).get("metrics"),
        bm_short=bm_short,
    )

    return result


def _sample_series(price_index: pd.Series, sample_freq: int = 5) -> list[dict]:
    """Sample a price-index series weekly (every 5 trading days) for JSON output."""
    sampled = price_index.iloc[::sample_freq]
    return [
        {"date": d.strftime("%Y-%m-%d"), "value": round(float(v), 6)}
        for d, v in sampled.items()
    ]


def _comparison_summary(
    label_a: str,
    label_b: str,
    m_a: dict,
    m_b: dict,
    m_bm: dict | None,
    bm_short: str = "VOO",
) -> dict:
    """Produce a head-to-head metrics diff between portfolio A and B."""
    def _diff(key):
        va = m_a.get(key)
        vb = m_b.get(key)
        if va is None or vb is None:
            return None
        return _round(va - vb)

    rows = {
        "annualized_return_diff": _diff("annualized_return"),
        "volatility_diff": _diff("annualized_volatility"),
        "sharpe_diff": _diff("sharpe_ratio"),
        "sortino_diff": _diff("sortino_ratio"),
        "max_drawdown_diff": _diff("max_drawdown"),
        "winner_return": label_a if (m_a.get("annualized_return") or 0) >= (m_b.get("annualized_return") or 0) else label_b,
        "winner_sharpe": label_a if (m_a.get("sharpe_ratio") or 0) >= (m_b.get("sharpe_ratio") or 0) else label_b,
        "winner_drawdown": label_a if (m_a.get("max_drawdown") or 0) >= (m_b.get("max_drawdown") or 0) else label_b,
    }
    if m_bm:
        rows["benchmark_annualized_return"] = m_bm.get("annualized_return")
        rows["a_vs_benchmark"] = _round(
            (m_a.get("annualized_return") or 0) - (m_bm.get("annualized_return") or 0)
        )
        rows["b_vs_benchmark"] = _round(
            (m_b.get("annualized_return") or 0) - (m_bm.get("annualized_return") or 0)
        )
    return rows
