"""
Macro data: market indices, rates, commodities, volatility.

All equity/futures/index series are fetched via yfinance and flow through the
same SQLite cache as ETF prices (get_close_series / get_prices).

FRED is used for fixed-income series not available on Yahoo Finance:
  - DGS2   : 2-year Treasury constant maturity yield
  - T10Y2Y : 10Y–2Y Treasury spread (recession indicator)
  - BAMLH0A0HYM2 : ICE BofA HY credit spread

Usage
-----
from core.macro import get_macro, get_risk_free_rate, get_yield_curve

series = get_macro("VIX")          # pd.Series of daily closes
rfr    = get_risk_free_rate()      # float, annualised (e.g. 0.043)
curve  = get_yield_curve()         # {"2Y": 4.12, "10Y": 4.39, "spread": 0.27}
"""
from __future__ import annotations

import functools
import logging
import os
import sys
from datetime import date, timedelta
from typing import Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import FRED_API_KEY, RISK_FREE_RATE, LOOKBACK_5Y
from core.data_fetcher import get_close_series

logger = logging.getLogger(__name__)

# ── Symbol registry ────────────────────────────────────────────────────────────
# Maps a friendly key to the yfinance symbol that flows through get_close_series().
# All of these are cached in SQLite identically to ETF tickers.

MACRO_SYMBOLS: dict[str, str] = {
    "VIX":  "^VIX",        # CBOE Volatility Index          (1990–present)
    "GOLD": "GC=F",        # Gold futures continuous         (2000–present)
    "WTI":  "CL=F",        # WTI crude oil futures           (2000–present)
    "DXY":  "DX-Y.NYB",   # US Dollar Index (ICE)           (1990–present)
    "SPX":  "^SPX",        # S&P 500 index (pure, no fees)   (1990–present)
    "NDX":  "^NDX",        # Nasdaq 100 index                (1990–present)
    "IRX":  "^IRX",        # 13-week T-bill yield (× 10 = %) (1990–present)
    "TNX":  "^TNX",        # 10Y Treasury yield  (× 10 = %) (1990–present)
    "TYX":  "^TYX",        # 30Y Treasury yield  (× 10 = %) (1990–present)
}

# Yahoo Finance yield symbols report values already in percent (e.g. 4.5 means 4.5%).
# Divide by 100 to convert to decimal for Sharpe / risk-free rate calculations.
_YIELD_KEYS = {"IRX", "TNX", "TYX"}

# FRED series IDs for data not available via yfinance
_FRED_SERIES: dict[str, str] = {
    "DGS2":          "2Y Treasury constant maturity yield (%)",
    "T10Y2Y":        "10Y minus 2Y Treasury spread (%)",
    "BAMLH0A0HYM2":  "ICE BofA HY OAS credit spread (%)",
    "DCOILWTICO":    "WTI crude spot price ($/bbl)",
}

# Energy ETFs eligible for WTI crude overlay in compare()
# NLR and URA are nuclear/uranium — WTI is the wrong commodity; excluded until uranium futures are available
ENERGY_TICKERS = {"VDE", "XLE", "XOP", "OIH"}

# Commodity-adjacent ETFs for gold overlay
GOLD_TICKERS = {"GOAU", "GDX", "GDXJ", "RING", "IAU", "GLD", "SLV"}


# ── Core fetcher ───────────────────────────────────────────────────────────────

def get_macro(
    key: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> pd.Series:
    """
    Return a daily close Series for a macro series by friendly key.

    Uses the shared SQLite cache — subsequent calls are free.
    Yield series (IRX, TNX, TYX) are returned as decimals (e.g. 0.045).

    Parameters
    ----------
    key   : one of MACRO_SYMBOLS keys (case-insensitive)
    start : start date (default: 5Y lookback)
    end   : end date   (default: today)
    """
    key = key.upper()
    if key not in MACRO_SYMBOLS:
        raise ValueError(f"Unknown macro key '{key}'. Valid keys: {list(MACRO_SYMBOLS)}")

    symbol = MACRO_SYMBOLS[key]
    if start is None:
        start = LOOKBACK_5Y

    series = get_close_series(symbol, start=start, end=end)

    if series.empty:
        logger.warning("No data returned for macro key '%s' (%s)", key, symbol)
        return series

    # Yahoo Finance reports yield values in percent — convert to decimal
    if key in _YIELD_KEYS:
        series = series / 100.0

    series.name = key
    return series


# ── Risk-free rate ─────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def get_risk_free_rate(trailing_days: int = 90) -> float:
    """
    Return the current annualised risk-free rate as a decimal (e.g. 0.043).

    Uses the trailing `trailing_days`-day average of the 13-week T-bill yield
    (^IRX) so Sharpe / Sortino ratios reflect actual market conditions rather
    than a hardcoded constant.

    Falls back to config.RISK_FREE_RATE if the fetch fails.
    """
    try:
        start = date.today() - timedelta(days=trailing_days + 30)
        irx = get_macro("IRX", start=start)
        if irx.empty:
            raise ValueError("empty IRX series")
        rfr = float(irx.iloc[-trailing_days:].mean())
        logger.debug("Dynamic RFR from ^IRX (%dd avg): %.4f", trailing_days, rfr)
        return rfr
    except Exception as exc:
        logger.warning("RFR fetch failed (%s); using config fallback %.4f", exc, RISK_FREE_RATE)
        return RISK_FREE_RATE


# ── FRED fetcher ───────────────────────────────────────────────────────────────

def fetch_fred(
    series_id: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> pd.Series:
    """
    Fetch a FRED series as a daily pd.Series (values in native units).

    Requires FRED_API_KEY in .env. Returns empty Series if key is absent or
    the request fails — callers should handle gracefully.

    Common series IDs
    -----------------
    DGS2          : 2Y Treasury yield (%)
    T10Y2Y        : 10Y–2Y spread (%)
    BAMLH0A0HYM2  : HY credit spread (%)
    DCOILWTICO    : WTI crude spot ($/bbl)
    """
    if not FRED_API_KEY:
        logger.debug("FRED_API_KEY not set; skipping fetch for %s", series_id)
        return pd.Series(dtype=float, name=series_id)

    if start is None:
        start = LOOKBACK_5Y

    params = {
        "series_id":   series_id,
        "api_key":     FRED_API_KEY,
        "file_type":   "json",
        "observation_start": start.isoformat(),
        "sort_order":  "asc",
    }
    if end:
        params["observation_end"] = end.isoformat()

    try:
        import requests as _req
        resp = _req.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if "observations" not in data:
            logger.warning("FRED response missing observations for %s: %s", series_id, data.get("error_message"))
            return pd.Series(dtype=float, name=series_id)

        records = [
            (obs["date"], float(obs["value"]))
            for obs in data["observations"]
            if obs["value"] not in (".", "")
        ]
        if not records:
            return pd.Series(dtype=float, name=series_id)

        dates, values = zip(*records)
        series = pd.Series(values, index=pd.to_datetime(dates), name=series_id)
        series = series.sort_index()
        logger.info("FRED %s: %d observations (%s → %s)", series_id, len(series),
                    series.index[0].date(), series.index[-1].date())
        return series

    except Exception as exc:
        logger.warning("FRED fetch failed for %s: %s", series_id, exc)
        return pd.Series(dtype=float, name=series_id)


# ── Yield curve snapshot ───────────────────────────────────────────────────────

def get_yield_curve() -> dict:
    """
    Return a current yield-curve snapshot.

    Pulls 3M (^IRX via yfinance) and 10Y (^TNX via yfinance) from cache,
    and 2Y + 10Y–2Y spread from FRED (if key available).

    Returns
    -------
    {
        "3M":     float | None,   # annualised %, e.g. 4.32
        "2Y":     float | None,
        "10Y":    float | None,
        "30Y":    float | None,
        "spread_10y_2y": float | None,   # positive = normal curve
        "as_of":  str,
    }
    """
    result: dict = {"3M": None, "2Y": None, "10Y": None, "30Y": None,
                    "spread_10y_2y": None, "as_of": date.today().isoformat()}

    # yfinance yields (in % from ^IRX / ^TNX / ^TYX — already converted to decimal by get_macro)
    for key, out_key in [("IRX", "3M"), ("TNX", "10Y"), ("TYX", "30Y")]:
        try:
            s = get_macro(key)
            if not s.empty:
                result[out_key] = round(float(s.iloc[-1]) * 100, 3)   # back to %
                result["as_of"] = s.index[-1].strftime("%Y-%m-%d")
        except Exception as exc:
            logger.warning("yield_curve: failed to fetch %s: %s", key, exc)

    # FRED for 2Y (not available cleanly on yfinance)
    try:
        dgs2 = fetch_fred("DGS2", start=date.today() - timedelta(days=10))
        if not dgs2.empty:
            result["2Y"] = round(float(dgs2.iloc[-1]), 3)
            spread = fetch_fred("T10Y2Y", start=date.today() - timedelta(days=10))
            if not spread.empty:
                result["spread_10y_2y"] = round(float(spread.iloc[-1]), 3)
            elif result["10Y"] is not None:
                result["spread_10y_2y"] = round(result["10Y"] - result["2Y"], 3)
    except Exception as exc:
        logger.warning("yield_curve: FRED fetch failed: %s", exc)
        if result["10Y"] is not None and result["3M"] is not None:
            result["spread_10y_2y"] = round(result["10Y"] - result["3M"], 3)

    return result


# ── Commodity context for compare() ───────────────────────────────────────────

def commodity_context(
    ticker: str,
    etf_series: pd.Series,
) -> Optional[dict]:
    """
    Return commodity context for an ETF if applicable.

    For energy ETFs: WTI crude beta and 1Y rolling correlation.
    For gold/miner ETFs: Gold futures beta and 1Y rolling correlation.

    Returns None if the ticker is not commodity-linked or data is unavailable.
    """
    from core.analytics import beta, correlation, _round

    ticker = ticker.upper()

    if ticker in ENERGY_TICKERS:
        commodity_key, label = "WTI", "WTI Crude (CL=F)"
    elif ticker in GOLD_TICKERS:
        commodity_key, label = "GOLD", "Gold Futures (GC=F)"
    else:
        return None

    try:
        commodity = get_macro(commodity_key, start=etf_series.index[0].date())
        if commodity.empty or len(commodity) < 60:
            return None

        # Align on common dates — use explicit keys to avoid fragile positional access
        aligned = pd.concat(
            [etf_series.rename("etf"), commodity.rename("com")], axis=1
        ).dropna()
        if len(aligned) < 60:
            return None

        etf_aligned = aligned["etf"]
        com_aligned = aligned["com"]

        # 1Y window (last 252 trading days)
        window = min(252, len(aligned))
        etf_1y = etf_aligned.iloc[-window:]
        com_1y = com_aligned.iloc[-window:]

        return {
            "commodity": label,
            "beta_to_commodity":   _round(beta(etf_aligned, com_aligned)),
            "correlation_1y":      _round(correlation(etf_1y, com_1y)),
            "correlation_full":    _round(correlation(etf_aligned, com_aligned)),
        }
    except Exception as exc:
        logger.warning("commodity_context failed for %s: %s", ticker, exc)
        return None
