"""
Realized gains engine: reconstructs realized P&L, income, and carry costs for a
tax year from Robinhood's read-only history endpoints.

Tax treatment follows 1099 conventions where the data allows:

  stocks   — FIFO lot matching over stock orders PLUS assignment/exercise
             equity components from option events (assignments never appear in
             stock order history). Short- vs long-term uses the anniversary
             rule (long-term starts the day AFTER the 1-year anniversary, with
             a leap-day guard — same convention as broker.get_purchase_dates).
             Same-day transactions process buys before sells. Shares whose
             basis predates the visible order history (ACATS transfers) are
             reported with unknown basis, excluded from totals, and flagged.

  options  — per-contract FIFO ledger over leg-level executions (roll orders
             mix open+close legs, so premium is attributed per leg; order fees
             are allocated to legs pro-rata by |cash|). Each closing execution
             realizes its matched share of the opening premium on the close
             date, so partial closes land in the correct tax year. Expirations
             realize remaining open lots at the expiration date. **Assigned or
             exercised contracts' premium is NOT option P&L**: per 1099 rules
             it folds into the stock leg (short call → added to sale proceeds;
             short put → subtracted from share basis), taking the stock's
             holding-period character. Contracts with closes that exceed
             visible opens (opened pre-window) are excluded and flagged.
             Directly-realized option P&L is classified short-term.

  income   — dividends (paid/reinvested), cash sweep interest; margin interest
             is reported as an expense.

Ground truth remains Robinhood's Tax Center / 1099: wash-sale adjustments are
not modeled, and order history inherits robin_stocks' silent pagination
truncation.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import date

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import broker

logger = logging.getLogger(__name__)


def _is_long_term(buy: str, sell: str) -> bool:
    """Long-term iff sell is strictly after the 1-year anniversary of buy
    (leap-day guard: Feb 29 anniversaries roll to Mar 1)."""
    b, s = date.fromisoformat(buy), date.fromisoformat(sell)
    try:
        anniversary = b.replace(year=b.year + 1)
    except ValueError:
        anniversary = b.replace(year=b.year + 1, month=3, day=1)
    return s > anniversary


def _option_ledger(orders: list[dict], events_by_ticker: dict[str, list[dict]],
                   today: date, year: int) -> dict:
    """Per-contract FIFO ledger. Returns realized closes, folded assignment
    premium per option_id, open unrealized premium, and unknown-basis flags."""
    # Chronological leg stream per contract, fees allocated pro-rata by |cash|.
    legs_by_id: dict[str, list[dict]] = defaultdict(list)
    meta: dict[str, dict] = {}
    for o in orders:
        total_abs = sum(abs(l["cash"]) for l in o["legs"]) or 1.0
        for l in o["legs"]:
            if l["quantity"] <= 0:
                continue
            fee = o["fees"] * abs(l["cash"]) / total_abs
            legs_by_id[l["option_id"]].append({**l, "cash": l["cash"] - fee})
            meta.setdefault(l["option_id"], {
                "ticker": o["ticker"], "option_type": l["option_type"],
                "strike": l["strike"], "expiration": l["expiration"],
                "multiplier": o.get("multiplier") or 100.0,
            })

    # Assignment / exercise events per contract (chronological).
    events_by_id: dict[str, list[dict]] = defaultdict(list)
    for evs in events_by_ticker.values():
        for ev in evs:
            if ev["option_id"] and ev["type"] in ("assignment", "exercise"):
                events_by_id[ev["option_id"]].append(ev)

    realized, folded, open_pos, unknown = [], {}, [], []
    for oid, legs in legs_by_id.items():
        legs.sort(key=lambda l: l["date"])
        m = meta[oid]
        lots: deque = deque()  # [qty_remaining, open_cash_per_contract]
        unknown_close_cash, unknown_qty = 0.0, 0.0

        def _match(qty: float) -> tuple[float, float]:
            """Pop up to qty contracts FIFO; returns (matched_qty, open_cash)."""
            matched, cash = 0.0, 0.0
            while qty > 1e-9 and lots:
                lot = lots[0]
                take = min(lot[0], qty)
                cash += take * lot[1]
                matched += take
                lot[0] -= take
                qty -= take
                if lot[0] <= 1e-9:
                    lots.popleft()
            return matched, cash

        for l in legs:
            if l["position_effect"] == "open":
                lots.append([l["quantity"], l["cash"] / l["quantity"]])
                continue
            matched, open_cash = _match(l["quantity"])
            if matched > 0:
                realized.append({
                    "ticker": m["ticker"], "closed_on": l["date"], "how": "closed",
                    "option_type": m["option_type"], "strike": m["strike"],
                    "expiration": m["expiration"],
                    "premium_pl": open_cash + l["cash"] * (matched / l["quantity"]),
                })
            short = l["quantity"] - matched
            if short > 1e-9:  # close with no visible open — pre-window contract
                unknown_close_cash += l["cash"] * (short / l["quantity"])
                unknown_qty += short

        for ev in sorted(events_by_id.get(oid, []), key=lambda e: e["date"]):
            matched, open_cash = _match(ev["quantity"])
            if matched > 0:
                folded[oid] = folded.get(oid, 0.0) + open_cash
            if ev["quantity"] - matched > 1e-9:
                unknown_qty += ev["quantity"] - matched

        if lots:
            remaining_cash = sum(q * per for q, per in lots)
            if m["expiration"] and date.fromisoformat(m["expiration"]) < today:
                realized.append({
                    "ticker": m["ticker"], "closed_on": m["expiration"],
                    "how": "expired", "option_type": m["option_type"],
                    "strike": m["strike"], "expiration": m["expiration"],
                    "premium_pl": remaining_cash,
                })
            else:
                open_pos.append({"option_id": oid, **m, "cash": remaining_cash})

        if unknown_qty > 1e-9:
            unknown.append({
                "ticker": m["ticker"], "option_type": m["option_type"],
                "strike": m["strike"], "expiration": m["expiration"],
                "quantity": unknown_qty, "close_cash": unknown_close_cash,
            })

    in_year = [r for r in realized if r["closed_on"].startswith(str(year))]
    in_year.sort(key=lambda r: r["closed_on"])
    return {
        "closed": in_year,
        "net": sum(r["premium_pl"] for r in in_year),
        "folded_by_id": folded,           # premium moved into stock legs
        "open_unrealized_premium": sum(p["cash"] for p in open_pos),
        "unknown_basis_contracts": unknown,
        "meta": meta,
    }


def _equity_transactions(events_by_ticker: dict[str, list[dict]],
                         ledger: dict) -> list[dict]:
    """Stock orders + option-event equity components (with folded premium
    adjusting the effective price), sorted date-then-buys-first."""
    txns = []
    for f in broker.get_cash_flows():
        if not f["shares"]:
            continue
        txns.append({
            "date": f["date"], "ticker": f["ticker"], "side": f["side"],
            "shares": f["shares"], "price": abs(f["amount"]) / f["shares"],
            "source": "order",
        })

    for evs in events_by_ticker.values():
        for ev in evs:
            premium = ledger["folded_by_id"].get(ev["option_id"], 0.0)
            for c in ev["equity_components"]:
                if not c["quantity"]:
                    continue
                # Assigned short call: premium adds to sale proceeds.
                # Assigned short put: premium reduces purchase basis.
                # (Sign works out identically: credit premium raises an
                # effective sell price and lowers an effective buy price
                # relative to cash paid — both are premium/share applied
                # with the side's sign.)
                adj = premium / c["quantity"] if c["quantity"] else 0.0
                price = c["price"] + adj if c["side"] == "sell" else c["price"] - adj
                txns.append({
                    "date": ev["date"], "ticker": c["ticker"],
                    "side": c["side"], "shares": c["quantity"], "price": price,
                    "source": ev["type"],
                })
    # Buys before sells within a day: order history arrives newest-first and
    # events are appended last; without the side key a same-day round trip
    # would FIFO-match the sell before its own buy exists.
    txns.sort(key=lambda t: (t["date"], 0 if t["side"] == "buy" else 1))
    return txns


def _stock_realized(txns: list[dict], year: int) -> dict:
    """FIFO-match sells against buys; classify ST/LT by matched-lot age."""
    lots: dict[str, deque] = defaultdict(deque)  # ticker -> [shares, price, date]
    sales: list[dict] = []
    for t in txns:
        if t["side"] == "buy":
            lots[t["ticker"]].append([t["shares"], t["price"], t["date"]])
            continue
        remaining, st_pl, lt_pl, basis = t["shares"], 0.0, 0.0, 0.0
        while remaining > 1e-9 and lots[t["ticker"]]:
            lot = lots[t["ticker"]][0]
            take = min(lot[0], remaining)
            pl = take * (t["price"] - lot[1])
            if _is_long_term(lot[2], t["date"]):
                lt_pl += pl
            else:
                st_pl += pl
            basis += take * lot[1]
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-9:
                lots[t["ticker"]].popleft()
        if t["date"].startswith(str(year)):
            sales.append({
                "date": t["date"], "ticker": t["ticker"], "source": t["source"],
                "shares": t["shares"], "price": t["price"],
                "proceeds": t["shares"] * t["price"],
                "short_term_pl": st_pl, "long_term_pl": lt_pl,
                "covered_basis": basis,
                "uncovered_shares": remaining,
                "uncovered_proceeds": remaining * t["price"],
            })
    return {
        "sales": sales,
        "short_term": sum(s["short_term_pl"] for s in sales),
        "long_term": sum(s["long_term_pl"] for s in sales),
        "uncovered_proceeds": sum(s["uncovered_proceeds"] for s in sales),
    }


def compute_realized(year: int | None = None, today: date | None = None) -> dict:
    """Full realized-gains picture for a tax year. Requires broker.login()."""
    today = today or date.today()
    year = year or today.year

    # Single fetch of each history source (rate-limit-sensitive session).
    option_orders = broker.get_option_order_history()
    event_tickers = sorted({o["ticker"] for o in option_orders}
                           | set(broker.get_positions().keys()))
    events_by_ticker = {t: broker.get_option_events(t) for t in event_tickers}

    ledger = _option_ledger(option_orders, events_by_ticker, today, year)
    txns = _equity_transactions(events_by_ticker, ledger)
    stocks = _stock_realized(txns, year)

    dividends = [d for d in broker.get_dividend_history()
                 if d["date"].startswith(str(year)) and d["state"] in ("paid", "reinvested")]
    interest = [i for i in broker.get_interest_payment_history()
                if i["date"].startswith(str(year))]
    margin = [m for m in broker.get_margin_interest_history()
              if m["date"].startswith(str(year))]

    div_total = sum(d["amount"] for d in dividends)
    int_total = sum(i["amount"] for i in interest)
    margin_total = sum(m["amount"] for m in margin)

    folded_total = sum(ledger["folded_by_id"].values())
    caveats = [
        "Ground truth is Robinhood's Tax Center / 1099 — wash-sale adjustments are NOT modeled here.",
        "Assigned/exercised option premium is folded into the stock leg per 1099 rules "
        f"(${folded_total:,.0f} total premium folded), taking the stock's ST/LT character — "
        "it is NOT listed under option P/L.",
        "Directly-realized option P/L is classified short-term.",
        "Order history may be incomplete: Robinhood's endpoints paginate until the server "
        "stops, and truncation is silent; ACATS-transferred shares have no buy record.",
    ]
    if stocks["uncovered_proceeds"] > 0:
        caveats.insert(0, (
            f"${stocks['uncovered_proceeds']:,.0f} of {year} sale proceeds have NO basis in "
            "order history (transferred-in shares) — their gain/loss is unknown here and "
            "EXCLUDED from the totals. Check the Robinhood app for those lots' basis."
        ))
    if ledger["unknown_basis_contracts"]:
        tickers = ", ".join(sorted({c["ticker"] for c in ledger["unknown_basis_contracts"]}))
        caveats.insert(0, (
            f"Option contracts on {tickers} were closed/assigned without a visible opening "
            "order (opened before the history window) — their premium P/L is unknown and "
            "EXCLUDED from the totals."
        ))

    return {
        "year": year,
        "stocks": stocks,
        "options": {k: ledger[k] for k in
                    ("closed", "net", "open_unrealized_premium", "unknown_basis_contracts")},
        "folded_premium": folded_total,
        "income": {
            "dividends": div_total, "dividend_count": len(dividends),
            "interest": int_total, "interest_count": len(interest),
            "margin_interest": margin_total,
        },
        "totals": {
            "short_term": stocks["short_term"] + ledger["net"],
            "long_term": stocks["long_term"],
            "net_realized": stocks["short_term"] + stocks["long_term"] + ledger["net"],
            "net_income": div_total + int_total - margin_total,
        },
        "caveats": caveats,
    }
