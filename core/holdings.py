"""
ETF holdings decomposition and portfolio overlap analysis.

Data priority:
  1. yfinance live fetch (cached in SQLite with 7-day TTL)
  2. seed_holdings.json (static fallback — manually seeded top-10 per ETF)

Overlap algorithm (from brief):
  For candidate C and portfolio P:
  1. Get holdings of C: {symbol: weight}
  2. Aggregate holdings of P: for each ETF, multiply stock_weight × ETF_allocation
  3. overlap_coefficient = sum of min(C[s], P[s]) for all shared symbols s

Effective concentration after adding C at allocation W:
  1. Scale existing portfolio to (1 - W)
  2. Add C's holdings at W
  3. Report resulting top-N stock exposures
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import PORTFOLIOS
from core.data_fetcher import get_holdings

logger = logging.getLogger(__name__)

_SEED_PATH = Path(__file__).parent.parent / "data" / "seed_holdings.json"
_seed_cache: Optional[dict] = None


def _load_seed() -> dict:
    global _seed_cache
    if _seed_cache is None:
        if _SEED_PATH.exists():
            with open(_SEED_PATH) as f:
                _seed_cache = json.load(f)
        else:
            _seed_cache = {}
    return _seed_cache


def get_etf_holdings(ticker: str) -> List[Dict]:
    """
    Return top holdings for an ETF as [{symbol, weight, name}].

    Tries yfinance (cached), falls back to seed data.
    """
    ticker = ticker.upper()
    holdings = get_holdings(ticker)

    if not holdings:
        seed = _load_seed()
        holdings = seed.get(ticker, [])
        if holdings:
            logger.debug("Using seed holdings for %s (%d positions)", ticker, len(holdings))
        else:
            logger.warning("No holdings data available for %s", ticker)

    return holdings


def holdings_as_weight_map(ticker: str) -> Dict[str, float]:
    """Return {symbol: weight} dict for an ETF (weights as fractions, e.g. 0.07)."""
    h = get_etf_holdings(ticker)
    return {item["symbol"]: item["weight"] for item in h}


# ── Portfolio-level aggregation ───────────────────────────────────────────────

def aggregate_portfolio_holdings(
    portfolio_name: str,
    portfolio_override: Optional[List[Dict]] = None,
) -> Dict[str, float]:
    """
    Aggregate all ETFs in a portfolio into a single stock-level weight map.

    Each stock's effective weight = sum(ETF_allocation × stock_weight_in_ETF).
    Returns {symbol: effective_weight}.

    portfolio_override: pass a list of {ticker, weight} to use instead of config.
    """
    positions = portfolio_override or PORTFOLIOS.get(portfolio_name, [])
    if not positions:
        raise ValueError(f"Portfolio '{portfolio_name}' not found")

    aggregated: Dict[str, float] = {}

    for pos in positions:
        ticker = pos["ticker"].upper()
        etf_alloc = float(pos["weight"])
        holdings = holdings_as_weight_map(ticker)

        if not holdings:
            logger.warning("No holdings for %s — skipping in aggregation", ticker)
            continue

        for symbol, stock_weight in holdings.items():
            sym = _normalise_symbol(symbol)
            aggregated[sym] = aggregated.get(sym, 0.0) + etf_alloc * stock_weight

    return dict(sorted(aggregated.items(), key=lambda x: x[1], reverse=True))


def _normalise_symbol(sym: str) -> str:
    """Normalise ticker variations: BRK.B → BRK-B, etc."""
    return sym.upper().replace(".", "-")


# ── Overlap analysis ──────────────────────────────────────────────────────────

def overlap_analysis(
    candidate_ticker: str,
    portfolio_name: str,
    portfolio_override: Optional[List[Dict]] = None,
) -> Dict:
    """
    Compute overlap between a candidate ETF and an existing portfolio.

    Returns:
      overlap_coefficient     — fraction of candidate's weight that already appears in portfolio
      shared_holdings         — [{symbol, candidate_weight, portfolio_weight, name}] sorted by overlap contribution
      unique_to_candidate     — [{symbol, weight, name}] holdings not in portfolio
      candidate_holding_count — number of holdings in candidate
    """
    candidate_ticker = candidate_ticker.upper()
    candidate_holdings_raw = get_etf_holdings(candidate_ticker)
    candidate_map = {_normalise_symbol(h["symbol"]): h for h in candidate_holdings_raw}
    portfolio_map = aggregate_portfolio_holdings(portfolio_name, portfolio_override)

    shared = []
    overlap_sum = 0.0

    for sym, h in candidate_map.items():
        c_weight = h["weight"]
        p_weight = portfolio_map.get(sym, 0.0)
        if p_weight > 0:
            contribution = min(c_weight, p_weight)
            overlap_sum += contribution
            shared.append({
                "symbol": sym,
                "name": h.get("name", sym),
                "candidate_weight": round(c_weight, 6),
                "portfolio_weight": round(p_weight, 6),
                "overlap_contribution": round(contribution, 6),
            })

    shared.sort(key=lambda x: x["overlap_contribution"], reverse=True)

    unique = [
        {"symbol": _normalise_symbol(h["symbol"]), "weight": h["weight"], "name": h.get("name", "")}
        for h in candidate_holdings_raw
        if _normalise_symbol(h["symbol"]) not in portfolio_map
    ]
    unique.sort(key=lambda x: x["weight"], reverse=True)

    return {
        "candidate_ticker": candidate_ticker,
        "portfolio": portfolio_name,
        "overlap_coefficient": round(overlap_sum, 6),
        "shared_holdings": shared,
        "unique_to_candidate": unique,
        "candidate_holding_count": len(candidate_holdings_raw),
        "portfolio_stock_count": len(portfolio_map),
    }


# ── Effective concentration after adding candidate ────────────────────────────

def effective_concentration(
    candidate_ticker: str,
    portfolio_name: str,
    candidate_allocation: float,
    top_n: int = 10,
    portfolio_override: Optional[List[Dict]] = None,
) -> Dict:
    """
    Compute effective single-stock concentration if candidate is added at
    candidate_allocation weight (e.g. 0.05 = 5%).

    Existing portfolio is scaled to (1 - candidate_allocation).

    Returns:
      top_holdings       — [{symbol, effective_weight, name}] top_n by weight
      max_single_stock   — highest single-stock effective weight
      candidate_ticker   — as provided
      candidate_allocation
    """
    candidate_ticker = candidate_ticker.upper()

    # Scale existing portfolio
    existing = aggregate_portfolio_holdings(portfolio_name, portfolio_override)
    scale = 1.0 - candidate_allocation
    scaled_existing: Dict[str, float] = {sym: w * scale for sym, w in existing.items()}

    # Add candidate at its allocation
    candidate_holdings_raw = get_etf_holdings(candidate_ticker)
    combined = dict(scaled_existing)
    for h in candidate_holdings_raw:
        sym = _normalise_symbol(h["symbol"])
        combined[sym] = combined.get(sym, 0.0) + candidate_allocation * h["weight"]

    # Sort and return top-N
    sorted_holdings = sorted(combined.items(), key=lambda x: x[1], reverse=True)

    # Build name lookup
    name_map: Dict[str, str] = {}
    for h in candidate_holdings_raw:
        name_map[_normalise_symbol(h["symbol"])] = h.get("name", "")

    top = [
        {"symbol": sym, "effective_weight": round(w, 6), "name": name_map.get(sym, "")}
        for sym, w in sorted_holdings[:top_n]
    ]

    return {
        "candidate_ticker": candidate_ticker,
        "candidate_allocation": candidate_allocation,
        "portfolio": portfolio_name,
        "top_holdings": top,
        "max_single_stock": round(sorted_holdings[0][1], 6) if sorted_holdings else 0.0,
        "max_single_stock_symbol": sorted_holdings[0][0] if sorted_holdings else None,
    }


# ── Portfolio holdings table ──────────────────────────────────────────────────

def portfolio_holdings_table(
    portfolio_name: str,
    top_n: int = 20,
    portfolio_override: Optional[List[Dict]] = None,
) -> List[Dict]:
    """
    Return top-N effective stock exposures across the entire portfolio.

    Returns [{symbol, effective_weight, name}].
    """
    positions = portfolio_override or PORTFOLIOS.get(portfolio_name, [])

    name_map: Dict[str, str] = {}
    aggregated: Dict[str, float] = {}
    for pos in positions:
        ticker = pos["ticker"].upper()
        etf_alloc = float(pos["weight"])
        etf_holdings = get_etf_holdings(ticker)
        if not etf_holdings:
            logger.warning("No holdings for %s — skipping in aggregation", ticker)
            continue
        for h in etf_holdings:
            sym = _normalise_symbol(h.get("symbol", ""))
            if not sym:
                continue
            if h.get("name"):
                name_map[sym] = h["name"]
            aggregated[sym] = aggregated.get(sym, 0.0) + etf_alloc * float(h.get("weight", 0))

    aggregated = dict(sorted(aggregated.items(), key=lambda x: x[1], reverse=True))

    return [
        {"symbol": sym, "effective_weight": round(w, 6), "name": name_map.get(sym, "")}
        for sym, w in list(aggregated.items())[:top_n]
    ]
