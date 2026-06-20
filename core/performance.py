"""
Money-weighted (IRR) performance of the live book vs. index benchmarks.

The constant-weight backtester answers "how would today's weights have done
historically." This module answers a different question: "given the actual
timing and size of every dollar I contributed, what return did my real choices
earn — and what would the same contributions have earned in the index?"

Methodology:
  - Pull filled equity cash flows from Robinhood order history (buys negative,
    sells positive) via broker.get_cash_flows().
  - User terminal value = current equity market value (broker positions).
  - User return = XIRR(cash_flows + terminal_value) — the annualized money-
    weighted rate that discounts every actual cash flow to zero.
  - For each benchmark, *clone the identical external cash flows* into the index
    (buy/sell index shares with the same dollars on the same dates), then take
    XIRR of those same flows against the index-cloned terminal value. Identical
    contribution timing is held fixed, so the only difference is asset choice —
    the honest apples-to-apples comparison.

Caveats (surfaced to the caller):
  - Equity only. Options P&L and any cash sleeve are excluded.
  - Dividends / DRIP / ACATS transfers-in are not in stock order history, so a
    book funded partly by those will understate cost basis (overstate return).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import BENCHMARKS
from core import broker
from core.data_fetcher import get_close_series, prefetch_prices

logger = logging.getLogger(__name__)

_DEFAULT_BENCHMARKS = ("voo", "nasdaq", "russell")


def _xirr(amounts: list[float], dates: list[date]) -> float | None:
    """
    Internal rate of return for irregularly-spaced cash flows (Excel XIRR).

    Solves for r where sum(CF_i / (1+r)**(years_i)) == 0, with years measured
    in actual/365 from the first cash flow. Returns the annualized rate, or
    None if the flows don't bracket a root (e.g. all same sign).
    """
    if len(amounts) < 2:
        return None
    t0 = dates[0]
    years = [(d - t0).days / 365.0 for d in dates]

    def npv(rate: float) -> float:
        return sum(cf / (1.0 + rate) ** y for cf, y in zip(amounts, years))

    # Need a sign change to have a real root.
    if not (any(a > 0 for a in amounts) and any(a < 0 for a in amounts)):
        return None

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        # No bracketed root in a sane return range.
        return None
    try:
        from scipy.optimize import brentq
        return float(brentq(npv, lo, hi, maxiter=200))
    except Exception:
        return None


def _clone_into_index(flows: list[dict], prices: pd.Series) -> tuple[float, float]:
    """
    Replay external cash flows into a single index series.

    Each flow's dollars buy/sell index shares at the close on (or just before)
    the flow date. Returns (terminal_value, total_shares). A buy (amount < 0)
    adds shares; a sell (amount > 0) removes them.
    """
    prices = prices.sort_index()
    shares = 0.0
    for f in flows:
        flow_date = pd.Timestamp(f["date"])
        # asof = last available close at/before the flow date (handles weekends/holidays)
        px = prices.asof(flow_date)
        if px is None or pd.isna(px) or px <= 0:
            continue
        # contribution into the index = cash that left your pocket = -amount
        shares += (-f["amount"]) / float(px)
    final_px = float(prices.iloc[-1])
    return shares * final_px, shares


def performance(
    benchmarks: tuple[str, ...] = _DEFAULT_BENCHMARKS,
    end: date | None = None,
) -> dict:
    """
    Compute money-weighted return of the live equity book vs index benchmarks.

    Requires broker.login() to have been called. Returns a structured dict for
    the formatter: cash-flow summary, coverage reconciliation, user XIRR over
    the covered sleeve, and per-benchmark cloned XIRR + terminal value.

    Coverage reconciliation is essential: Robinhood's stock-order endpoint only
    returns recent orders, and positions transferred in (ACATS) have no order
    record at all. We therefore compute the MWR only over the *covered sleeve* —
    the shares the order history actually pays for — so cash flows and terminal
    value stay consistent. Uncovered positions are reported, not silently mixed
    into a meaningless whole-book number.
    """
    if end is None:
        end = date.today()

    flows = broker.get_cash_flows()
    if not flows:
        raise ValueError("No filled equity orders found in Robinhood order history")

    positions = broker.get_positions()
    book_value = sum(p["market_value"] for p in positions.values())

    # ── Coverage reconciliation ───────────────────────────────────────────────
    # Net shares acquired through visible orders, per ticker. The covered sleeve
    # is min(net acquired, currently held) — you can't attribute more value to
    # order history than you actually still hold.
    net_shares: dict[str, float] = defaultdict(float)
    for f in flows:
        net_shares[f["ticker"]] += f["shares"] if f["side"] == "buy" else -f["shares"]

    covered_value = 0.0
    covered, uncovered = [], []
    for ticker, p in positions.items():
        held = p["shares"]
        px = p["current_price"]
        cov_shares = min(max(net_shares.get(ticker, 0.0), 0.0), held)
        cov_val = cov_shares * px
        covered_value += cov_val
        frac = (cov_shares / held) if held else 0.0
        row = {
            "ticker": ticker,
            "held_shares": round(held, 4),
            "order_shares": round(net_shares.get(ticker, 0.0), 4),
            "covered_value": round(cov_val, 2),
            "covered_frac": round(frac, 4),
        }
        (covered if cov_shares > 1e-6 else uncovered).append(row)

    coverage_ratio = (covered_value / book_value) if book_value else 0.0

    first_date = date.fromisoformat(flows[0]["date"])
    total_bought = -sum(f["amount"] for f in flows if f["amount"] < 0)
    total_sold = sum(f["amount"] for f in flows if f["amount"] > 0)
    net_invested = total_bought - total_sold

    # User XIRR over the covered sleeve: every visible flow + the covered
    # terminal value today (NOT the full book — that would mix in shares the
    # flows never paid for and blow the IRR past any sane bracket).
    amounts = [f["amount"] for f in flows] + [covered_value]
    dts = [date.fromisoformat(f["date"]) for f in flows] + [end]
    user_xirr = _xirr(amounts, dts)
    terminal_value = covered_value

    # Prefetch all benchmark tickers in one batch.
    bm_tickers = {b: BENCHMARKS[b] for b in benchmarks if b in BENCHMARKS}
    prefetch_prices(list(bm_tickers.values()), first_date, end)

    bm_results: list[dict] = []
    for label, ticker in bm_tickers.items():
        series = get_close_series(ticker, start=first_date, end=end)
        if series.empty:
            logger.warning("No price data for benchmark %s (%s)", label, ticker)
            continue
        bm_terminal, _shares = _clone_into_index(flows, series)
        bm_amounts = [f["amount"] for f in flows] + [bm_terminal]
        bm_xirr = _xirr(bm_amounts, dts)
        bm_results.append({
            "label": label,
            "ticker": ticker,
            "terminal_value": round(bm_terminal, 2),
            "xirr": round(bm_xirr, 6) if bm_xirr is not None else None,
            "xirr_diff": round(user_xirr - bm_xirr, 6)
            if (user_xirr is not None and bm_xirr is not None) else None,
            "value_diff": round(terminal_value - bm_terminal, 2),
        })

    return {
        "start_date": flows[0]["date"],
        "end_date": end.isoformat(),
        "n_flows": len(flows),
        "total_bought": round(total_bought, 2),
        "total_sold": round(total_sold, 2),
        "net_invested": round(net_invested, 2),
        "book_value": round(book_value, 2),
        "covered_value": round(covered_value, 2),
        "coverage_ratio": round(coverage_ratio, 4),
        "covered_positions": sorted(covered, key=lambda r: -r["covered_value"]),
        "uncovered_positions": sorted(uncovered, key=lambda r: r["ticker"]),
        "current_value": round(terminal_value, 2),
        "total_gain": round(terminal_value - net_invested, 2),
        "user_xirr": round(user_xirr, 6) if user_xirr is not None else None,
        "benchmarks": bm_results,
        "caveats": [
            f"Coverage: order history accounts for only "
            f"{coverage_ratio*100:.0f}% of your ${book_value:,.0f} book "
            f"(${covered_value:,.0f}). The XIRR reflects ONLY that covered "
            f"sleeve, not the whole portfolio.",
            "Positions transferred in (ACATS) or bought before Robinhood's "
            "order-history window have no order record and are excluded.",
            "Equity only — options P&L and cash are excluded.",
            "Dividends and DRIP are not in stock order history; if present, "
            "cost basis is understated (return overstated).",
            "Money-weighted (XIRR): reflects the timing and size of your actual "
            "contributions, not a buy-and-hold time-weighted return.",
        ],
    }
