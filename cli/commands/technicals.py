from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json
from config import PORTFOLIOS, resolve_portfolio
from core.technicals import get_technicals_multi


def _n(v, fmt="{:.2f}") -> str:
    return fmt.format(v) if v is not None else "—"


def _levels(levels: list) -> str:
    return " / ".join(f"{v:g}" for v in levels) if levels else "—"


@click.command()
@click.argument("tickers", nargs=-1, metavar="TICKER")
@click.option("--portfolio", default=None,
              help="Compute technicals for every ticker in a named portfolio (or 'live').")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def technicals_cmd(tickers: tuple, portfolio: str | None, fmt: str):
    """Price technicals: SMA50/200, RSI(14), 52-week range, swing support/resistance.

    \b
    Examples:
      technicals COIN XLV AAPL
      technicals --portfolio live
      technicals SHLD --format json
    """
    if not tickers and not portfolio:
        raise click.UsageError("Provide one or more TICKER arguments or --portfolio.")

    if portfolio:
        try:
            positions = resolve_portfolio(portfolio)
        except ValueError:
            raise click.BadParameter(
                f"Portfolio '{portfolio}' not found. Valid: {list(PORTFOLIOS.keys())} (or 'live')",
                param_hint="--portfolio",
            )
        ticker_list = [p["ticker"].upper() for p in positions]
    else:
        ticker_list = [t.upper() for t in tickers]

    results = get_technicals_multi(ticker_list)

    envelope = build_envelope(
        command="technicals",
        args={"tickers": ticker_list, "portfolio": portfolio},
        data={"results": list(results.values())},
        data_freshness=max((r.get("as_of", "") for r in results.values()), default=None) or None,
    )

    if fmt == "json":
        print_json(envelope)
        return

    rows = []
    for r in results.values():
        if r.get("error"):
            rows.append([r["ticker"], r["error"]] + ["—"] * 7)
            continue
        rows.append([
            r["ticker"],
            _n(r["price"]),
            _n(r["sma50"]),
            _n(r["sma200"]),
            _n(r["rsi14"], "{:.0f}"),
            f"{_n(r['low_52w'])}–{_n(r['high_52w'])}",
            _n((r["pct_from_52w_high"] or 0) * 100, "{:+.1f}%") if r.get("pct_from_52w_high") is not None else "—",
            _levels(r["support"]),
            _levels(r["resistance"]),
        ])

    click.echo(tabulate(
        rows,
        headers=["Ticker", "Price", "SMA50", "SMA200", "RSI", "52w Range",
                 "vs 52wH", "Support", "Resistance"],
        tablefmt="simple",
    ))
    click.echo("  Support/resistance = 5-day swing pivots over trailing 6 months — zones, not lines.")
