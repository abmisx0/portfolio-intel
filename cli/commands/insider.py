from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json
from core.finnhub import get_insider_transactions


@click.command()
@click.argument("tickers", nargs=-1, metavar="TICKER")
@click.option("--limit", default=10, show_default=True, type=int,
              help="Max transactions to show per ticker.")
@click.option("--buys-only", is_flag=True, default=False,
              help="Show only open-market purchases (BUY).")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def insider_cmd(tickers: tuple, limit: int, buys_only: bool, fmt: str):
    """Insider transaction filings from SEC Form 4.

    \b
    Examples:
      insider NVDA MU
      insider AAPL --buys-only
      insider SMH --format json
    """
    if not tickers:
        raise click.UsageError("Provide one or more TICKER arguments.")

    results = {}
    for ticker in tickers:
        txns = get_insider_transactions(ticker.upper())
        if buys_only:
            txns = [t for t in txns if t["code"] == "BUY"]
        results[ticker.upper()] = txns[:limit]

    envelope = build_envelope(
        command="insider",
        args={"tickers": [t.upper() for t in tickers], "buys_only": buys_only},
        data=results,
        data_freshness=None,
    )

    if fmt == "json":
        print_json(envelope)
        return

    for ticker, txns in results.items():
        click.echo(f"\n{'='*65}")
        click.echo(f"  INSIDER TRANSACTIONS — {ticker}")
        click.echo(f"{'='*65}")
        if not txns:
            click.echo("  No transactions found.")
            continue
        rows = [
            [
                t["transaction_date"],
                t["name"][:28],
                t["code"],
                f"{t['shares']:,}",
                f"${t['price']:,.2f}" if t["price"] else "—",
                f"${t['value']:,.0f}" if t["value"] else "—",
            ]
            for t in txns
        ]
        click.echo(tabulate(
            rows,
            headers=["Date", "Name", "Type", "Shares", "Price", "Value"],
            tablefmt="simple",
        ))
    click.echo()
