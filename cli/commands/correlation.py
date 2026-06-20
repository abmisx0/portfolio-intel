from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click

from cli.formatters import build_envelope, print_json, print_correlation_table
from config import LOOKBACK_5Y, resolve_portfolio
from core.data_fetcher import get_close_series, price_map_freshness
from core.analytics import correlation_matrix

_5Y_START = LOOKBACK_5Y


def run_correlation(portfolio_name: str) -> dict:
    try:
        positions = resolve_portfolio(portfolio_name)
    except ValueError as e:
        raise click.UsageError(str(e))

    price_map = {}
    for pos in positions:
        t = pos["ticker"].upper()
        s = get_close_series(t, start=_5Y_START)
        if not s.empty:
            price_map[t] = s

    result = correlation_matrix(price_map)

    data_freshness = price_map_freshness(price_map)

    return {
        "portfolio": portfolio_name,
        "correlation_matrix": result,
        "data_freshness": data_freshness,
    }


@click.command()
@click.option("--portfolio", default="live", show_default=True,
              help="Portfolio name from config.py, or 'live' for current Robinhood holdings.")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]), help="Output format")
def correlation_cmd(portfolio: str, fmt: str):
    """Correlation matrix across all portfolio positions."""
    data = run_correlation(portfolio)
    envelope = build_envelope(
        command="correlation",
        args={"portfolio": portfolio},
        data=data,
        data_freshness=data.get("data_freshness"),
    )
    if fmt == "json":
        print_json(envelope)
    else:
        print_correlation_table(data)
