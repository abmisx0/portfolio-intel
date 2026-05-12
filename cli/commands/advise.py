from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import date, timedelta

import click
import numpy as np
import pandas as pd
from tabulate import tabulate

from concurrent.futures import ThreadPoolExecutor

from cli.formatters import build_envelope, print_json
from core.broker import login, get_account_data, get_watchlist, get_purchase_dates
from core.data_fetcher import get_close_series, prefetch_prices
from core.analytics import TRADING_DAYS, _get_rfr
from config import PORTFOLIOS

LOOKBACK_DAYS = 365 * 3
CANDIDATE_ALLOCATION = 0.05
MIN_HISTORY_DAYS = 252

# Curated universe screened for discovery suggestions — thematically aligned with the
# user's macro-driven ETF approach (defense, semis, energy, metals, frontier tech).
# Tickers already in the portfolio or watchlist are filtered out at runtime.
DISCOVERY_UNIVERSE: dict[str, str] = {
    # Defense / Aerospace
    "ITA": "iShares U.S. Aerospace & Defense ETF",
    "SHLD": "Global X Defense Tech ETF",
    "CODA": "Themes Transatlantic Defense ETF",
    # Semiconductors / supply chain
    "SOXX": "iShares Semiconductor ETF",
    "AMAT": "Applied Materials",
    "ASML": "ASML Holding",
    "AVGO": "Broadcom",
    "MU": "Micron Technology",
    # Energy
    "XLE": "Energy Select Sector SPDR",
    "OIH": "VanEck Oil Services ETF",
    "AMLP": "Alerian MLP ETF",
    "XOM": "Exxon Mobil",
    "CVX": "Chevron",
    # Precious metals
    "GLD": "SPDR Gold Shares",
    "GOAU": "US Global GO GOLD ETF",
    "GDX": "VanEck Gold Miners ETF",
    "GDXJ": "VanEck Junior Gold Miners ETF",
    "PSLV": "Sprott Physical Silver Trust",
    # Nuclear / uranium alternatives
    "URA": "Global X Uranium ETF",
    "CCJ": "Cameco Corp",
    # Healthcare
    "XLV": "Health Care Select Sector SPDR",
    "ABBV": "AbbVie",
    "UNH": "UnitedHealth Group",
    # AI / Robotics
    "BOTZ": "Global X Robotics & AI ETF",
    "AIQ": "Global X AI & Technology ETF",
    # Tech
    "MSFT": "Microsoft",
    "META": "Meta Platforms",
    # International
    "EWJ": "iShares MSCI Japan ETF",
    "VEA": "Vanguard Developed Markets ETF",
    "MCHI": "iShares MSCI China ETF",
    # Commodities
    "PDBC": "Invesco Optimum Yield Commodity ETF",
    # Infrastructure
    "PAVE": "Global X U.S. Infrastructure Development ETF",
    "IGF": "iShares Global Infrastructure ETF",
    # Financials
    "JPM": "JPMorgan Chase",
    "BRK-B": "Berkshire Hathaway B",
    # Frontier / Quantum extras
    "IONQ": "IonQ",
    # Crypto infrastructure
    "MSTR": "MicroStrategy",
    # Consumer staples (defensive diversifier)
    "XLP": "Consumer Staples Select Sector SPDR",
    "KO": "Coca-Cola",
}


# ── Metric helpers ─────────────────────────────────────────────────────────────

def _ann_return(rets: pd.Series) -> float:
    return (1 + rets.mean()) ** TRADING_DAYS - 1


def _ann_vol(rets: pd.Series) -> float:
    return float(rets.std() * np.sqrt(TRADING_DAYS))


def _sharpe(rets: pd.Series, rfr: float) -> float:
    vol = _ann_vol(rets)
    return (_ann_return(rets) - rfr) / vol if vol > 0 else float("nan")


def _sortino(rets: pd.Series, rfr: float) -> float:
    downside = rets[rets < 0]
    if downside.empty:
        return float("inf")
    dd_vol = float(downside.std() * np.sqrt(TRADING_DAYS))
    return (_ann_return(rets) - rfr) / dd_vol if dd_vol > 0 else float("nan")


def _max_drawdown(prices: pd.Series) -> float:
    return float((prices / prices.cummax() - 1).min())


def _portfolio_returns(price_map: dict[str, pd.Series], weights: dict[str, float]) -> pd.Series:
    aligned = pd.concat(price_map.values(), axis=1, keys=price_map.keys()).dropna()
    if aligned.empty:
        return pd.Series(dtype=float)
    daily = aligned.pct_change().dropna()
    total_w = sum(weights.get(t, 0) for t in aligned.columns)
    if total_w == 0:
        return pd.Series(dtype=float)
    w = pd.Series({t: weights.get(t, 0) / total_w for t in aligned.columns})
    return (daily * w).sum(axis=1)


def _avg_corr(cand_rets: pd.Series, price_map: dict[str, pd.Series]) -> float | None:
    corrs = []
    for prices in price_map.values():
        aligned = pd.concat([cand_rets, prices.pct_change().dropna()], axis=1).dropna()
        if len(aligned) > 30:
            corrs.append(float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1])))
    return float(np.mean(corrs)) if corrs else None


def _load_price_map(tickers: list[str], start: date, end: date) -> dict[str, pd.Series]:
    """Batch-prefetch to cache, then read all series sequentially."""
    prefetch_prices(tickers, start, end)
    price_map: dict[str, pd.Series] = {}
    for ticker in tickers:
        try:
            s = get_close_series(ticker, str(start), str(end))
            if s is not None and not s.empty:
                price_map[ticker] = s
        except Exception:
            pass
    return price_map


# ── Core scoring engines ────────────────────────────────────────────────────────

def _score_trims(
    current_weights: dict[str, float],
    current_price_map: dict[str, pd.Series],
    holdings: dict[str, dict],
    rfr: float,
) -> tuple[dict, list[dict]]:
    """
    For each position, compute what happens to portfolio Sharpe/Sortino if it's removed.
    Positive delta = position is dragging the portfolio = trim candidate.
    """
    baseline_rets = _portfolio_returns(current_price_map, current_weights)
    baseline = {
        "sharpe": _sharpe(baseline_rets, rfr),
        "sortino": _sortino(baseline_rets, rfr),
        "ann_return": _ann_return(baseline_rets),
        "ann_vol": _ann_vol(baseline_rets),
    }

    results = []
    for ticker, prices in current_price_map.items():
        remaining_map = {t: s for t, s in current_price_map.items() if t != ticker}
        remaining_weights = {t: w for t, w in current_weights.items() if t != ticker}
        if not remaining_map:
            continue

        new_rets = _portfolio_returns(remaining_map, remaining_weights)
        new_sharpe = _sharpe(new_rets, rfr)
        new_sortino = _sortino(new_rets, rfr)

        pos_rets = prices.pct_change().dropna()
        h = holdings.get(ticker, {})

        results.append({
            "ticker": ticker,
            "weight": current_weights.get(ticker, 0),
            "market_value": h.get("market_value", 0),
            "gain_pct": h.get("gain_pct", 0),
            "pos_sharpe": _sharpe(pos_rets, rfr),
            "pos_sortino": _sortino(pos_rets, rfr),
            "pos_dd": _max_drawdown(prices),
            "delta_sharpe": new_sharpe - baseline["sharpe"]
                if not (np.isnan(new_sharpe) or np.isnan(baseline["sharpe"])) else None,
            "delta_sortino": new_sortino - baseline["sortino"]
                if not (np.isnan(new_sortino) or np.isnan(baseline["sortino"])) else None,
        })

    # Sort: positions whose removal would most improve the portfolio come first
    results.sort(key=lambda x: (x["delta_sharpe"] or -99), reverse=True)
    return baseline, results


def _score_additions(
    current_weights: dict[str, float],
    current_price_map: dict[str, pd.Series],
    candidates: list[dict],  # list of {ticker, name, price, ...}
    start: date,
    end: date,
    rfr: float,
) -> list[dict]:
    """Score candidates by their marginal impact when added at CANDIDATE_ALLOCATION."""
    baseline_rets = _portfolio_returns(current_price_map, current_weights)
    baseline_sharpe = _sharpe(baseline_rets, rfr)
    baseline_sortino = _sortino(baseline_rets, rfr)

    results = []
    for cand in candidates:
        ticker = cand["ticker"]
        cand_prices = cand.get("_prices")

        if cand_prices is None or len(cand_prices) < MIN_HISTORY_DAYS:
            results.append({**cand, "skip_reason": "insufficient price history"})
            continue

        cand_rets = cand_prices.pct_change().dropna()
        corr = _avg_corr(cand_rets, current_price_map)

        scale = 1 - CANDIDATE_ALLOCATION
        new_weights = {t: w * scale for t, w in current_weights.items()}
        new_weights[ticker] = CANDIDATE_ALLOCATION
        new_rets = _portfolio_returns({**current_price_map, ticker: cand_prices}, new_weights)

        new_sharpe = _sharpe(new_rets, rfr)
        new_sortino = _sortino(new_rets, rfr)

        results.append({
            **cand,
            "cand_sharpe": _sharpe(cand_rets, rfr),
            "cand_sortino": _sortino(cand_rets, rfr),
            "cand_dd": _max_drawdown(cand_prices),
            "avg_corr": corr,
            "delta_sharpe": new_sharpe - baseline_sharpe
                if not (np.isnan(new_sharpe) or np.isnan(baseline_sharpe)) else None,
            "delta_sortino": new_sortino - baseline_sortino
                if not (np.isnan(new_sortino) or np.isnan(baseline_sortino)) else None,
            "skip_reason": None,
        })

    def _rank(r):
        ds = r.get("delta_sharpe") or 0
        dso = r.get("delta_sortino") or 0
        corr = r.get("avg_corr") or 0
        return (ds + dso) / 2 - corr * 0.01

    scorable = sorted([r for r in results if not r.get("skip_reason")], key=_rank, reverse=True)
    skipped = [r for r in results if r.get("skip_reason")]
    return scorable + skipped


# ── Output helpers ──────────────────────────────────────────────────────────────

def _fmt_delta(v) -> str:
    return f"{v:+.3f}" if v is not None and not np.isnan(v) else "—"

def _fmt_metric(v) -> str:
    return f"{v:.3f}" if v is not None and not np.isnan(v) else "—"

def _fmt_pct(v) -> str:
    return f"{v:.0%}" if v is not None and not np.isnan(v) else "—"

def _tax_fields(pd_info: dict) -> dict:
    has_stcg = pd_info.get("has_short_term_lots")
    ltcg_date = pd_info.get("ltcg_all_lots_date")
    if has_stcg and ltcg_date:
        status = f"STCG→LTCG {ltcg_date}"
    elif has_stcg is False:
        status = "LTCG"
    else:
        status = None
    return {"tax_status": status, "ltcg_date": ltcg_date, "first_purchase": pd_info.get("first_purchase")}


def _trim_signal(r) -> str:
    ds = r.get("delta_sharpe") or 0
    ps = r.get("pos_sharpe") or 0
    w = r.get("weight") or 0
    if ds > 0.05:
        return "EXIT"
    if ds > 0.01 or ps < 0.3:
        return "TRIM"
    if w > 0.25:
        return "REDUCE"
    return "HOLD"


def _addition_rows(scored: list[dict], budget: float) -> list[list]:
    rows = []
    for r in scored:
        if r.get("skip_reason"):
            continue
        price = r.get("price") or 0
        shares = int(budget / price) if price else 0
        rows.append([
            r["ticker"],
            (r.get("name") or "")[:30],
            _fmt_delta(r.get("delta_sharpe")),
            _fmt_delta(r.get("delta_sortino")),
            _fmt_metric(r.get("cand_sharpe")),
            _fmt_metric(r.get("cand_sortino")),
            _fmt_pct(r.get("cand_dd")),
            f"{r['avg_corr']:.2f}" if r.get("avg_corr") is not None else "—",
            f"{shares} shr",
            f"${shares * price:,.0f}",
        ])
    return rows

_ADDITION_HEADERS = [
    "Ticker", "Name", "ΔSharpe", "ΔSortino",
    "Sharpe", "Sortino", "Max DD", "Avg Corr",
    "Shares (5%)", "Cost",
]


# ── Command ─────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--portfolio", default=None,
              help="Target portfolio (e.g. v8) to cross-reference trim signals with drift.")
@click.option("--discoveries", default=5, show_default=True, type=int,
              help="Number of universe discovery suggestions to show.")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def advise_cmd(portfolio: str | None, discoveries: int, fmt: str):
    """
    Full portfolio advisory: health check, trim signals, watchlist screening,
    and discovery suggestions from a curated thematic universe.

    Fetches live positions and watchlist directly from Robinhood.
    """
    if portfolio and portfolio not in PORTFOLIOS:
        click.echo(f"  Unknown portfolio '{portfolio}'. Valid: {', '.join(PORTFOLIOS)}", err=True)
        sys.exit(1)

    try:
        login()
        with ThreadPoolExecutor(max_workers=3) as executor:
            account_future = executor.submit(get_account_data)
            watchlist_future = executor.submit(get_watchlist)
            purchase_future = executor.submit(get_purchase_dates)
        holdings, total_value = account_future.result()
        watchlist = watchlist_future.result()
        purchase_dates = purchase_future.result()
    except Exception as e:
        click.echo(f"  Robinhood error: {e}", err=True)
        sys.exit(1)

    if not holdings:
        click.echo("  No live positions found.", err=True)
        sys.exit(1)

    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    rfr = _get_rfr()

    held_tickers = set(holdings.keys())
    watchlist_tickers = {w["ticker"] for w in watchlist}

    # Candidates for addition: watchlist items not already held
    watchlist_candidates = [
        {"ticker": w["ticker"], "name": w["name"], "price": w["price"]}
        for w in watchlist if w["ticker"] not in held_tickers
    ]

    # Discovery candidates: universe tickers not held and not in watchlist
    discovery_candidates = [
        {"ticker": t, "name": n, "price": 0.0}
        for t, n in DISCOVERY_UNIVERSE.items()
        if t not in held_tickers and t not in watchlist_tickers
    ]

    all_fetch_tickers = (
        list(held_tickers)
        + [c["ticker"] for c in watchlist_candidates]
        + [c["ticker"] for c in discovery_candidates]
    )

    if fmt == "table":
        click.echo(f"\n  Fetching price history for {len(all_fetch_tickers)} tickers...")

    price_map = _load_price_map(all_fetch_tickers, start, end)

    # Portfolio simulation uses only holdings with sufficient history
    sim_price_map = {t: s for t, s in price_map.items()
                     if t in held_tickers and len(s) >= MIN_HISTORY_DAYS}
    current_weights = {t: holdings[t]["portfolio_pct"] for t in sim_price_map}

    excluded_from_sim = held_tickers - set(sim_price_map)

    if not current_weights:
        click.echo("  No holdings with sufficient price history to simulate.", err=True)
        sys.exit(1)

    # Attach fetched prices to candidate dicts
    for c in watchlist_candidates:
        c["_prices"] = price_map.get(c["ticker"])
        if c["_prices"] is not None and c["price"] == 0.0:
            c["price"] = float(c["_prices"].iloc[-1])
    for c in discovery_candidates:
        c["_prices"] = price_map.get(c["ticker"])
        if c["_prices"] is not None and c["price"] == 0.0:
            c["price"] = float(c["_prices"].iloc[-1])

    # Run all three scoring passes
    baseline, trim_scores = _score_trims(current_weights, sim_price_map, holdings, rfr)
    watchlist_scored = _score_additions(current_weights, sim_price_map, watchlist_candidates, start, end, rfr)
    discovery_scored = _score_additions(current_weights, sim_price_map, discovery_candidates, start, end, rfr)

    # Target portfolio tickers for drift cross-reference
    target_tickers: set[str] = set()
    if portfolio:
        target_tickers = {p["ticker"] for p in PORTFOLIOS[portfolio]}

    budget = total_value * CANDIDATE_ALLOCATION

    # ── JSON output ──────────────────────────────────────────────────────────
    if fmt == "json":
        def _cand_dict(r):
            price = r.get("price") or 0
            shares = int(budget / price) if price else None
            return {
                "ticker": r["ticker"],
                "name": r.get("name", ""),
                "price": price,
                "delta_sharpe": r.get("delta_sharpe"),
                "delta_sortino": r.get("delta_sortino"),
                "cand_sharpe": r.get("cand_sharpe"),
                "cand_sortino": r.get("cand_sortino"),
                "cand_max_drawdown": r.get("cand_dd"),
                "avg_corr": r.get("avg_corr"),
                "suggested_shares": shares,
                "suggested_cost": round(shares * price, 2) if shares else None,
                "skip_reason": r.get("skip_reason"),
            }

        print_json(build_envelope(
            command="advise",
            args={"portfolio": portfolio},
            data={
                "total_value": total_value,
                "baseline": baseline,
                "excluded_from_simulation": sorted(excluded_from_sim),
                "trim_signals": [
                    {
                        "ticker": r["ticker"],
                        "weight": r["weight"],
                        "market_value": r["market_value"],
                        "gain_pct": r["gain_pct"],
                        "pos_sharpe": r["pos_sharpe"],
                        "pos_sortino": r["pos_sortino"],
                        "pos_max_drawdown": r["pos_dd"],
                        "delta_sharpe_if_removed": r["delta_sharpe"],
                        "delta_sortino_if_removed": r["delta_sortino"],
                        "signal": _trim_signal(r),
                        "in_target": r["ticker"] in target_tickers if portfolio else None,
                        **_tax_fields(purchase_dates.get(r["ticker"], {})),
                    }
                    for r in trim_scores
                ],
                "watchlist_candidates": [_cand_dict(r) for r in watchlist_scored],
                "discovery_suggestions": [_cand_dict(r) for r in discovery_scored
                                          if not r.get("skip_reason")][:discoveries],
            },
        ))
        return

    # ── Table output ─────────────────────────────────────────────────────────

    click.echo(f"\n{'='*82}")
    click.echo(f"  PORTFOLIO ADVISORY")
    click.echo(f"{'='*82}")
    click.echo(
        f"  Portfolio: ${total_value:,.2f}  |  "
        f"Sharpe: {baseline['sharpe']:.3f}  |  "
        f"Sortino: {baseline['sortino']:.3f}  |  "
        f"Ann. Return: {baseline['ann_return']:.1%}  |  "
        f"Ann. Vol: {baseline['ann_vol']:.1%}"
    )
    click.echo(f"  RFR: {rfr:.1%}  |  Lookback: 3Y  |  Simulation: {len(sim_price_map)}/{len(holdings)} positions")
    if excluded_from_sim:
        click.echo(f"  Excluded (< 1Y history): {', '.join(sorted(excluded_from_sim))}")

    # ── Section 1: Trim Signals ──────────────────────────────────────────────
    click.echo(f"\n{'─'*82}")
    click.echo(f"  SECTION 1 — TRIM SIGNALS  (ΔSharpe = improvement if this position is removed)")
    click.echo(f"{'─'*82}")

    trim_rows = []
    for r in trim_scores:
        signal = _trim_signal(r)
        drift_flag = "" if not portfolio else ("" if r["ticker"] in target_tickers else " ← not in target")
        pd_info = purchase_dates.get(r["ticker"], {})
        tax = _tax_fields(pd_info)
        trim_rows.append([
            r["ticker"] + drift_flag,
            f"{r['weight']:.1%}",
            f"${r['market_value']:,.0f}",
            f"{r['gain_pct']:+.1f}%",
            _fmt_metric(r["pos_sharpe"]),
            _fmt_metric(r["pos_sortino"]),
            _fmt_pct(r["pos_dd"]),
            _fmt_delta(r["delta_sharpe"]),
            _fmt_delta(r["delta_sortino"]),
            signal,
            tax["tax_status"] or "—",
        ])

    click.echo(tabulate(
        trim_rows,
        headers=["Ticker", "Weight", "Value", "G/L", "Sharpe", "Sortino",
                 "Max DD", "ΔSharpe↑", "ΔSortino↑", "Signal", "Tax"],
        tablefmt="simple",
    ))
    click.echo("  ΔSharpe↑ / ΔSortino↑: positive = portfolio improves if removed.")
    if portfolio:
        click.echo(f"  '← not in target' = outside {portfolio} allocation. Consider exiting.")

    # ── Section 2: Watchlist Candidates ─────────────────────────────────────
    click.echo(f"\n{'─'*82}")
    click.echo(f"  SECTION 2 — WATCHLIST CANDIDATES  (ranked by composite portfolio impact)")
    click.echo(f"{'─'*82}")
    click.echo(f"  Candidate allocation assumed: 5% (${budget:,.0f})")

    wl_scorable = [r for r in watchlist_scored if not r.get("skip_reason")]
    wl_skipped = [r for r in watchlist_scored if r.get("skip_reason")]

    if wl_scorable:
        click.echo(tabulate(
            _addition_rows(wl_scorable, budget),
            headers=_ADDITION_HEADERS,
            tablefmt="simple",
        ))
    else:
        click.echo("  No watchlist candidates with sufficient price history.")

    already_held_wl = [w for w in watchlist if w["ticker"] in held_tickers]
    if already_held_wl:
        click.echo(f"  Already held (in watchlist): {', '.join(w['ticker'] for w in already_held_wl)}")
    if wl_skipped:
        click.echo(f"  Skipped (no data): {', '.join(r['ticker'] for r in wl_skipped)}")

    # ── Section 3: Discovery Suggestions ────────────────────────────────────
    click.echo(f"\n{'─'*82}")
    click.echo(f"  SECTION 3 — DISCOVERY SUGGESTIONS  (top {discoveries} from thematic universe)")
    click.echo(f"{'─'*82}")
    click.echo(f"  Screening {len(discovery_candidates)} thematic tickers not in your portfolio or watchlist.")

    disc_scorable = [r for r in discovery_scored if not r.get("skip_reason")]
    disc_skipped = [r for r in discovery_scored if r.get("skip_reason")]

    if disc_scorable:
        click.echo(tabulate(
            _addition_rows(disc_scorable[:discoveries], budget),
            headers=_ADDITION_HEADERS,
            tablefmt="simple",
        ))
    else:
        click.echo("  No discovery candidates with sufficient price history.")

    if disc_skipped:
        click.echo(f"  Skipped (no data): {', '.join(r['ticker'] for r in disc_skipped)}")

    click.echo()
