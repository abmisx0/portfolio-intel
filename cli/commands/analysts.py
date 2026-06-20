from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json
from config import PORTFOLIOS, resolve_portfolio
from core.analysts import get_analyst_data_multi, rec_label


def _consensus_bar(consensus: dict) -> str:
    parts = []
    for short, key in [("SB", "strong_buy"), ("B", "buy"), ("H", "hold"), ("S", "sell"), ("SS", "strong_sell")]:
        n = consensus.get(key, 0)
        if n:
            parts.append(f"{short}:{n}")
    return "  ".join(parts) if parts else "N/A"


def _print_analysts_table(results: list) -> None:
    for r in results:
        ticker  = r.get("ticker", "?")
        price   = r.get("current_price")
        tmean   = r.get("target_mean")
        thigh   = r.get("target_high")
        tlow    = r.get("target_low")
        upside  = r.get("upside_to_mean")
        n       = r.get("analyst_count") or 0
        label   = rec_label(r.get("recommendation", ""), r.get("rec_score"))
        consensus = r.get("consensus", {})
        changes   = r.get("recent_changes", [])

        click.echo(f"\n{'='*62}")
        click.echo(f"  {ticker}  |  {label}  |  {n} analysts")
        click.echo(f"{'='*62}")

        if tmean:
            px_str = f"${price:,.2f}" if price else "N/A"
            up_str = f"{upside*100:+.1f}%" if upside is not None else "N/A"
            click.echo(f"  Current price:   {px_str}")
            click.echo(f"  Target (mean):   ${tmean:,.2f}   upside: {up_str}")
            click.echo(f"  Target (range):  ${tlow:,.2f} – ${thigh:,.2f}")
        else:
            click.echo("  No price target data available.")

        if consensus:
            click.echo(f"\n  Breakdown:  {_consensus_bar(consensus)}")

        if changes:
            click.echo(f"\n  Recent rating changes (last {len(changes)}):")
            rows = [
                [c["date"], c["firm"][:30], c["from_grade"] or "—", "→", c["to_grade"], c["action"]]
                for c in changes
            ]
            click.echo(tabulate(rows, headers=["Date", "Firm", "From", "", "To", "Action"], tablefmt="simple"))

    click.echo()


@click.command()
@click.argument("tickers", nargs=-1, metavar="TICKER")
@click.option("--portfolio", default=None,
              help="Pull analyst data for every ticker in a named portfolio.")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def analysts_cmd(tickers: tuple, portfolio: str | None, fmt: str):
    """Analyst consensus, price targets, and recent rating changes.

    \b
    Examples:
      analysts MU AVGO TSM
      analysts --portfolio core_satellite
      analysts NVDA --format json
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

    click.echo(f"  Fetching analyst data for {len(ticker_list)} ticker(s)…", err=True)
    results = get_analyst_data_multi(ticker_list)

    envelope = build_envelope(
        command="analysts",
        args={"tickers": ticker_list, "portfolio": portfolio},
        data={"results": list(results.values())},
        data_freshness=None,
    )

    if fmt == "json":
        print_json(envelope)
    else:
        _print_analysts_table(list(results.values()))
