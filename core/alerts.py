"""
Portfolio alerts: flag conditions that warrant attention.

Computed at analysis time — no persistent storage. Designed to surface
important signals in both the CLI and web dashboard.

Alert types:
  HIGH_CORRELATION    — two positions with corr > threshold (default 0.75)
  HIGH_CONCENTRATION  — single stock effective weight > threshold (default 5%)
  HIGH_OVERLAP        — candidate ETF overlap coefficient > threshold (default 20%)
  THEME_OVERLAP       — same theme represented by multiple positions (risk of duplication)
"""
from __future__ import annotations

from typing import List, Dict, Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Thresholds ─────────────────────────────────────────────────────────────────

CORR_HIGH      = 0.75   # correlation pairs flagged
CORR_EXTREME   = 0.90   # correlation pairs flagged as critical
CONCENTRATION  = 0.05   # single-stock effective weight threshold
OVERLAP_WARN   = 0.15   # candidate ETF overlap coefficient


# ── Alert builders ─────────────────────────────────────────────────────────────

def _alert(level: str, type_: str, message: str, detail: dict) -> dict:
    return {"level": level, "type": type_, "message": message, "detail": detail}


def check_correlation_alerts(
    matrix_data: dict,
    threshold: float = CORR_HIGH,
) -> List[dict]:
    """
    Scan correlation matrix for high-correlation pairs.
    Returns list of alert dicts.
    """
    tickers = matrix_data.get("tickers", [])
    matrix  = matrix_data.get("matrix", {})
    alerts  = []
    seen    = set()

    for i, ta in enumerate(tickers):
        for j, tb in enumerate(tickers):
            if i >= j:
                continue
            pair = tuple(sorted([ta, tb]))
            if pair in seen:
                continue
            seen.add(pair)

            v = matrix.get(ta, {}).get(tb)
            if v is None:
                continue
            v = float(v)

            if v >= CORR_EXTREME:
                alerts.append(_alert(
                    "critical", "HIGH_CORRELATION",
                    f"{ta} and {tb} are nearly identical (corr={v:.2f}). "
                    f"Consider whether both are needed.",
                    {"ticker_a": ta, "ticker_b": tb, "correlation": round(v, 4)},
                ))
            elif v >= threshold:
                alerts.append(_alert(
                    "warning", "HIGH_CORRELATION",
                    f"{ta} and {tb} have high correlation (corr={v:.2f}).",
                    {"ticker_a": ta, "ticker_b": tb, "correlation": round(v, 4)},
                ))

    alerts.sort(key=lambda a: a["detail"]["correlation"], reverse=True)
    return alerts


def check_concentration_alerts(
    top_holdings: List[dict],
    threshold: float = CONCENTRATION,
) -> List[dict]:
    """Flag single-stock effective weights above threshold."""
    alerts = []
    for h in top_holdings:
        w = h.get("effective_weight", 0)
        if w > threshold:
            alerts.append(_alert(
                "warning", "HIGH_CONCENTRATION",
                f"{h['symbol']} represents {w*100:.1f}% of effective portfolio weight.",
                {"symbol": h["symbol"], "effective_weight": round(w, 6), "name": h.get("name", "")},
            ))
    return alerts


def check_theme_overlap_alerts(positions: list) -> List[dict]:
    """Flag themes represented by multiple positions (potential duplication)."""
    from collections import defaultdict
    theme_map: dict = defaultdict(list)
    for pos in positions:
        theme_map[pos.get("theme", "Other")].append(pos["ticker"].upper())

    alerts = []
    for theme, tickers in theme_map.items():
        if len(tickers) > 1:
            alerts.append(_alert(
                "info", "THEME_OVERLAP",
                f"Theme '{theme}' has {len(tickers)} positions: {', '.join(tickers)}. "
                f"Verify this is intentional diversification within the theme.",
                {"theme": theme, "tickers": tickers},
            ))
    return alerts


def run_portfolio_alerts(
    positions: list,
    matrix_data: dict,
    top_holdings: List[dict],
    corr_threshold: float = CORR_HIGH,
    conc_threshold: float = CONCENTRATION,
) -> dict:
    """
    Run all alert checks for a portfolio.

    Returns:
        {
            "critical": [...],
            "warning":  [...],
            "info":     [...],
            "total":    int,
        }
    """
    all_alerts = (
        check_correlation_alerts(matrix_data, corr_threshold)
        + check_concentration_alerts(top_holdings, conc_threshold)
        + check_theme_overlap_alerts(positions)
    )

    return {
        "critical": [a for a in all_alerts if a["level"] == "critical"],
        "warning":  [a for a in all_alerts if a["level"] == "warning"],
        "info":     [a for a in all_alerts if a["level"] == "info"],
        "total":    len(all_alerts),
    }
