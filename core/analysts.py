"""
Analyst consensus, price targets, and rating changes via yfinance.
Results are cached in SQLite for 1 day (analyst data updates daily at most).
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

_REC_LABELS = {
    "strong_buy":  "Strong Buy",
    "buy":         "Buy",
    "hold":        "Hold",
    "sell":        "Sell",
    "strong_sell": "Strong Sell",
}


def _fetch_from_yf(ticker: str) -> dict:
    t    = yf.Ticker(ticker)
    info = t.info or {}

    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
    target_mean   = info.get("targetMeanPrice")
    target_high   = info.get("targetHighPrice")
    target_low    = info.get("targetLowPrice")
    target_median = info.get("targetMedianPrice")
    rec_key       = info.get("recommendationKey", "")
    rec_score     = info.get("recommendationMean")   # 1.0 strong buy → 5.0 strong sell
    num_analysts  = info.get("numberOfAnalystOpinions") or 0

    upside = None
    if target_mean and current_price:
        upside = (target_mean - current_price) / current_price

    consensus: Dict[str, int] = {}
    try:
        rs = t.recommendations_summary
        if rs is not None and not rs.empty:
            row = rs.iloc[-1]
            consensus = {
                "strong_buy":  int(row.get("strongBuy",  0)),
                "buy":         int(row.get("buy",         0)),
                "hold":        int(row.get("hold",        0)),
                "sell":        int(row.get("sell",        0)),
                "strong_sell": int(row.get("strongSell", 0)),
            }
    except Exception:
        pass

    recent_changes: List[dict] = []
    try:
        ud = t.upgrades_downgrades
        if ud is not None and not ud.empty:
            for idx, row in ud.head(10).iterrows():
                recent_changes.append({
                    "date":       str(idx.date()) if hasattr(idx, "date") else str(idx)[:10],
                    "firm":       row.get("Firm", ""),
                    "from_grade": row.get("FromGrade", ""),
                    "to_grade":   row.get("ToGrade", ""),
                    "action":     row.get("Action", ""),
                })
    except Exception:
        pass

    return {
        "ticker":         ticker.upper(),
        "current_price":  current_price,
        "target_mean":    target_mean,
        "target_high":    target_high,
        "target_low":     target_low,
        "target_median":  target_median,
        "upside_to_mean": upside,
        "recommendation": rec_key,
        "rec_score":      rec_score,
        "analyst_count":  num_analysts,
        "consensus":      consensus,
        "recent_changes": recent_changes,
        "fetched":        date.today().isoformat(),
    }


def get_analyst_data(ticker: str) -> dict:
    """Return analyst data for ticker, using 1-day SQLite cache."""
    return cached_json("analyst_data", ticker, _fetch_from_yf, ttl_days=1)


def get_analyst_data_multi(tickers: List[str]) -> Dict[str, dict]:
    return {t.upper(): get_analyst_data(t) for t in tickers}


def rec_label(key: str, score: float | None = None) -> str:
    label = _REC_LABELS.get(key, key.replace("_", " ").title() if key else "N/A")
    return f"{label} ({score:.1f})" if score is not None else label
