from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json
from config import PORTFOLIOS, LOOKBACK_5Y
from core.data_fetcher import get_close_series
from core.analytics import correlation_matrix
from core.holdings import portfolio_holdings_table
from core.alerts import run_portfolio_alerts, CORR_HIGH, CONCENTRATION

_5Y_START = LOOKBACK_5Y


@click.command()
@click.option("--portfolio", default="proposed", show_default=True)
@click.option("--corr-threshold", default=CORR_HIGH, show_default=True, type=float,
              help="Correlation threshold for HIGH_CORRELATION alerts")
@click.option("--conc-threshold", default=CONCENTRATION, show_default=True, type=float,
              help="Single-stock concentration threshold (e.g. 0.05 = 5%)")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def alerts_cmd(portfolio: str, corr_threshold: float, conc_threshold: float, fmt: str):
    """Check portfolio for high correlation, concentration, and theme overlap alerts."""
    positions = PORTFOLIOS.get(portfolio, [])

    price_map = {}
    for pos in positions:
        t = pos["ticker"].upper()
        s = get_close_series(t, start=_5Y_START)
        if not s.empty:
            price_map[t] = s

    cm = correlation_matrix(price_map)
    top_holdings = portfolio_holdings_table(portfolio, top_n=30)

    result = run_portfolio_alerts(
        positions, cm, top_holdings, corr_threshold, conc_threshold
    )

    envelope = build_envelope(
        command="alerts",
        args={"portfolio": portfolio, "corr_threshold": corr_threshold, "conc_threshold": conc_threshold},
        data=result,
    )

    if fmt == "json":
        print_json(envelope)
        return

    total = result["total"]
    click.echo(f"\n{'='*60}")
    click.echo(f"  ALERTS: portfolio={portfolio}  ({total} total)")
    click.echo(f"{'='*60}")

    def _print_group(level: str, emoji: str):
        items = result[level]
        if not items:
            return
        click.echo(f"\n  {emoji} {level.upper()} ({len(items)})")
        for a in items:
            click.echo(f"  • [{a['type']}] {a['message']}")

    _print_group("critical", "🔴")
    _print_group("warning",  "🟡")
    _print_group("info",     "🔵")

    if total == 0:
        click.echo("\n  ✅ No alerts. Portfolio looks clean.")
    click.echo()
