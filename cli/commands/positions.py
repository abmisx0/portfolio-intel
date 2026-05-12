from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from concurrent.futures import ThreadPoolExecutor
from tabulate import tabulate

from cli.formatters import build_envelope, print_json
from config import PORTFOLIOS
from core.broker import login, get_account_data, get_purchase_dates
from core.rebalancer import compute_rebalance


@click.command()
@click.option("--portfolio", default=None,
              help="Target portfolio to compare against (e.g. v8). Shows drift and share trades.")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def positions_cmd(portfolio: str | None, fmt: str):
    """
    Show current Robinhood positions.

    Without --portfolio: raw holdings (shares, price, market value, G/L).
    With --portfolio:    drift vs. target allocation + share trades needed.
    """
    try:
        login()
        with ThreadPoolExecutor(max_workers=2) as executor:
            account_future = executor.submit(get_account_data)
            purchase_future = executor.submit(get_purchase_dates)
        holdings, total_value = account_future.result()
        purchase_dates = purchase_future.result()
    except Exception as e:
        click.echo(f"  Robinhood error: {e}", err=True)
        sys.exit(1)

    if not holdings:
        click.echo("  No positions found in Robinhood account.")
        return

    if portfolio and portfolio not in PORTFOLIOS:
        click.echo(f"  Unknown portfolio '{portfolio}'. Valid: {', '.join(PORTFOLIOS)}", err=True)
        sys.exit(1)

    current_weights = {t: d["portfolio_pct"] for t, d in holdings.items()}
    plan = compute_rebalance(portfolio, total_value, current_weights) if portfolio else None

    if fmt == "json":
        positions_out = {t: {**d, **purchase_dates.get(t, {})} for t, d in holdings.items()}
        payload: dict = {"total_value": total_value, "positions": positions_out}
        if plan:
            payload["rebalance"] = plan
        print_json(build_envelope(
            command="positions",
            args={"portfolio": portfolio},
            data=payload,
        ))
        return

    click.echo(f"\n{'='*70}")
    click.echo(f"  ROBINHOOD POSITIONS")
    click.echo(f"{'='*70}")

    rows = []
    for ticker, d in sorted(holdings.items(), key=lambda x: -x[1]["market_value"]):
        pd_info = purchase_dates.get(ticker, {})
        first_buy = pd_info.get("first_purchase") or "—"
        ltcg_date = pd_info.get("ltcg_all_lots_date")
        has_stcg = pd_info.get("has_short_term_lots")
        if has_stcg and ltcg_date:
            tax_flag = f"STCG→LTCG {ltcg_date}"
        elif has_stcg is False:
            tax_flag = "LTCG"
        else:
            tax_flag = "—"
        rows.append([
            ticker,
            f"{d['shares']:.4f}".rstrip("0").rstrip("."),
            f"${d['current_price']:,.2f}",
            f"${d['market_value']:,.2f}",
            f"{d['portfolio_pct']*100:.1f}%",
            f"${d['avg_cost']:,.2f}",
            f"{d['gain_pct']:+.1f}%",
            first_buy,
            tax_flag,
        ])

    click.echo(tabulate(
        rows,
        headers=["Ticker", "Shares", "Price", "Market Value", "Portfolio %", "Avg Cost", "G/L%",
                 "First Buy", "Tax Status"],
        tablefmt="simple",
    ))
    click.echo(f"\n  Total Portfolio Value: ${total_value:,.2f}")

    if not plan:
        click.echo()
        return

    positions_data = plan["positions"]
    summary = plan["summary"]
    target_tickers = {p["ticker"] for p in PORTFOLIOS[portfolio]}
    extra = {t: d for t, d in holdings.items() if t not in target_tickers}

    click.echo(f"\n{'='*70}")
    click.echo(f"  DRIFT vs. {portfolio.upper()}")
    click.echo(f"{'='*70}")

    drift_rows = []
    for p in positions_data:
        direction = p.get("trade_direction", "HOLD")
        shares = p.get("trade_shares") or 0
        sign = {"BUY": "+", "SELL": "-"}.get(direction)
        shares_str = f"{sign}{shares:.2f}" if sign else "—"
        drift_rows.append([
            p["ticker"],
            f"{p['target_weight']*100:.1f}%",
            f"{p.get('current_weight', 0)*100:.1f}%",
            f"{p.get('drift', 0)*100:+.1f}%",
            direction,
            f"${p.get('trade_dollars', 0):,.0f}",
            shares_str,
        ])

    click.echo(tabulate(
        drift_rows,
        headers=["Ticker", "Target", "Current", "Drift", "Action", "$ Trade", "Shares"],
        tablefmt="simple",
    ))

    if extra:
        click.echo(f"\n  Positions not in {portfolio} (consider exiting):")
        for ticker, d in extra.items():
            click.echo(f"    {ticker}  {d['shares']:.4f} shares  ${d['market_value']:,.2f}  ({d['portfolio_pct']*100:.1f}%)")

    click.echo(f"\n  Buys : {summary['buys']} positions  +${summary['total_buy_dollars']:,.0f}")
    click.echo(f"  Sells: {summary['sells']} positions  -${summary['total_sell_dollars']:,.0f}")
    if summary.get("data_freshness"):
        click.echo(f"  Prices as of: {summary['data_freshness']}")
    click.echo()
