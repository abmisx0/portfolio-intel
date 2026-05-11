"""
Robinhood account data via robin_stocks.

READ-ONLY. The only robin_stocks calls permitted here are:
    rh.login()                              — authentication
    rh.account.build_holdings()             — current equity positions
    rh.profiles.load_portfolio_profile()    — portfolio equity
    rh.options.get_open_option_positions()  — open options positions
    rh.orders.get_all_open_option_orders()  — pending options orders
    rh.get_watchlist_by_name()             — watchlist tickers

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


def get_option_positions() -> list[dict]:
    """Return open options positions as a list of normalized dicts.

    Strike and option_type are not on the position record — they require a
    secondary fetch of the option instrument by ID.
    """
    rh = _require_login()
    raw: list = rh.options.get_open_option_positions() or []
    results = []
    for pos in raw:
        try:
            option_id = pos.get("option_id") or ""
            strike = 0.0
            option_type = ""
            if option_id:
                instrument = rh.options.get_option_instrument_data_by_id(option_id) or {}
                strike = float(instrument.get("strike_price") or 0)
                option_type = (instrument.get("type") or "").lower()
            results.append({
                "ticker": (pos.get("chain_symbol") or "").upper(),
                "expiration": pos.get("expiration_date", ""),
                "strike": strike,
                "option_type": option_type,
                "position_type": (pos.get("type") or "").lower(),  # "short" or "long"
                "quantity": float(pos.get("quantity") or 0),
                "avg_price": float(pos.get("average_price") or 0),
                "trade_value_multiplier": float(pos.get("trade_value_multiplier") or 100),
            })
        except (TypeError, ValueError):
            logger.warning("Skipping malformed option position: %s", pos.get("id"))
    return results


def get_pending_option_orders() -> list[dict]:
    """Return open (pending/queued) options orders — these are your live asks."""
    rh = _require_login()
    raw: list = rh.orders.get_all_open_option_orders() or []
    results = []
    for order in raw:
        legs = order.get("legs") or []
        for leg in legs:
            try:
                results.append({
                    "ticker": (order.get("chain_symbol") or "").upper(),
                    "expiration": leg.get("expiration_date", ""),
                    "strike": float(leg.get("strike_price") or 0),
                    "option_type": (leg.get("option_type") or "").lower(),
                    "side": (leg.get("side") or "").lower(),
                    "quantity": float(order.get("quantity") or 0),
                    "ask_price": float(order.get("price") or 0),
                    "direction": (order.get("direction") or "").lower(),
                    "status": (order.get("derived_state") or order.get("state") or "").lower(),
                })
            except (TypeError, ValueError):
                logger.warning("Skipping malformed option order: %s", order.get("id"))
    return results


def get_account_data() -> tuple[dict[str, dict], float]:
    """Return (positions, total_value) in one call, fetching both endpoints in parallel."""
    with ThreadPoolExecutor(max_workers=2) as executor:
        positions_future = executor.submit(get_positions)
        value_future = executor.submit(get_portfolio_value)
        return positions_future.result(), value_future.result()


def get_watchlist(name: str = "Watchlist") -> list[dict]:
    """Return tickers from a Robinhood watchlist as a list of normalized dicts."""
    rh = _require_login()
    raw: dict = rh.get_watchlist_by_name(name) or {}
    results = []
    for item in raw.get("results") or []:
        ticker = (item.get("symbol") or "").upper()
        if not ticker:
            continue
        try:
            results.append({
                "ticker": ticker,
                "name": item.get("name") or "",
                "price": float(item.get("price") or 0),
                "one_day_pct": float(item.get("one_day_percent_change") or 0),
                "high_52w": float(item.get("high_52_weeks") or 0),
                "low_52w": float(item.get("low_52_weeks") or 0),
                "in_portfolio": bool(item.get("holdings")),
            })
        except (TypeError, ValueError):
            logger.warning("Skipping malformed watchlist item: %s", ticker)
    return results
