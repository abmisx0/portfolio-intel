"""
Robinhood account data via robin_stocks.

READ-ONLY. The only robin_stocks calls permitted here are:
    rh.login()                           — authentication
    rh.account.build_holdings()          — current positions
    rh.profiles.load_portfolio_profile() — portfolio equity

The rh module is stored in a private module-level cache after login and
never returned or exposed to callers — all public functions return plain
Python dicts/floats so no write-capable object can leak out.

Credentials are read from .env (RH_USERNAME, RH_PASSWORD).
robin_stocks caches the OAuth token in ~/.tokens/robinhood.pickle after
the first successful login — MFA is only prompted once.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_rh_module = None  # populated by login(); never exposed to callers


def login() -> None:
    """Authenticate with Robinhood. Token and module cached for this process."""
    global _rh_module
    username = os.getenv("RH_USERNAME")
    password = os.getenv("RH_PASSWORD")
    if not username or not password:
        raise ValueError("RH_USERNAME and RH_PASSWORD must be set in .env")
    try:
        import robin_stocks.robinhood as rh
    except ImportError:
        raise ImportError("robin-stocks is not installed. Run: pip install robin-stocks")
    rh.login(username, password, store_session=True)
    _rh_module = rh


def _require_login():
    if _rh_module is None:
        raise RuntimeError("Call broker.login() before fetching Robinhood data")
    return _rh_module


def get_positions() -> dict[str, dict]:
    """
    Return current Robinhood holdings.

    Returns:
        {
          TICKER: {
            shares: float,
            current_price: float,
            market_value: float,
            portfolio_pct: float (0–1),
            avg_cost: float,
            gain_pct: float,
          }
        }
    """
    rh = _require_login()
    holdings: dict = rh.account.build_holdings()
    result: dict[str, dict] = {}
    for ticker, data in holdings.items():
        try:
            result[ticker.upper()] = {
                "shares": float(data.get("quantity") or 0),
                "current_price": float(data.get("price") or 0),
                "market_value": float(data.get("equity") or 0),
                "portfolio_pct": float(data.get("percentage") or 0) / 100,
                "avg_cost": float(data.get("average_buy_price") or 0),
                "gain_pct": float(data.get("percent_change") or 0),
            }
        except (TypeError, ValueError):
            logger.warning("Skipping malformed position data for %s", ticker)
    return result


def get_portfolio_value() -> float:
    """Return total Robinhood portfolio equity in dollars (includes cash)."""
    rh = _require_login()
    profile: dict = rh.profiles.load_portfolio_profile()
    # Robinhood returns extended_hours_equity (not equity) when the market is closed
    value = profile.get("equity") or profile.get("extended_hours_equity") or 0
    return float(value)


def get_account_data() -> tuple[dict[str, dict], float]:
    """Return (positions, total_value) in one call, fetching both endpoints in parallel."""
    with ThreadPoolExecutor(max_workers=2) as executor:
        positions_future = executor.submit(get_positions)
        value_future = executor.submit(get_portfolio_value)
        return positions_future.result(), value_future.result()
