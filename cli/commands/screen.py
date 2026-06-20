from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click

from cli.formatters import build_envelope, print_json, print_screen_table
from core.screener import screen


@click.command()
@click.argument("ticker")
@click.option("--portfolio", default="live", show_default=True,
              help="Portfolio to screen against — a config name, or 'live' for current Robinhood holdings.")
@click.option("--allocation", default=0.05, show_default=True, type=float,
              help="Assumed allocation for effective-concentration calc (e.g. 0.05 = 5%)")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]), help="Output format")
def screen_cmd(ticker: str, portfolio: str, allocation: float, fmt: str):
    """Screen a candidate ETF against your portfolio."""
    data = screen(ticker.upper(), portfolio_name=portfolio, candidate_allocation=allocation)
    envelope = build_envelope(
        command="screen",
        args={"ticker": ticker.upper(), "portfolio": portfolio, "allocation": allocation},
        data=data,
        data_freshness=data.get("data_freshness"),
    )
    if fmt == "json":
        print_json(envelope)
    else:
        print_screen_table(data)
