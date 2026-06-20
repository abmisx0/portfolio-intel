from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json
from core.finnhub import get_earnings_surprises, get_earnings_estimates


def _surprise_arrow(pct: float | None) -> str:
    if pct is None:
        return ""
    return "▲" if pct > 0 else "▼"


@click.command()
@click.argument("tickers", nargs=-1, metavar="TICKER")
@click.option("--forward", is_flag=True, default=False,
              help="Show forward EPS estimates instead of historical surprises.")
@click.option("--quarters", default=8, show_default=True, type=int,
              help="Number of quarters of history to show.")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def earnings_cmd(tickers: tuple, forward: bool, quarters: int, fmt: str):
    """EPS surprises and forward analyst estimates.

    \b
    Examples:
      earnings NVDA MU
      earnings AAPL --forward
      earnings NVDA --quarters 4
      earnings TSLA --format json
    """
    if not tickers:
        raise click.UsageError("Provide one or more TICKER arguments.")

    results = {}
    for ticker in tickers:
        t = ticker.upper()
        if forward:
            entry: dict = {}
            for freq in ("quarterly", "annual"):
                try:
                    entry[freq] = get_earnings_estimates(t, freq=freq)
                except PermissionError:
                    entry[freq] = []
                    entry["note"] = "forward estimates require Finnhub paid plan"
            results[t] = entry
        else:
            results[t] = get_earnings_surprises(t, quarters=quarters)

    envelope = build_envelope(
        command="earnings",
        args={"tickers": [t.upper() for t in tickers], "forward": forward, "quarters": quarters},
        data=results,
        data_freshness=None,
    )

    if fmt == "json":
        print_json(envelope)
        return

    for ticker, data in results.items():
        click.echo(f"\n{'='*65}")
        click.echo(f"  EARNINGS — {ticker}")
        click.echo(f"{'='*65}")

        if forward:
            q_items = data.get("quarterly") or []
            a_items = data.get("annual") or []

            if q_items:
                click.echo("\n  Forward EPS Estimates (Quarterly):")
                rows = [
                    [e["period"], f"{e['eps_avg']:+.2f}" if e.get("eps_avg") is not None else "—",
                     f"{e['eps_high']:+.2f}" if e.get("eps_high") is not None else "—",
                     f"{e['eps_low']:+.2f}" if e.get("eps_low") is not None else "—",
                     e.get("analysts") or "—"]
                    for e in q_items
                ]
                click.echo(tabulate(rows, headers=["Period", "Mean EPS", "High", "Low", "Analysts"], tablefmt="simple"))

            if a_items:
                click.echo("\n  Forward EPS Estimates (Annual):")
                rows = [
                    [e["period"], f"{e['eps_avg']:+.2f}" if e.get("eps_avg") is not None else "—",
                     f"{e['eps_high']:+.2f}" if e.get("eps_high") is not None else "—",
                     f"{e['eps_low']:+.2f}" if e.get("eps_low") is not None else "—",
                     e.get("analysts") or "—"]
                    for e in a_items
                ]
                click.echo(tabulate(rows, headers=["Period", "Mean EPS", "High", "Low", "Analysts"], tablefmt="simple"))

            if not q_items and not a_items:
                click.echo("  No forward estimate data available.")
        else:
            if not data:
                click.echo("  No earnings history available.")
                continue
            rows = [
                [e["period"],
                 f"{e['estimate']:+.2f}" if e.get("estimate") is not None else "—",
                 f"{e['actual']:+.2f}" if e.get("actual") is not None else "—",
                 f"{_surprise_arrow(e.get('surprise_pct'))} {e['surprise_pct']:+.1f}%"
                 if e.get("surprise_pct") is not None else "—"]
                for e in data
            ]
            click.echo(tabulate(rows, headers=["Period", "Estimate", "Actual", "Surprise %"], tablefmt="simple"))

    click.echo()
