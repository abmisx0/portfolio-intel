"""
Robinhood account data via robin_stocks.

READ-ONLY. The only robin_stocks calls permitted here are:
    rh.login()                                      — authentication
    rh.account.build_holdings()                     — current equity positions
    rh.profiles.load_portfolio_profile()            — portfolio equity
    rh.options.get_open_option_positions()          — open options positions
    rh.options.get_option_instrument_data_by_id()   — option strike/type metadata
    rh.options.get_option_market_data_by_id()        — option mark/implied vol/greeks
    rh.orders.get_all_open_option_orders()          — pending options orders
    rh.orders.get_all_stock_orders()                — equity order history
    rh.stocks.get_instrument_by_url()               — resolve instrument URL → ticker
    rh.get_watchlist_by_name()                      — watchlist tickers

The rh module is stored in a private module-level cache after login and
never returned or exposed to callers — all public functions return plain
Python dicts/floats so no write-capable object can leak out.

Credentials are read from .env (RH_USERNAME, RH_PASSWORD).
robin_stocks caches the OAuth token in ~/.tokens/robinhood.pickle after
the first successful login — MFA is only prompted once.
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_rh_module = None       # populated by login(); never exposed to callers
_instrument_cache: dict[str, str] = {}  # instrument URL → ticker symbol


def login() -> None:
    """Authenticate with Robinhood. Token and module cached for this process.

    If RH_MFA_SECRET (the TOTP secret from authenticator-app MFA setup) is in
    .env, a 6-digit code is generated and passed to login — this skips the
    device-approval flow entirely, which is flaky under rate limits and dies
    when the in-app prompt isn't approved within its short validity window.
    """
    global _rh_module
    username = os.getenv("RH_USERNAME")
    password = os.getenv("RH_PASSWORD")
    if not username or not password:
        raise ValueError("RH_USERNAME and RH_PASSWORD must be set in .env")
    try:
        import robin_stocks.robinhood as rh
    except ImportError:
        raise ImportError("robin-stocks is not installed. Run: pip install robin-stocks")

    mfa_code = None
    mfa_secret = os.getenv("RH_MFA_SECRET")
    if mfa_secret:
        try:
            import pyotp
            mfa_code = pyotp.TOTP(mfa_secret).now()
        except ImportError:
            logger.warning("RH_MFA_SECRET set but pyotp not installed (pip install pyotp) "
                           "— falling back to device-approval login")

    # robin_stocks prints progress to stdout; redirect to stderr so it never
    # pollutes JSON output (stdout is reserved for the --format json envelope).
    with contextlib.redirect_stdout(sys.stderr):
        if mfa_code:
            rh.login(username, password, mfa_code=mfa_code, store_session=True)
        else:
            rh.login(username, password, store_session=True)
    _rh_module = rh


def _require_login():
    if _rh_module is None:
        raise RuntimeError("Call broker.login() before fetching Robinhood data")
    return _rh_module


def _resolve_instrument_url(rh, url: str) -> str:
    """Resolve a Robinhood instrument URL to an uppercase ticker symbol."""
    if url in _instrument_cache:
        return _instrument_cache[url]
    try:
        data = rh.stocks.get_instrument_by_url(url) or {}
        ticker = (data.get("symbol") or "").upper()
    except Exception:
        ticker = ""
    _instrument_cache[url] = ticker
    return ticker


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

    def _normalize(pos: dict) -> dict | None:
        try:
            option_id = pos.get("option_id") or ""
            strike = 0.0
            option_type = ""
            implied_vol = None
            mark_price = None
            broker_greeks: dict = {}
            if option_id:
                instrument = rh.options.get_option_instrument_data_by_id(option_id) or {}
                strike = float(instrument.get("strike_price") or 0)
                option_type = (instrument.get("type") or "").lower()
                md = rh.options.get_option_market_data_by_id(option_id) or {}
                if isinstance(md, list):
                    md = md[0] if md else {}
                iv = md.get("implied_volatility")
                implied_vol = float(iv) if iv not in (None, "") else None
                mk = md.get("mark_price") or md.get("adjusted_mark_price")
                mark_price = float(mk) if mk not in (None, "") else None
                # Robinhood returns per-share Greeks for the LONG side on the same
                # market-data record — surface them so callers can prefer the
                # broker's production model over our Black-Scholes approximation.
                for g in ("delta", "gamma", "theta", "vega", "rho"):
                    v = md.get(g)
                    if v not in (None, ""):
                        try:
                            broker_greeks[g] = float(v)
                        except (TypeError, ValueError):
                            pass
            return {
                "ticker": (pos.get("chain_symbol") or "").upper(),
                "expiration": pos.get("expiration_date", ""),
                "strike": strike,
                "option_type": option_type,
                "position_type": (pos.get("type") or "").lower(),
                "quantity": float(pos.get("quantity") or 0),
                "avg_price": float(pos.get("average_price") or 0),
                "trade_value_multiplier": float(pos.get("trade_value_multiplier") or 100),
                "implied_volatility": implied_vol,  # per-contract IV from chain, None if unavailable
                "mark_price": mark_price,           # current per-share option mark
                "broker_greeks": broker_greeks,     # per-share LONG-side Greeks from RH, {} if absent
            }
        except (TypeError, ValueError):
            logger.warning("Skipping malformed option position: %s", pos.get("id"))
            return None

    # Each position needs two per-id REST calls; fan them out instead of serializing.
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [r for r in pool.map(_normalize, raw) if r is not None]
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


def get_purchase_dates() -> dict[str, dict]:
    """
    Return per-ticker purchase lot summary derived from filled equity order history.

    Resolves each order's instrument URL to a ticker symbol (cached per process).
    Only filled buy orders are counted; sells are ignored so cancelled/partial
    orders don't corrupt the date range.

    Returns:
        {
          TICKER: {
            "first_purchase":      "YYYY-MM-DD",  # oldest filled buy lot
            "last_purchase":       "YYYY-MM-DD",  # most recent filled buy lot
            "has_short_term_lots": bool,           # any lot < 1 year old today
            "ltcg_all_lots_date":  "YYYY-MM-DD",  # date all lots become LTCG
          }
        }
    """
    rh = _require_login()
    today = date.today()
    one_year_ago = today.replace(year=today.year - 1)

    orders = rh.orders.get_all_stock_orders() or []

    unique_urls = {
        order["instrument"]
        for order in orders
        if order.get("side") == "buy"
        and order.get("state") == "filled"
        and order.get("instrument")
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        pool.map(lambda u: _resolve_instrument_url(rh, u), unique_urls)

    lots: dict[str, list[date]] = {}
    for order in orders:
        if order.get("side") != "buy" or order.get("state") != "filled":
            continue
        url = order.get("instrument") or ""
        tx_str = order.get("last_transaction_at") or ""
        if not url or not tx_str:
            continue
        try:
            tx_date = date.fromisoformat(tx_str[:10])
        except ValueError:
            continue
        ticker = _resolve_instrument_url(rh, url)
        if ticker:
            lots.setdefault(ticker, []).append(tx_date)

    result: dict[str, dict] = {}
    for ticker, dates in lots.items():
        dates.sort()
        last = dates[-1]
        try:
            anniversary = last.replace(year=last.year + 1)
        except ValueError:  # Feb 29 → Feb 28 in a non-leap year
            anniversary = last.replace(year=last.year + 1, day=28)
        # LTCG requires holding MORE than one year — the first long-term day is the
        # day after the one-year anniversary, not the anniversary itself.
        ltcg_date = anniversary + timedelta(days=1)
        result[ticker] = {
            "first_purchase": str(dates[0]),
            "last_purchase": str(last),
            "has_short_term_lots": any(d > one_year_ago for d in dates),
            "ltcg_all_lots_date": str(ltcg_date),
        }
    return result


def get_cash_flows() -> list[dict]:
    """
    Return dated equity cash flows derived from filled stock order history.

    Each flow is a single filled buy or sell, with a signed dollar amount from
    the investor's perspective:
        buy  → negative (cash leaves your pocket into the position)
        sell → positive (cash returns to your pocket)

    This is the raw material for a money-weighted (IRR) return: the actual
    timing and size of every contribution/withdrawal. Dividends, DRIP, and
    ACATS transfers-in are NOT in stock order history, so a book funded partly
    by those will show an understated cost basis here — callers should surface
    that caveat.

    Returns (sorted oldest-first):
        [{"date": "YYYY-MM-DD", "ticker": str, "side": "buy"|"sell",
          "amount": float,     # signed dollars: buy < 0, sell > 0
          "shares": float}]    # always positive share count for the fill
    """
    rh = _require_login()
    orders = rh.orders.get_all_stock_orders() or []

    unique_urls = {
        order["instrument"]
        for order in orders
        if order.get("state") == "filled" and order.get("instrument")
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        pool.map(lambda u: _resolve_instrument_url(rh, u), unique_urls)

    flows: list[dict] = []
    for order in orders:
        if order.get("state") != "filled":
            continue
        side = (order.get("side") or "").lower()
        if side not in ("buy", "sell"):
            continue
        url = order.get("instrument") or ""
        tx_str = order.get("last_transaction_at") or ""
        if not url or not tx_str:
            continue
        try:
            tx_date = date.fromisoformat(tx_str[:10])
        except ValueError:
            continue

        # Prefer the broker's executed notional (covers fractional fills and
        # price improvement exactly); fall back to price × quantity.
        notional = None
        en = order.get("executed_notional")
        if isinstance(en, dict) and en.get("amount") not in (None, ""):
            try:
                notional = float(en["amount"])
            except (TypeError, ValueError):
                notional = None
        if notional is None:
            try:
                notional = float(order.get("average_price") or 0) * float(
                    order.get("cumulative_quantity") or 0
                )
            except (TypeError, ValueError):
                continue
        if notional <= 0:
            continue

        ticker = _resolve_instrument_url(rh, url)
        if not ticker:
            continue
        try:
            shares = float(order.get("cumulative_quantity") or 0)
        except (TypeError, ValueError):
            shares = 0.0
        flows.append({
            "date": str(tx_date),
            "ticker": ticker,
            "side": side,
            "amount": -notional if side == "buy" else notional,
            "shares": shares,
        })

    flows.sort(key=lambda f: f["date"])
    return flows


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
