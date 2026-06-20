"""
Portfolio optimizer: find optimal weights for a set of ETFs.

Objectives (all long-only, weights sum to 1):
  sharpe            — Maximize Sharpe ratio (tangency portfolio)
  sortino           — Maximize Sortino ratio
  min-vol           — Minimize portfolio annualized volatility
  min-cvar          — Minimize CVaR at a given confidence level
  min-drawdown      — Minimize maximum peak-to-trough drawdown
  max-return        — Maximize expected annualized return
  quadratic-utility — Maximize E[R] - (λ/2)·Var[R]
  omega             — Maximize Omega ratio

All price data flows through the existing SQLite cache.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize, OptimizeResult

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data_fetcher import get_close_series
from core.analytics import TRADING_DAYS, _round
from core.macro import get_risk_free_rate

logger = logging.getLogger(__name__)

OBJECTIVES = frozenset({
    "sharpe", "sortino", "min-vol", "min-cvar", "min-drawdown",
    "max-return", "quadratic-utility", "omega",
})

_DEFAULT_LOOKBACK_DAYS = 365 * 3  # 3Y default for optimization


# ── Public entry point ─────────────────────────────────────────────────────────

def optimize(
    tickers: List[str],
    objective: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    current_weights: Optional[Dict[str, float]] = None,
    confidence: float = 0.95,
    risk_aversion: float = 3.0,
    min_weight: float = 0.0,
    max_weight: float = 1.0,
    per_min: Optional[Dict[str, float]] = None,
    per_max: Optional[Dict[str, float]] = None,
) -> dict:
    """
    Find optimal portfolio weights for the given objective.

    Args:
        tickers:         List of ETF tickers to optimize across.
        objective:       One of OBJECTIVES.
        start:           History start date (default: 3 years ago).
        end:             History end date (default: today).
        current_weights: {ticker: weight} for comparison; need not sum to 1.
        confidence:      Tail probability for CVaR (default 0.95 = worst 5%).
        risk_aversion:   λ for quadratic-utility objective (default 3.0).
        min_weight:      Lower bound per ticker (default 0.0 = long-only).
        max_weight:      Upper bound per ticker (default 1.0).

    Returns a structured dict with optimal weights, metrics comparison,
    and optimization diagnostics.
    """
    objective = objective.lower()
    if objective not in OBJECTIVES:
        raise ValueError(f"Unknown objective '{objective}'. Valid: {sorted(OBJECTIVES)}")

    tickers = [t.upper() for t in tickers]

    if start is None:
        start = date.today() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
    if end is None:
        end = date.today()

    # ── Fetch and align price data ─────────────────────────────────────────────
    price_map: Dict[str, pd.Series] = {}
    for t in tickers:
        s = get_close_series(t, start=start, end=end)
        if not s.empty:
            price_map[t] = s
        else:
            logger.warning("No price data for %s — excluded from optimization", t)

    if len(price_map) < 2:
        raise ValueError(f"Need ≥2 tickers with price data; got {len(price_map)}")

    price_df = pd.concat(price_map.values(), axis=1, keys=price_map.keys()).dropna()
    if len(price_df) < 60:
        raise ValueError(
            f"Only {len(price_df)} overlapping trading days — need at least 60"
        )

    usable = list(price_df.columns)
    returns = price_df.pct_change().dropna().values  # shape (T, N)
    n = len(usable)

    rfr = get_risk_free_rate()
    daily_rfr = (1 + rfr) ** (1 / TRADING_DAYS) - 1

    # ── Objective functions ────────────────────────────────────────────────────

    def _ann_ret_vol(w: np.ndarray) -> Tuple[float, float]:
        r = returns @ w
        ann_ret = float((1 + r).prod() ** (TRADING_DAYS / len(r)) - 1)
        ann_vol = float(r.std() * np.sqrt(TRADING_DAYS))
        return ann_ret, ann_vol

    def neg_sharpe(w: np.ndarray) -> float:
        ann_ret, ann_vol = _ann_ret_vol(w)
        return 0.0 if ann_vol < 1e-8 else -(ann_ret - rfr) / ann_vol

    def neg_sortino(w: np.ndarray) -> float:
        ann_ret, _ = _ann_ret_vol(w)
        r = returns @ w
        downside = r[r < 0]
        if len(downside) < 2:
            return -1e6
        dd_vol = float(downside.std() * np.sqrt(TRADING_DAYS))
        return 0.0 if dd_vol < 1e-8 else -(ann_ret - rfr) / dd_vol

    def port_vol(w: np.ndarray) -> float:
        return float((returns @ w).std() * np.sqrt(TRADING_DAYS))

    def neg_cvar(w: np.ndarray) -> float:
        r = returns @ w
        q = np.percentile(r, (1 - confidence) * 100)
        tail = r[r <= q]
        # Maximize tail mean = minimize CVaR risk (tail mean is negative)
        return -float(tail.mean()) if len(tail) > 0 else 0.0

    def neg_return(w: np.ndarray) -> float:
        ann_ret, _ = _ann_ret_vol(w)
        return -ann_ret

    def neg_utility(w: np.ndarray) -> float:
        ann_ret, ann_vol = _ann_ret_vol(w)
        return -(ann_ret - (risk_aversion / 2) * ann_vol ** 2)

    def neg_omega(w: np.ndarray) -> float:
        r = returns @ w
        gains = np.maximum(r - daily_rfr, 0).sum()
        losses = np.maximum(daily_rfr - r, 0).sum()
        return -(gains / losses) if losses > 1e-8 else -1e6

    def min_drawdown(w: np.ndarray) -> float:
        r = returns @ w
        cum = (1 + r).cumprod()
        running_max = np.maximum.accumulate(cum)
        mdd = float(((cum - running_max) / running_max).min())
        return abs(mdd)  # mdd is negative; abs(mdd) minimized = smallest peak-to-trough decline

    obj_fn = {
        "sharpe":            neg_sharpe,
        "sortino":           neg_sortino,
        "min-vol":           port_vol,
        "min-cvar":          neg_cvar,
        "min-drawdown":      min_drawdown,
        "max-return":        neg_return,
        "quadratic-utility": neg_utility,
        "omega":             neg_omega,
    }[objective]

    # ── Run optimization with multiple starts ──────────────────────────────────
    _per_min = per_min or {}
    _per_max = per_max or {}
    for t in usable:
        lo = _per_min.get(t, min_weight)
        hi = _per_max.get(t, max_weight)
        if lo > hi:
            raise ValueError(f"Infeasible bounds for {t}: min {lo} > max {hi}")

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(_per_min.get(t, min_weight), _per_max.get(t, max_weight)) for t in usable]

    # Convex objectives have a unique global minimum — single start is sufficient.
    # Non-convex objectives (Sharpe, Sortino, Omega, CVaR) benefit from multiple starts.
    _CONVEX = {"min-vol", "max-return"}
    rng = np.random.default_rng(42)
    starts = [np.full(n, 1.0 / n)]
    if objective not in _CONVEX:
        for _ in range(4):
            raw = rng.dirichlet(np.ones(n))
            raw = np.clip(raw, min_weight, max_weight)
            raw /= raw.sum()
            starts.append(raw)

    best: Optional[OptimizeResult] = None
    for x0 in starts:
        res = minimize(
            obj_fn, x0=x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-9},
        )
        if best is None or res.fun < best.fun:
            best = res

    # Clip to the per-ticker bounds (not just [0,1]) and renormalise. A single
    # clip-then-normalise can push a capped ticker back above its bound, so
    # iterate — converges in 2–3 passes for any realistic constraint set.
    lo_arr = np.array([b[0] for b in bounds])
    hi_arr = np.array([b[1] for b in bounds])
    opt_w = best.x
    for _ in range(5):
        opt_w = np.clip(opt_w, lo_arr, hi_arr)
        s = opt_w.sum()
        if s <= 0 or abs(s - 1.0) < 1e-9:
            break
        opt_w = opt_w / s
    opt_w = np.clip(opt_w, lo_arr, hi_arr)
    opt_w /= opt_w.sum()
    optimal_weights = {t: _round(float(w), 6) for t, w in zip(usable, opt_w)}

    # ── Metrics helper ─────────────────────────────────────────────────────────
    def _metrics(weights_dict: Dict[str, float]) -> dict:
        w = np.array([weights_dict.get(t, 0.0) for t in usable])
        if w.sum() > 0:
            w = w / w.sum()

        r = returns @ w
        ann_ret = float((1 + r).prod() ** (TRADING_DAYS / len(r)) - 1)
        ann_vol = float(r.std() * np.sqrt(TRADING_DAYS))

        sharpe = (ann_ret - rfr) / ann_vol if ann_vol > 1e-8 else None

        downside = r[r < 0]
        dd_vol = float(downside.std() * np.sqrt(TRADING_DAYS)) if len(downside) >= 2 else None
        sortino = (ann_ret - rfr) / dd_vol if dd_vol and dd_vol > 1e-8 else None

        cum = (1 + r).cumprod()
        running_max = np.maximum.accumulate(cum)
        mdd = float(((cum - running_max) / running_max).min())
        calmar = ann_ret / abs(mdd) if mdd != 0 else None

        q = np.percentile(r, (1 - confidence) * 100)
        tail = r[r <= q]
        cvar = float(tail.mean()) if len(tail) > 0 else None  # negative = loss

        gains = np.maximum(r - daily_rfr, 0).sum()
        losses = np.maximum(daily_rfr - r, 0).sum()
        omega = float(gains / losses) if losses > 1e-8 else None

        return {
            "annualized_return":    _round(ann_ret),
            "annualized_volatility": _round(ann_vol),
            "sharpe_ratio":         _round(sharpe),
            "sortino_ratio":        _round(sortino),
            "max_drawdown":         _round(mdd),
            "calmar_ratio":         _round(calmar),
            f"cvar_{int(confidence * 100)}": _round(cvar),
            "omega_ratio":          _round(omega),
        }

    # ── Assemble result ────────────────────────────────────────────────────────
    result: dict = {
        "objective": objective,
        "tickers": usable,
        "period": {
            "start": price_df.index[0].strftime("%Y-%m-%d"),
            "end":   price_df.index[-1].strftime("%Y-%m-%d"),
            "trading_days": len(returns),
        },
        "parameters": {
            "confidence":    confidence,
            "risk_aversion": risk_aversion,
            "min_weight":    min_weight,
            "max_weight":    max_weight,
            "per_min":       per_min or {},
            "per_max":       per_max or {},
        },
        "optimal_weights": optimal_weights,
        "metrics": {"optimal": _metrics(optimal_weights)},
        "optimization": {
            "success":    best.success,
            "message":    best.message,
            "iterations": best.nit,
        },
    }

    if current_weights:
        cw = {t: float(current_weights.get(t, 0.0)) for t in usable}
        cw_sum = sum(cw.values())
        if cw_sum > 0:
            cw = {t: v / cw_sum for t, v in cw.items()}
        result["current_weights"] = {t: _round(v, 6) for t, v in cw.items()}
        result["weight_changes"] = {
            t: _round(optimal_weights[t] - cw[t], 6) for t in usable
        }
        result["metrics"]["current"] = _metrics(cw)

    return result
