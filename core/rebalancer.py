"""
Rebalancing calculator.

Given a target portfolio and total portfolio value, computes:
  - Ideal dollar allocation per position
  - Ideal share count at current prices
  - If current weights are provided: drift from target + recommended trades

Current prices are pulled from the SQLite price cache (latest close).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import PORTFOLIOS
from core.data_fetcher import get_close_series

logger = logging.getLogger(__name__)

_DRIFT_THRESHOLD = 0.001


def _latest_price(ticker: str) -> tuple[Optional[float], Optional[str]]:
    """Return (price, date_str) from the latest cached close for a ticker."""
    series = get_close_series(ticker, start=date.today() - timedelta(days=7))
    if series.empty:
        return None, None
    return float(series.iloc[-1]), series.index[-1].strftime("%Y-%m-%d")


def compute_rebalance(
    portfolio_name: str,
    total_value: float,
    current_weights: Optional[Dict[str, float]] = None,
    portfolio_override: Optional[List[Dict]] = None,
) -> dict:
    """
    Compute a rebalance plan.

    Args:
        portfolio_name:   Name of the target portfolio in config.
        total_value:      Total portfolio value in dollars.
        current_weights:  {ticker: current_weight} if user wants drift analysis.
                          Weights should sum to ~1. If None, assumes at target.
        portfolio_override: Custom positions list (bypasses config lookup).

    Returns a structured dict with per-position plan + summary.
    """
    positions = portfolio_override or PORTFOLIOS.get(portfolio_name)
    if not positions:
        raise ValueError(f"Portfolio '{portfolio_name}' not found")

    # Normalise weights to sum to 1 (excluding any flex/cash positions)
    total_w = sum(p["weight"] for p in positions)
    positions_data = []

    data_freshness = None

    for pos in positions:
        ticker = pos["ticker"].upper()
        target_w = pos["weight"] / total_w  # normalised
        target_dollars = target_w * total_value

        price, price_date_str = _latest_price(ticker)
        target_shares = target_dollars / price if price else None

        if price_date_str and (data_freshness is None or price_date_str > data_freshness):
            data_freshness = price_date_str

        row = {
            "ticker": ticker,
            "theme": pos.get("theme", ""),
            "target_weight": round(target_w, 6),
            "target_dollars": round(target_dollars, 2),
            "current_price": round(price, 4) if price else None,
            "target_shares": round(target_shares, 2) if target_shares else None,
        }

        if current_weights is not None:
            curr_w = current_weights.get(ticker, 0.0)
            drift = target_w - curr_w
            curr_dollars = curr_w * total_value
            trade_dollars = drift * total_value

            if drift > _DRIFT_THRESHOLD:
                direction = "BUY"
            elif drift < -_DRIFT_THRESHOLD:
                direction = "SELL"
            else:
                direction = "HOLD"

            row.update({
                "current_weight": round(curr_w, 6),
                "current_dollars": round(curr_dollars, 2),
                "drift": round(drift, 6),
                "trade_direction": direction,
                "trade_dollars": round(abs(trade_dollars), 2),
                "trade_shares": round(abs(trade_dollars) / price, 2) if price and abs(trade_dollars) > 0 else 0.0,
            })

        positions_data.append(row)

    # Sort by abs(drift) descending if drift mode, else by target weight
    if current_weights is not None:
        positions_data.sort(key=lambda x: abs(x.get("drift", 0)), reverse=True)
    else:
        positions_data.sort(key=lambda x: x["target_weight"], reverse=True)

    # Summary
    summary = {
        "total_value": total_value,
        "position_count": len(positions_data),
        "data_freshness": data_freshness,
    }
    if current_weights is not None:
        buys  = [p for p in positions_data if p.get("trade_direction") == "BUY"]
        sells = [p for p in positions_data if p.get("trade_direction") == "SELL"]
        holds = [p for p in positions_data if p.get("trade_direction") == "HOLD"]
        summary.update({
            "total_buy_dollars":  round(sum(p["trade_dollars"] for p in buys), 2),
            "total_sell_dollars": round(sum(p["trade_dollars"] for p in sells), 2),
            "buys": len(buys),
            "sells": len(sells),
            "holds": len(holds),
            "max_drift_ticker": positions_data[0]["ticker"] if positions_data else None,
            "max_drift": positions_data[0].get("drift") if positions_data else None,
        })

    return {
        "portfolio": portfolio_name,
        "mode": "drift" if current_weights is not None else "target",
        "positions": positions_data,
        "summary": summary,
    }


def parse_current_weights(weights_str: str) -> Dict[str, float]:
    """
    Parse a compact weight string: 'VOO:0.32,NLR:0.13,SMH:0.14'
    Returns {ticker: weight}.
    """
    result = {}
    for part in weights_str.split(","):
        part = part.strip()
        if ":" in part:
            ticker, w = part.split(":", 1)
            result[ticker.strip().upper()] = float(w.strip())
    return result
