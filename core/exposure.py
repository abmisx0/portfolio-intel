"""
Delta-adjusted portfolio exposure.

Equity-only views (build_holdings) miss option positions entirely. A short put
is long-equivalent exposure; a short (covered) call cancels equity upside. This
module computes Black-Scholes delta for each open option and folds it into a
single per-ticker delta-adjusted weight, so the reported book reflects economic
exposure rather than just shares held.

Implied vol is approximated by trailing-1Y realized vol of the underlying — good
enough for direction and magnitude. Deep-ITM/-OTM deltas are insensitive to the
vol assumption; near-the-money deltas carry the most estimation error.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

from scipy.stats import norm

from config import LOOKBACK_5Y, TICKER_THEMES
from core.analytics import _get_rfr, ann_vol_from_returns
from core.broker import login, get_account_data, get_option_positions
from core.data_fetcher import get_close_series, prefetch_prices

TRADING_DAYS = 252
_VOL_FALLBACK = 0.35
CAP_RATIO = 0.5  # delta_value below this fraction of equity_value ⇒ "capped" by short call

# Plausibility ceilings on a broker's per-share Greeks. Real equity-option
# Greeks never approach these; values beyond them are bad RH data (illiquid
# deep-ITM/near-expiry quotes), so the Black-Scholes value is used instead.
_GREEK_SANITY = {"delta": 1.01, "gamma": 5.0, "vega": 5.0, "theta": 50.0, "rho": 50.0}


def _underlying(ticker: str) -> tuple[Optional[float], float]:
    """Return (latest_price, annualized_realized_vol). Price is None if unavailable."""
    s = get_close_series(ticker.upper(), start=LOOKBACK_5Y)
    if s.empty:
        return None, _VOL_FALLBACK
    price = float(s.iloc[-1])
    rets = s.pct_change().dropna().iloc[-TRADING_DAYS:]
    vol = ann_vol_from_returns(rets) if len(rets) > 5 else _VOL_FALLBACK
    return price, vol


def _bs_delta(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """Black-Scholes per-share delta. Falls back to intrinsic delta when degenerate."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        if is_call:
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return norm.cdf(d1) if is_call else norm.cdf(d1) - 1.0


def _bs_greeks(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> dict:
    """
    Full Black-Scholes per-share Greeks for a LONG one-share option.

    Conventions (the standard quoting units):
      delta : per $1 move in the underlying
      gamma : change in delta per $1 move
      vega  : per +1 percentage-point of IV  (raw vega / 100)
      theta : per calendar day               (raw annual theta / 365)
      rho   : per +1 percentage-point of rate (raw rho / 100)

    Degenerate inputs (expired / zero-vol) return delta only; the other Greeks
    are 0 because there is no optionality left to measure.
    """
    delta = _bs_delta(S, K, T, r, sigma, is_call)
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {"delta": delta, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}

    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    pdf_d1 = norm.pdf(d1)
    disc = math.exp(-r * T)

    gamma = pdf_d1 / (S * sigma * sqrtT)
    vega = S * pdf_d1 * sqrtT / 100.0
    common_theta = -(S * pdf_d1 * sigma) / (2 * sqrtT)
    if is_call:
        theta = (common_theta - r * K * disc * norm.cdf(d2)) / 365.0
        rho = K * T * disc * norm.cdf(d2) / 100.0
    else:
        theta = (common_theta + r * K * disc * norm.cdf(-d2)) / 365.0
        rho = -K * T * disc * norm.cdf(-d2) / 100.0

    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho}


def compute_exposure() -> dict:
    """
    Combine live equity holdings and option positions into delta-adjusted
    per-ticker exposure.

    Returns a structured dict:
        {
          "total_value": float,
          "positions": [
            {ticker, equity_value, equity_weight,
             option_delta_dollars, delta_value, delta_weight,
             has_options}
          ],                                   # sorted by |delta_value| desc
          "options": [                         # per-contract detail
            {ticker, option_type, position_type, strike, expiration,
             underlying, iv_proxy, delta_per_share, delta_shares,
             delta_dollars, itm}
          ],
          "summary": {equity_total, option_delta_total, delta_adjusted_total},
        }
    """
    login()  # idempotent; ensures broker session whether or not caller logged in
    holdings, total_value = get_account_data()
    opts = get_option_positions()

    today = date.today()
    r = _get_rfr()  # dynamic ^IRX rate, consistent with the rest of the analytics stack
    price_cache: dict[str, tuple[Optional[float], float]] = {}

    # Batch-prefetch every distinct underlying in one yfinance call before the loop.
    underlyings = sorted({o["ticker"] for o in opts})
    if underlyings:
        prefetch_prices(underlyings, LOOKBACK_5Y, today)

    def und(tk: str):
        if tk not in price_cache:
            price_cache[tk] = _underlying(tk)
        return price_cache[tk]

    option_rows = []
    option_delta_by_ticker: dict[str, float] = {}
    for o in opts:
        tk = o["ticker"]
        S, vol = und(tk)
        K = o["strike"]
        try:
            exp = date.fromisoformat(o["expiration"])
            T = max((exp - today).days, 0) / 365.0
        except (TypeError, ValueError):
            T = 0.0
        is_call = o["option_type"] == "call"
        mult = o.get("trade_value_multiplier", 100) or 100
        sign = -1.0 if o["position_type"] == "short" else 1.0
        contracts = o["quantity"]
        # Prefer real implied vol from the chain; fall back to realized vol.
        iv = o.get("implied_volatility")
        has_real_iv = iv is not None and iv > 0
        sigma = iv if has_real_iv else vol
        iv_source = "chain" if has_real_iv else "realized"
        # Premium collected: robin_stocks avg_price is per-contract (per-share × 100).
        avg = abs(o.get("avg_price") or 0.0)
        prem_per_share = avg / 100.0 if avg > mult else avg
        premium = prem_per_share * contracts * mult * (1.0 if o["position_type"] == "short" else -1.0)

        if S is None:
            option_rows.append({
                "ticker": tk, "option_type": o["option_type"],
                "position_type": o["position_type"], "strike": K,
                "expiration": o["expiration"], "underlying": None,
                "iv": None, "iv_source": None, "delta_per_share": None,
                "delta_shares": None, "delta_dollars": None, "itm": None,
                "premium": premium, "contracts": contracts,
                "gamma_per_share": None, "vega_per_share": None,
                "theta_per_share": None, "rho_per_share": None,
                "pos_delta_shares": None, "pos_gamma": None,
                "pos_vega_dollars": None, "pos_theta_dollars": None,
                "pos_rho_dollars": None,
            })
            continue

        # Greeks: prefer Robinhood's production model (accounts for American
        # exercise + dividends, matches the app); fall back to our Black-Scholes
        # per field. Robinhood quotes per-share Greeks in the same retail
        # convention as _bs_greeks (theta/day, vega per IV-point, rho per
        # rate-point), so they merge field-by-field — BUT RH returns corrupt
        # Greeks for illiquid deep-ITM/near-expiry contracts (e.g. a per-share
        # vega of 800), so each broker field must pass a plausibility ceiling
        # before it's trusted; otherwise Black-Scholes fills it.
        bs = _bs_greeks(S, K, T, r, sigma, is_call)
        bk = o.get("broker_greeks") or {}
        g = {}
        broker_used = False
        for name in ("delta", "gamma", "theta", "vega", "rho"):
            rh_val = bk.get(name)
            if rh_val is not None and abs(rh_val) <= _GREEK_SANITY[name]:
                g[name] = rh_val
                broker_used = True
            else:
                g[name] = bs[name]
        greek_source = "broker" if broker_used else "blackscholes"
        # Sanity cross-check: flag when the two models disagree materially on
        # delta (the early-exercise-sensitive Greek). Large gaps = deep-ITM near
        # expiry, where our European assumption is weakest.
        delta_divergence = (abs(bk["delta"] - bs["delta"])
                            if bk.get("delta") is not None else None)

        d = g["delta"]
        delta_shares = d * contracts * mult * sign
        delta_dollars = delta_shares * S
        itm = (S > K) if is_call else (S < K)
        option_delta_by_ticker[tk] = option_delta_by_ticker.get(tk, 0.0) + delta_dollars

        # Position-level Greeks: per-share Greek × contracts × 100 × short/long
        # sign, in the units a holder reads off a brokerage screen.
        #   pos_delta_shares : share-equivalents of underlying exposure
        #   pos_gamma        : change in pos_delta_shares per $1 underlying move
        #   pos_theta_dollars: P&L per calendar day (positive = time decay earns you)
        #   pos_vega_dollars : P&L per +1 IV point
        #   pos_rho_dollars  : P&L per +1 rate point
        scale = contracts * mult * sign
        option_rows.append({
            "ticker": tk, "option_type": o["option_type"],
            "position_type": o["position_type"], "strike": K,
            "expiration": o["expiration"], "underlying": S,
            "iv": sigma, "iv_source": iv_source, "delta_per_share": d,
            "delta_shares": delta_shares, "delta_dollars": delta_dollars, "itm": itm,
            "premium": premium, "contracts": contracts,
            "gamma_per_share": g["gamma"],
            "vega_per_share": g["vega"],
            "theta_per_share": g["theta"],
            "rho_per_share": g["rho"],
            "pos_delta_shares": delta_shares,
            "pos_gamma": g["gamma"] * scale,
            "pos_vega_dollars": g["vega"] * scale,
            "pos_theta_dollars": g["theta"] * scale,
            "pos_rho_dollars": g["rho"] * scale,
            "greek_source": greek_source,          # "broker" (RH) or "blackscholes" (our fallback)
            "delta_divergence": delta_divergence,  # |RH delta − BS delta| per share, None if no RH
        })

    all_tickers = set(holdings) | set(option_delta_by_ticker)
    positions = []
    equity_total = 0.0
    option_delta_total = 0.0
    for tk in all_tickers:
        eq = holdings.get(tk, {}).get("market_value", 0.0)
        od = option_delta_by_ticker.get(tk, 0.0)
        delta_value = eq + od
        equity_total += eq
        option_delta_total += od
        positions.append({
            "ticker": tk,
            "equity_value": eq,
            "equity_weight": eq / total_value if total_value else 0.0,
            "option_delta_dollars": od,
            "delta_value": delta_value,
            "delta_weight": delta_value / total_value if total_value else 0.0,
            "has_options": tk in option_delta_by_ticker,
        })

    positions.sort(key=lambda p: abs(p["delta_value"]), reverse=True)
    option_rows.sort(key=lambda o: abs(o["delta_dollars"] or 0), reverse=True)

    return {
        "total_value": total_value,
        "positions": positions,
        "options": option_rows,
        "summary": {
            "equity_total": equity_total,
            "option_delta_total": option_delta_total,
            "delta_adjusted_total": equity_total + option_delta_total,
            "premium_total": sum(o.get("premium") or 0 for o in option_rows),
            # Book-level Greek totals across all option contracts: net daily
            # theta P&L, net vega P&L per IV point, net rho P&L per rate point.
            "theta_dollars_total": sum(o.get("pos_theta_dollars") or 0 for o in option_rows),
            "vega_dollars_total": sum(o.get("pos_vega_dollars") or 0 for o in option_rows),
            "rho_dollars_total": sum(o.get("pos_rho_dollars") or 0 for o in option_rows),
        },
    }


def delta_adjusted_positions(exposure: Optional[dict] = None) -> list[dict]:
    """
    Live book expressed as standard portfolio positions ({ticker, weight, theme,
    role}) using delta-adjusted economic value instead of share value.

    Net-long economic exposure only (positions with delta_value <= 0 — e.g. a
    fully covered/capped name or a net-short call — are dropped, since they carry
    no long exposure to weight). Weights normalized to sum to 1.0. `role` records
    whether exposure is equity, option-only (synthetic), or option-capped.

    Pass a precomputed `exposure` dict (from compute_exposure) to avoid a second
    Robinhood round-trip; otherwise it fetches one.
    """
    exp = exposure or compute_exposure()
    rows = [p for p in exp["positions"] if p["delta_value"] > 0]
    gross = sum(p["delta_value"] for p in rows) or 1.0

    out = []
    for p in rows:
        if not p["has_options"]:
            role = "Live equity position"
        elif p["equity_value"] == 0:
            role = "Synthetic (option delta only)"
        elif p["delta_value"] < p["equity_value"] * CAP_RATIO:
            role = "Equity capped by short call"
        else:
            role = "Equity + option delta"
        out.append({
            "ticker": p["ticker"],
            "weight": p["delta_value"] / gross,
            "theme": TICKER_THEMES.get(p["ticker"].upper(), "Other"),
            "role": role,
        })
    return out


def exposure_as_holdings(exposure: dict, equity_holdings: dict) -> dict[str, dict]:
    """
    Convert compute_exposure() output into a holdings-style dict (the shape
    get_account_data returns), using delta-adjusted economic value as
    market_value / portfolio_pct. Equity metadata (shares, cost, gain) is carried
    over from equity_holdings where present; net-short (delta_value <= 0) names
    are dropped. Lets advise reason on economic exposure without re-implementing
    the holdings schema in the command layer.
    """
    total = exposure["total_value"]
    out: dict[str, dict] = {}
    for p in exposure["positions"]:
        dv = p["delta_value"]
        if dv <= 0:
            continue
        base = equity_holdings.get(p["ticker"], {})
        out[p["ticker"]] = {
            "shares": base.get("shares", 0.0),
            "current_price": base.get("current_price", 0.0),
            "market_value": dv,
            "portfolio_pct": dv / total if total else 0.0,
            "avg_cost": base.get("avg_cost", 0.0),
            "gain_pct": base.get("gain_pct", 0.0),
        }
    return out
