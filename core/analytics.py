"""
Core analytics: risk/return metrics computed from daily price series.

All functions accept a pd.Series of daily prices (not returns) unless noted.
Annualisation uses 252 trading days.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import RISK_FREE_RATE

def _get_rfr() -> float:
    """Return the current risk-free rate. Lazy import avoids circular dep with macro.py."""
    from core.macro import get_risk_free_rate  # get_risk_free_rate already falls back to config
    return get_risk_free_rate()

TRADING_DAYS = 252


# ── Daily returns ──────────────────────────────────────────────────────────────

def daily_returns(prices: pd.Series) -> pd.Series:
    """Percentage daily returns from a price series."""
    return prices.pct_change().dropna()


# ── Core metrics ───────────────────────────────────────────────────────────────

def annualized_return(prices: pd.Series) -> float:
    """Compound annualized return over the full series."""
    return ann_return_from_returns(daily_returns(prices))


def annualized_volatility(prices: pd.Series) -> float:
    """Annualized standard deviation of daily returns."""
    return ann_vol_from_returns(daily_returns(prices))


def sharpe_ratio(prices: pd.Series, rfr: Optional[float] = None) -> float:
    if rfr is None:
        rfr = _get_rfr()
    ann_ret = annualized_return(prices)
    ann_vol = annualized_volatility(prices)
    if ann_vol == 0 or np.isnan(ann_vol):
        return float("nan")
    return (ann_ret - rfr) / ann_vol


def sortino_ratio(prices: pd.Series, rfr: Optional[float] = None) -> float:
    if rfr is None:
        rfr = _get_rfr()
    r = daily_returns(prices)
    if r.empty:
        return float("nan")
    ann_ret = annualized_return(prices)
    downside = r[r < 0]
    if downside.empty:
        return float("inf")
    downside_dev = float(downside.std() * np.sqrt(TRADING_DAYS))
    if downside_dev == 0:
        return float("nan")
    return (ann_ret - rfr) / downside_dev


def max_drawdown(prices: pd.Series) -> float:
    """Maximum peak-to-trough drawdown (negative number, e.g. -0.35 for -35%)."""
    if prices.empty:
        return float("nan")
    cum = (1 + daily_returns(prices)).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    return float(dd.min())


def calmar_ratio(prices: pd.Series) -> float:
    ann_ret = annualized_return(prices)
    mdd = max_drawdown(prices)
    if mdd == 0 or np.isnan(mdd):
        return float("nan")
    return ann_ret / abs(mdd)


def beta(prices: pd.Series, benchmark: pd.Series) -> float:
    """Beta of prices vs benchmark (e.g. VOO)."""
    r = daily_returns(prices)
    rb = daily_returns(benchmark)
    aligned = pd.concat([r, rb], axis=1).dropna()
    if len(aligned) < 20:
        return float("nan")
    cov = aligned.cov().iloc[0, 1]
    var = aligned.iloc[:, 1].var()
    return float(cov / var) if var != 0 else float("nan")


def correlation(prices_a: pd.Series, prices_b: pd.Series) -> float:
    """Pearson correlation of daily returns between two price series."""
    r_a = daily_returns(prices_a)
    r_b = daily_returns(prices_b)
    aligned = pd.concat([r_a, r_b], axis=1).dropna()
    if len(aligned) < 20:
        return float("nan")
    return float(aligned.corr().iloc[0, 1])


# ── Return-based primitives (canonical; for callers holding a returns series) ────
# These exist so commands that already have a daily-returns series (e.g. portfolio
# simulations in advise) use the SAME geometric definitions as the price-based
# functions above — avoiding the arithmetic-mean drift that made commands disagree.

def ann_return_from_returns(r: pd.Series) -> float:
    """Geometric (compound) annualized return from a daily-returns series."""
    if r is None or r.empty:
        return float("nan")
    n_years = len(r) / TRADING_DAYS
    return float((1 + r).prod() ** (1 / n_years) - 1) if n_years > 0 else float("nan")


def ann_vol_from_returns(r: pd.Series) -> float:
    if r is None or r.empty:
        return float("nan")
    return float(r.std() * np.sqrt(TRADING_DAYS))


def sharpe_from_returns(r: pd.Series, rfr: Optional[float] = None) -> float:
    if rfr is None:
        rfr = _get_rfr()
    vol = ann_vol_from_returns(r)
    if not vol or np.isnan(vol):
        return float("nan")
    return (ann_return_from_returns(r) - rfr) / vol


def sortino_from_returns(r: pd.Series, rfr: Optional[float] = None) -> float:
    if rfr is None:
        rfr = _get_rfr()
    if r is None or r.empty:
        return float("nan")
    downside = r[r < 0]
    if downside.empty:
        return float("inf")
    dd = float(downside.std() * np.sqrt(TRADING_DAYS))
    if dd == 0:
        return float("nan")
    return (ann_return_from_returns(r) - rfr) / dd


def max_drawdown_from_returns(r: pd.Series) -> float:
    if r is None or r.empty:
        return float("nan")
    cum = (1 + r).cumprod()
    return float(((cum - cum.cummax()) / cum.cummax()).min())


# ── Full metrics bundle ────────────────────────────────────────────────────────

def compute_metrics(
    prices: pd.Series,
    benchmark: Optional[pd.Series] = None,
    rfr: Optional[float] = None,
    label: Optional[str] = None,
) -> dict:
    """
    Compute the standard metrics bundle for a price series.

    Returns a dict with keys: total_return, annualized_return, annualized_volatility,
    sharpe_ratio, sortino_ratio, max_drawdown, calmar_ratio, beta (if benchmark
    provided), plus period metadata.

    All intermediate values are computed from a single daily_returns() call to
    avoid redundant pct_change() passes over the data.
    """
    if rfr is None:
        rfr = _get_rfr()

    r = daily_returns(prices)
    total_ret = float(prices.iloc[-1] / prices.iloc[0] - 1) if len(prices) >= 2 else float("nan")

    n_years = len(r) / TRADING_DAYS
    ann_ret = float((1 + r).prod() ** (1 / n_years) - 1) if n_years > 0 else float("nan")
    ann_vol = float(r.std() * np.sqrt(TRADING_DAYS)) if not r.empty else float("nan")

    sharpe = _round((ann_ret - rfr) / ann_vol) if ann_vol and not np.isnan(ann_vol) else None

    downside = r[r < 0]
    dd_dev = float(downside.std() * np.sqrt(TRADING_DAYS)) if not downside.empty else float("nan")
    sortino = _round((ann_ret - rfr) / dd_dev) if dd_dev and not np.isnan(dd_dev) else None

    cum = (1 + r).cumprod()
    mdd = float(((cum - cum.cummax()) / cum.cummax()).min()) if not r.empty else float("nan")
    calmar = _round(ann_ret / abs(mdd)) if mdd and not np.isnan(mdd) else None

    result = {
        "ticker": label or str(prices.name),
        "start_date": prices.index[0].strftime("%Y-%m-%d") if not prices.empty else None,
        "end_date": prices.index[-1].strftime("%Y-%m-%d") if not prices.empty else None,
        "trading_days": int(len(r)),
        "total_return": _round(total_ret),
        "annualized_return": _round(ann_ret),
        "annualized_volatility": _round(ann_vol),
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown": _round(mdd),
        "calmar_ratio": calmar,
    }
    if benchmark is not None:
        result["beta"] = _round(beta(prices, benchmark))
        result["correlation_to_benchmark"] = _round(correlation(prices, benchmark))
    return result


# ── Trailing return windows ────────────────────────────────────────────────────

_WINDOWS_DAYS = {"1M": 30, "3M": 91, "6M": 182, "1Y": 365, "3Y": 365*3, "5Y": 365*5, "10Y": 365*10}

# Trailing-window lengths in trading days, for risk-adjusted multi-window comparison.
_MULTI_WINDOWS = {"1Y": TRADING_DAYS, "3Y": TRADING_DAYS*3, "5Y": TRADING_DAYS*5, "10Y": TRADING_DAYS*10}


def multi_window_metrics(prices: pd.Series, rfr: Optional[float] = None) -> dict:
    """
    Annualized return + Sharpe over trailing 1Y/3Y/5Y/10Y windows.

    Lets a single position be judged for *consistency* rather than one lookback —
    a name that only looks good (or bad) in one window is flagged by the spread.
    Windows longer than the available history return None.
    Returns {window: {ann_return, sharpe, n_days} | None}.
    """
    if rfr is None:
        rfr = _get_rfr()
    r = daily_returns(prices)
    out: dict = {}
    for label, n in _MULTI_WINDOWS.items():
        if len(r) < n:
            out[label] = None
            continue
        wr = r.iloc[-n:]
        ann_ret = ann_return_from_returns(wr)
        ann_vol = ann_vol_from_returns(wr)
        sharpe = _round((ann_ret - rfr) / ann_vol) if ann_vol and not np.isnan(ann_vol) else None
        out[label] = {"ann_return": _round(ann_ret), "sharpe": sharpe, "n_days": int(n)}
    return out


def trailing_return_windows(prices: pd.Series) -> dict:
    """
    Compute trailing returns for standard windows plus YTD.
    Returns {window: float|None} — e.g. {"1M": 0.05, "YTD": -0.02, ...}
    """
    if prices.empty or len(prices) < 2:
        return {w: None for w in list(_WINDOWS_DAYS) + ["YTD"]}

    latest = prices.index[-1]
    latest_price = prices.iloc[-1]
    results = {}

    for window, days in _WINDOWS_DAYS.items():
        cutoff = latest - pd.Timedelta(days=days)
        sub = prices[prices.index <= cutoff]
        results[window] = _pct(latest_price, sub.iloc[-1]) if not sub.empty else None

    # YTD
    ytd_start = pd.Timestamp(latest.year, 1, 1)
    sub_ytd = prices[prices.index >= ytd_start]
    results["YTD"] = _pct(latest_price, sub_ytd.iloc[0]) if len(sub_ytd) >= 2 else None

    return results


def _pct(current, base) -> float:
    return float(current / base - 1)


def _round(v, decimals=6):
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return None
    return round(float(v), decimals)


# ── Portfolio-level analytics ─────────────────────────────────────────────────

def correlation_matrix(
    price_map: dict,  # {ticker: pd.Series of prices}
) -> dict:
    """
    Compute all pairwise correlations across a dict of price series.

    Returns:
      matrix   — nested dict {ticker_a: {ticker_b: corr}}
      tickers  — ordered list of tickers (same order as rows/cols)
      rows     — list-of-lists for table rendering
    """
    tickers = list(price_map.keys())
    n = len(tickers)

    # Build daily returns DataFrame aligned on common dates
    returns_df = pd.concat(
        [daily_returns(price_map[t]).rename(t) for t in tickers],
        axis=1,
    ).dropna()

    corr_df = returns_df.corr()

    matrix = {}
    for ta in tickers:
        matrix[ta] = {}
        for tb in tickers:
            v = corr_df.loc[ta, tb] if ta in corr_df.index and tb in corr_df.columns else float("nan")
            matrix[ta][tb] = _round(v)

    rows = []
    for ta in tickers:
        row = [_round(corr_df.loc[ta, tb]) if ta in corr_df.index and tb in corr_df.columns else None
               for tb in tickers]
        rows.append(row)

    return {"tickers": tickers, "matrix": matrix, "rows": rows}


def rolling_window_snapshot(
    prices: pd.Series,
    windows: Optional[dict] = None,
) -> dict:
    """
    Compute annualised return and volatility over trailing windows (in trading days).

    windows: {label: trading_days}, default {"30d": 30, "90d": 90, "1Y": 252}
    Returns {label: {annualized_return, annualized_volatility, sharpe_ratio}}
    """
    if windows is None:
        windows = {"30d": 30, "90d": 90, "1Y": 252}

    r = daily_returns(prices)
    rfr = _get_rfr()
    result = {}

    for label, n in windows.items():
        if len(r) < n:
            result[label] = {"annualized_return": None, "annualized_volatility": None, "sharpe_ratio": None}
            continue
        window_r = r.iloc[-n:]
        ann_ret = float((1 + window_r).prod() ** (TRADING_DAYS / n) - 1)
        ann_vol = float(window_r.std() * np.sqrt(TRADING_DAYS))
        sharpe = _round((ann_ret - rfr) / ann_vol) if ann_vol else None
        result[label] = {
            "annualized_return": _round(ann_ret),
            "annualized_volatility": _round(ann_vol),
            "sharpe_ratio": sharpe,
        }

    return result


def portfolio_position_metrics(
    positions: list,
    price_map: dict,
    benchmark_series: pd.Series,
) -> list:
    """
    Compute per-position metrics for all ETFs in a portfolio.

    Returns a list of dicts, one per position, including:
    trailing returns (1Y), risk metrics, rolling window snapshots, beta, correlation.
    """
    results = []
    for pos in positions:
        ticker = pos["ticker"].upper()
        weight = pos["weight"]
        theme = pos.get("theme", "")
        prices = price_map.get(ticker)

        if prices is None or prices.empty:
            results.append({
                "ticker": ticker,
                "weight": weight,
                "theme": theme,
                "error": "no price data",
            })
            continue

        trailing = trailing_return_windows(prices)
        metrics = compute_metrics(prices, benchmark=benchmark_series)
        rolling = rolling_window_snapshot(prices)
        windows = multi_window_metrics(prices)

        results.append({
            "ticker": ticker,
            "weight": weight,
            "theme": theme,
            "trailing_returns": trailing,
            "metrics": metrics,
            "rolling": rolling,
            "windows": windows,
        })

    return results


def theme_attribution(
    positions: list,
    price_map: dict,
    trailing_days: int = 252,
) -> list:
    """
    Compute per-theme return attribution over trailing_days trading days.

    Attribution: each position contributes weight × trailing_return.
    Returns list of {theme, tickers, weight, return, contribution} sorted by contribution.
    """
    from collections import defaultdict

    # Compute trailing return for each ticker over the window
    ticker_returns = {}
    for pos in positions:
        ticker = pos["ticker"].upper()
        prices = price_map.get(ticker)
        if prices is None or prices.empty or len(prices) < trailing_days:
            ticker_returns[ticker] = None
            continue
        window = prices.iloc[-trailing_days:]
        ticker_returns[ticker] = float(window.iloc[-1] / window.iloc[0] - 1)

    # Group by theme
    theme_data = defaultdict(lambda: {"tickers": [], "total_weight": 0.0, "weighted_return": 0.0})
    for pos in positions:
        ticker = pos["ticker"].upper()
        weight = pos["weight"]
        theme = pos.get("theme", "Other")
        r = ticker_returns.get(ticker)

        theme_data[theme]["tickers"].append(ticker)
        theme_data[theme]["total_weight"] += weight
        if r is not None:
            theme_data[theme]["weighted_return"] += weight * r

    results = []
    for theme, d in theme_data.items():
        tw = d["total_weight"]
        wr = d["weighted_return"]
        theme_return = wr / tw if tw > 0 else None  # avg return within theme
        results.append({
            "theme": theme,
            "tickers": d["tickers"],
            "theme_weight": _round(tw),
            "theme_return": _round(theme_return),
            "portfolio_contribution": _round(wr),  # weight × return = contribution to port
        })

    results.sort(key=lambda x: x["portfolio_contribution"] or 0, reverse=True)
    return results


# ── Portfolio-level weighted return series ────────────────────────────────────

def portfolio_returns_series(
    price_map: dict,  # {ticker: pd.Series of prices}
    weights: dict,    # {ticker: float}
) -> pd.Series:
    """
    Compute a daily portfolio return series from a dict of price series and weights.
    Weights need not sum to 1 (they're normalised internally).
    """
    aligned = pd.concat(price_map.values(), axis=1, keys=price_map.keys()).dropna()
    if aligned.empty:
        return pd.Series(dtype=float)

    daily = aligned.pct_change().dropna()
    total_weight = sum(weights.get(t, 0) for t in aligned.columns)
    if total_weight == 0:
        return pd.Series(dtype=float)

    w = pd.Series({t: weights.get(t, 0) / total_weight for t in aligned.columns})
    return (daily * w).sum(axis=1)
