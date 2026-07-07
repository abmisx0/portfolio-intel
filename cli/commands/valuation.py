from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json, fmt_pct, fmt_mult
from config import PORTFOLIOS, resolve_portfolio
from core.valuation import get_valuation_multi


def _cap(v) -> str:
    if v is None:
        return "—"
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    return f"${v / 1e6:.0f}M"


def _print_valuation_table(results: list) -> None:
    stocks = [r for r in results if r.get("quote_type") not in ("ETF", "MUTUALFUND")]
    funds = [r for r in results if r.get("quote_type") in ("ETF", "MUTUALFUND")]

    if stocks:
        click.echo("\n  Stocks — valuation multiples")
        rows = [
            [r["ticker"], fmt_mult(r.get("trailing_pe")), fmt_mult(r.get("forward_pe")),
             fmt_mult(r.get("price_to_sales")), fmt_mult(r.get("price_to_book")),
             fmt_mult(r.get("ev_to_ebitda")), fmt_pct(r.get("profit_margin")),
             fmt_pct(r.get("dividend_yield")), _cap(r.get("market_cap"))]
            for r in stocks
        ]
        click.echo(tabulate(
            rows,
            headers=["Ticker", "P/E", "Fwd P/E", "P/S", "P/B", "EV/EBITDA",
                     "Margin", "Div Yld", "Mkt Cap"],
            tablefmt="simple",
        ))

    if funds:
        click.echo("\n  Funds — portfolio-level multiples")
        rows = [
            [r["ticker"], fmt_mult(r.get("fund_pe")), fmt_mult(r.get("fund_pb")),
             fmt_mult(r.get("fund_ps")), fmt_pct(r.get("expense_ratio")),
             fmt_pct(r.get("dividend_yield")), _cap(r.get("aum"))]
            for r in funds
        ]
        click.echo(tabulate(
            rows,
            headers=["Ticker", "Fund P/E", "Fund P/B", "Fund P/S",
                     "Expense", "Div Yld", "AUM"],
            tablefmt="simple",
        ))

    click.echo()


@click.command()
@click.argument("tickers", nargs=-1, metavar="TICKER")
@click.option("--portfolio", default=None,
              help="Pull valuation data for every ticker in a named portfolio (or 'live').")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def valuation_cmd(tickers: tuple, portfolio: str | None, fmt: str):
    """Valuation multiples: P/E, P/S, P/B, EV/EBITDA for stocks; fund P/E and
    expense ratio for ETFs.

    \b
    Examples:
      valuation PFE LLY ABBV
      valuation --portfolio live
      valuation SMH NVDA --format json
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

    click.echo(f"  Fetching valuation data for {len(ticker_list)} ticker(s)…", err=True)
    results = get_valuation_multi(ticker_list)

    envelope = build_envelope(
        command="valuation",
        args={"tickers": ticker_list, "portfolio": portfolio},
        data={"results": list(results.values())},
        data_freshness=None,
    )

    if fmt == "json":
        print_json(envelope)
    else:
        _print_valuation_table(list(results.values()))
