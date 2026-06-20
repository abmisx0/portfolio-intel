from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click

from cli.formatters import build_envelope, print_json, print_holdings_table
from core.holdings import portfolio_holdings_table


@click.command()
@click.option("--portfolio", default="live", show_default=True,
              help="Portfolio name from config.py, or 'live' for current Robinhood holdings.")
@click.option("--top", default=20, show_default=True, type=int,
              help="Number of top holdings to return")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]), help="Output format")
def holdings_cmd(portfolio: str, top: int, fmt: str):
    """Show effective single-stock exposures across the portfolio."""
    holdings = portfolio_holdings_table(portfolio, top_n=top)
    data = {"portfolio": portfolio, "top_n": top, "holdings": holdings}
    envelope = build_envelope(
        command="holdings",
        args={"portfolio": portfolio, "top": top},
        data=data,
    )
    if fmt == "json":
        print_json(envelope)
    else:
        print_holdings_table(data)
