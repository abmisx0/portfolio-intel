from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json
from core.rebalancer import compute_rebalance, parse_current_weights


@click.command()
@click.option("--portfolio", default="proposed", show_default=True,
              help="Target portfolio (proposed|previous)")
@click.option("--value", default=None, type=float,
              help="Total portfolio value in dollars. Auto-fetched from Robinhood if --from-robinhood.")
@click.option("--current", default=None,
              help="Current allocations as 'VOO:0.32,NLR:0.13,...' (optional; triggers drift analysis)")
@click.option("--from-robinhood", "from_robinhood", is_flag=True, default=False,
              help="Fetch current positions and portfolio value live from Robinhood.")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def rebalance_cmd(portfolio: str, value: float | None, current: str | None,
                  from_robinhood: bool, fmt: str):
    """
    Compute target allocation and rebalance trades.

    Without --current/--from-robinhood: shows ideal dollar allocation at target weights.
    With --current:                     shows drift from target and recommended trades.
    With --from-robinhood:              fetches live positions from Robinhood automatically.
    """
    current_weights: dict | None = None

    if from_robinhood:
        try:
            from core.broker import login, get_positions, get_account_data
            login()
            if value is None:
                holdings, value = get_account_data()
            else:
                holdings = get_positions()
            current_weights = {t: d["portfolio_pct"] for t, d in holdings.items()}
        except Exception as e:
            click.echo(f"  Robinhood fetch failed: {e}", err=True)
            sys.exit(1)
    elif current:
        current_weights = parse_current_weights(current)

    if value is None:
        raise click.UsageError("--value is required unless --from-robinhood is used.")

    data = compute_rebalance(portfolio, value, current_weights)

    envelope = build_envelope(
        command="rebalance",
        args={"portfolio": portfolio, "value": value, "current": current},
        data=data,
        data_freshness=data["summary"].get("data_freshness"),
    )

    if fmt == "json":
        print_json(envelope)
        return

    # Table output
    positions = data["positions"]
    summary = data["summary"]
    mode = data["mode"]

    click.echo(f"\n{'='*65}")
    click.echo(f"  REBALANCE PLAN: portfolio={portfolio}  total=${value:,.0f}")
    click.echo(f"  Mode: {mode.upper()}")
    click.echo(f"{'='*65}")

    if mode == "target":
        rows = [
            [
                p["ticker"],
                p.get("theme", ""),
                f"{p['target_weight']*100:.1f}%",
                f"${p['target_dollars']:,.0f}",
                f"${p['current_price']:,.2f}" if p["current_price"] else "N/A",
                f"{p['target_shares']:.1f}" if p["target_shares"] else "N/A",
            ]
            for p in positions
        ]
        click.echo(tabulate(
            rows,
            headers=["Ticker", "Theme", "Target Wt", "Target $", "Price", "Shares"],
            tablefmt="simple",
        ))
    else:
        rows = [
            [
                p["ticker"],
                f"{p['target_weight']*100:.1f}%",
                f"{p['current_weight']*100:.1f}%",
                f"{p['drift']*100:+.1f}%",
                p["trade_direction"],
                f"${p['trade_dollars']:,.0f}",
                f"{p['trade_shares']:.1f}" if p.get("trade_shares") else "—",
            ]
            for p in positions
        ]
        click.echo(tabulate(
            rows,
            headers=["Ticker", "Target", "Current", "Drift", "Action", "$ Amount", "Shares"],
            tablefmt="simple",
        ))
        click.echo(f"\n  Buys : {summary['buys']} positions  +${summary['total_buy_dollars']:,.0f}")
        click.echo(f"  Sells: {summary['sells']} positions  -${summary['total_sell_dollars']:,.0f}")
        if summary.get("max_drift_ticker"):
            click.echo(f"  Largest drift: {summary['max_drift_ticker']} ({summary['max_drift']*100:+.1f}%)")

    if summary.get("data_freshness"):
        click.echo(f"\n  Prices as of: {summary['data_freshness']}")
    click.echo()
