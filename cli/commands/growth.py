from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json, fmt_pct, fmt_mult
from config import PORTFOLIOS, resolve_portfolio
from core.growth import get_growth_multi


def _g(v) -> str:
    """Growth fraction → signed percent ('+13.8%')."""
    return fmt_pct(v, decimals=1, signed=True)


def _print_growth_table(results: list) -> None:
    failed = [r for r in results if r.get("error")]
    stocks = [r for r in results if not r.get("error")
              and r.get("quote_type") not in ("ETF", "MUTUALFUND")]
    funds = [r for r in results if not r.get("error")
             and r.get("quote_type") in ("ETF", "MUTUALFUND")]

    if stocks:
        click.echo("\n  Stocks — consensus forward growth (fiscal years; LTG = long-term growth)")
        rows = [
            [r["ticker"], _g(r.get("rev_growth_0y")), _g(r.get("rev_growth_1y")),
             _g(r.get("eps_growth_0y")), _g(r.get("eps_growth_1y")),
             _g(r.get("ltg")), fmt_mult(r.get("forward_pe")),
             f"{r['peg']:.2f}" if r.get("peg") is not None else "—",
             _g(r.get("profit_margin")),
             int(r["analysts"]) if r.get("analysts") else "—"]
            for r in stocks
        ]
        click.echo(tabulate(
            rows,
            headers=["Ticker", "Rev FY0", "Rev FY1", "EPS FY0", "EPS FY1",
                     "LTG", "Fwd P/E", "PEG", "Margin", "#An"],
            tablefmt="simple",
        ))
        idx = next((r.get("index_ltg") for r in stocks if r.get("index_ltg")), None)
        if idx is not None:
            click.echo(f"\n  S&P 500 LTG baseline: {_g(idx)} — a PEG near 1 is cheap "
                       "for its growth; above ~2.5 the growth is fully paid for.")
        click.echo("  PEG = Fwd P/E ÷ LTG; when the LTG column is '—' it falls "
                   "back to EPS FY1 growth (JSON field `peg_basis`).")

    if funds:
        click.echo("\n  Funds — no consensus estimates exist for ETFs; use `valuation` "
                   "for fund P/E and run `growth` on their top holdings (see `screen`).")
        click.echo("  " + ", ".join(r["ticker"] for r in funds))

    if failed:
        click.echo("\n  Skipped (fetch failed, not cached — retry later): "
                   + ", ".join(r["ticker"] for r in failed))

    click.echo()


@click.command()
@click.argument("tickers", nargs=-1, metavar="TICKER")
@click.option("--portfolio", default=None,
              help="Pull growth estimates for every ticker in a named portfolio (or 'live').")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def growth_cmd(tickers: tuple, portfolio: str | None, fmt: str):
    """Consensus forward revenue/EPS growth estimates and PEG per ticker.

    \b
    Examples:
      growth NFLX LLY NVDA
      growth --portfolio live
      growth NVDA --format json
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

    click.echo(f"  Fetching growth estimates for {len(ticker_list)} ticker(s)…", err=True)
    results = get_growth_multi(ticker_list)

    envelope = build_envelope(
        command="growth",
        args={"tickers": ticker_list, "portfolio": portfolio},
        data={"estimates": list(results.values())},
        data_freshness=None,
    )

    if fmt == "json":
        print_json(envelope)
    else:
        _print_growth_table(list(results.values()))
