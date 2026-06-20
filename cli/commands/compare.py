from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click

from cli.formatters import build_envelope, print_json, print_compare_table, print_compare_multi_table
from core.screener import compare, compare_multi


@click.command()
@click.argument("tickers", nargs=-1, metavar="TICKER")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]), help="Output format")
def compare_cmd(tickers: tuple, fmt: str):
    """Side-by-side comparison of 2+ ETFs.

    \b
    Examples:
      compare QQQ SPY
      compare GLD IAU SGOL GLDM
      compare SMH SOXX QQQ --format json
    """
    if len(tickers) < 2:
        raise click.UsageError("Provide at least two tickers.")

    if len(tickers) == 2:
        a, b = tickers[0].upper(), tickers[1].upper()
        data = compare(a, b)
        envelope = build_envelope(
            command="compare",
            args={"tickers": [a, b]},
            data=data,
            data_freshness=data.get("data_freshness"),
        )
        if fmt == "json":
            print_json(envelope)
        else:
            print_compare_table(data)
    else:
        data = compare_multi(list(tickers))
        envelope = build_envelope(
            command="compare",
            args={"tickers": [t.upper() for t in tickers]},
            data=data,
            data_freshness=data.get("data_freshness"),
        )
        if fmt == "json":
            print_json(envelope)
        else:
            print_compare_multi_table(data)
