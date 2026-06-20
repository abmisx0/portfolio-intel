"""
Central configuration: portfolio definitions, API settings, and constants.
"""
from __future__ import annotations

from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).parent
DATA_DIR = ROOT_DIR / "data"
CACHE_DB_PATH = DATA_DIR / "cache.db"
PORTFOLIOS_JSON_PATH = DATA_DIR / "portfolios.json"

DATA_DIR.mkdir(exist_ok=True)

# ── Portfolio Definitions ─────────────────────────────────────────────────────

# Each entry: {"ticker": str, "weight": float (0–1), "theme": str, "role": str}

import json as _json

PORTFOLIOS: dict[str, list[dict]] = {
    # ── Getting Started ────────────────────────────────────────────────────────────
    # Define your portfolios here. Each entry:
    #   ticker  : ETF or stock symbol (string, uppercase)
    #   weight  : target allocation as a decimal (0.0–1.0, must sum to 1.0)
    #   theme   : sector / macro theme label (used for attribution reporting)
    #   role    : one-line thesis note — why this position exists
    #
    # Run any CLI command with --portfolio <name>:
    #   python3 -m cli analytics --portfolio core_satellite
    #   python3 -m cli optimize --portfolio thematic --objective sharpe
    #   python3 -m cli backtest --a core_satellite --b thematic --start 2020-01-01

    # ── Example: Core-Satellite ────────────────────────────────────────────────────
    # A simple starting point: broad market anchor + a few thematic satellites.
    "core_satellite": [
        {"ticker": "VOO",  "weight": 0.50, "theme": "Broad Market",  "role": "S&P 500 core — low cost passive anchor"},
        {"ticker": "QQQ",  "weight": 0.20, "theme": "Technology",    "role": "Nasdaq 100 growth tilt"},
        {"ticker": "GLD",  "weight": 0.10, "theme": "Commodities",   "role": "Physical gold — inflation hedge and diversifier"},
        {"ticker": "BND",  "weight": 0.10, "theme": "Fixed Income",  "role": "Broad bond market — volatility buffer"},
        {"ticker": "VNQ",  "weight": 0.10, "theme": "Real Estate",   "role": "REIT index — real asset income exposure"},
    ],

    # ── Example: Thematic / Sector ────────────────────────────────────────────────
    # A portfolio built around macro theses rather than market-cap weighting.
    # Use `optimize` to discover or validate weights for your own theses.
    "thematic": [
        {"ticker": "SMH",  "weight": 0.25, "theme": "Technology",    "role": "Semiconductor ETF — AI compute backbone"},
        {"ticker": "XLE",  "weight": 0.20, "theme": "Energy",        "role": "Energy sector — commodity / geopolitical hedge"},
        {"ticker": "XLF",  "weight": 0.20, "theme": "Financials",    "role": "Financials — rate cycle and deregulation exposure"},
        {"ticker": "GLD",  "weight": 0.15, "theme": "Commodities",   "role": "Physical gold — store of value, DXY hedge"},
        {"ticker": "VHT",  "weight": 0.10, "theme": "Healthcare",    "role": "Healthcare — defensive growth, aging demographics"},
        {"ticker": "BND",  "weight": 0.10, "theme": "Fixed Income",  "role": "Bonds — tail-risk buffer"},
    ],
}

# ── Personal portfolios (gitignored, never committed) ─────────────────────────
# Add your own portfolios to data/portfolios.json — same schema as above.
# They merge into PORTFOLIOS at startup and are available to all CLI commands.
if PORTFOLIOS_JSON_PATH.exists():
    _personal = _json.loads(PORTFOLIOS_JSON_PATH.read_text())
    PORTFOLIOS.update(_personal)

# ── Live portfolio resolution ─────────────────────────────────────────────────
# The reserved name "live" is not a static config entry — it is fetched from
# Robinhood at call time so every command reflects your real book, never a
# stale snapshot. Fetched once per process and reused across all call sites.
LIVE_PORTFOLIO = "live"
_LIVE_CACHE: "list[dict] | None" = None
_LIVE_CACHE_TS: float = 0.0
_LIVE_CACHE_TTL: float = 300.0  # seconds; bounds staleness in long-lived processes (web dashboard)

# Thesis labels applied to live holdings (Robinhood has no theme metadata).
# Add tickers here as your book evolves; unmapped tickers fall back to "Other".
TICKER_THEMES: dict[str, str] = {
    "SMH": "Technology",       "AAPL": "Technology",
    "PPA": "Defense",          "XAR": "Defense",        "ITA": "Defense",      "SHLD": "Defense",
    "VDE": "Energy",
    "SLV": "Commodities",      "IAU": "Commodities",    "GLD": "Commodities",
    "NLR": "Nuclear Energy",
    "QTUM": "Quantum Computing",
    "COIN": "Crypto/Fintech",  "CRCL": "Crypto/Fintech", "HOOD": "Crypto/Fintech",
    "NFLX": "Streaming",
    "PSUS": "Alternative",     "PS": "Alternative",
    "XLV": "Healthcare",
}


def resolve_portfolio(name: str) -> "list[dict]":
    """Resolve a --portfolio name to a list of position dicts.

    name == "live": fetch current Robinhood holdings and return them in the
    standard schema ({ticker, weight, theme, role}) with weights normalized to
    sum to 1.0 across held positions. The live snapshot is fetched once per
    process. Any other name is looked up in PORTFOLIOS.

    Raises ValueError if the name is unknown or no live positions are found.
    """
    if name == LIVE_PORTFOLIO:
        global _LIVE_CACHE, _LIVE_CACHE_TS
        import time
        now = time.time()
        if _LIVE_CACHE is None or (now - _LIVE_CACHE_TS) > _LIVE_CACHE_TTL:
            from core.broker import login, get_account_data  # lazy: avoids import cycle
            login()
            holdings, _total = get_account_data()
            _LIVE_CACHE_TS = now
            gross = sum(h["market_value"] for h in holdings.values()) or 1.0
            _LIVE_CACHE = [
                {
                    "ticker": ticker,
                    "weight": h["market_value"] / gross,
                    "theme": TICKER_THEMES.get(ticker.upper(), "Other"),
                    "role": "Live Robinhood position",
                }
                for ticker, h in holdings.items()
                if h["market_value"] > 0
            ]
        if not _LIVE_CACHE:
            raise ValueError("No live Robinhood positions found")
        return [dict(p) for p in _LIVE_CACHE]

    positions = PORTFOLIOS.get(name)
    if positions is None:
        raise ValueError(f"Portfolio '{name}' not found in config")
    return positions

# ── App Defaults ──────────────────────────────────────────────────────────────

# "live" = current Robinhood holdings (resolved at request time); the named
# alternatives are advisory targets maintained in data/portfolios.json.
DEFAULT_PORTFOLIO: str = "live"
PORTFOLIO_DISPLAY_ORDER: list[str] = ["live", "checkup_target", "thesis_core"]

# Thesis anchors — positions held on conviction, never flagged EXIT by the trim
# engine regardless of trailing Sharpe (a diversifier/hedge naturally drags Sharpe
# in an equity bull market; the ΔSharpe signal must not recommend exiting it).
# Edit this set to match your own non-negotiable positions.
THESIS_ANCHORS: set[str] = {"SMH", "PPA", "NLR"}

# ── Data Fetcher Settings ─────────────────────────────────────────────────────

# Benchmark ticker used for beta/correlation across all analytics
BENCHMARK_TICKER: str = "VOO"

# Default lookback for price history (5 years + buffer for rolling windows)
from datetime import date as _date, timedelta as _td
LOOKBACK_5Y: _date = _date.today() - _td(days=365 * 5 + 30)
# Full history lookback — rolling 10.5Y ensures the 10Y perf button always has data
LOOKBACK_ALL: _date = _date.today() - _td(days=365 * 10 + 200)

# Risk-free rate for Sharpe/Sortino calculations (annualised, approximate T-bill yield)
RISK_FREE_RATE: float = 0.045

# ETF holdings cache TTL in days
HOLDINGS_CACHE_TTL_DAYS: int = 7

# yfinance retry settings
YFINANCE_MAX_RETRIES: int = 3
YFINANCE_BACKOFF_BASE: float = 2.0  # seconds; doubles each retry

# ── Optional API Keys (loaded from .env if present) ───────────────────────────
import os
from dotenv import load_dotenv

load_dotenv(ROOT_DIR / ".env")

FRED_API_KEY: str | None = os.getenv("FRED_API_KEY")
DISCORD_WEBHOOK_URL: str | None = os.getenv("DISCORD_WEBHOOK_URL")
FINNHUB_API_KEY: str | None = os.getenv("FINNHUB_API_KEY")

# ── Benchmark tickers ─────────────────────────────────────────────────────────
# BENCHMARK_TICKER : ETF benchmark (includes fees/tracking error) — default for most views
# BENCHMARK_SPX    : Pure S&P 500 index — used when --benchmark spx is passed
BENCHMARK_SPX: str = "^SPX"

# Selectable benchmarks for `backtest`/`performance` (--benchmark flag).
# voo/spx track the S&P 500 (ETF vs pure index); nasdaq/russell use the
# investable ETF proxies (QQQ/IWM) since ^NDX/^RUT history is not freely
# cached. The label (key) is what the user passes; the value is the ticker.
BENCHMARKS: dict[str, str] = {
    "voo": BENCHMARK_TICKER,
    "spx": BENCHMARK_SPX,
    "nasdaq": "QQQ",
    "russell": "IWM",
}
