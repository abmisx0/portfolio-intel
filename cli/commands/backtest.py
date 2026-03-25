from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click

from cli.formatters import build_envelope, print_json, print_backtest_table
from core.backtester import backtest


@click.command()
@click.option("--a", "portfolio_a", default="proposed", show_default=True,
              help="First portfolio name (proposed|previous)")
@click.option("--b", "portfolio_b", default="previous", show_default=True,
              help="Second portfolio name (proposed|previous)")
@click.option("--start", default="2020-01-01", show_default=True,
              help="Backtest start date (YYYY-MM-DD)")
@click.option("--end", default=None,
              help="Backtest end date (YYYY-MM-DD, default: today)")
@click.option("--no-benchmark", is_flag=True, default=False,
              help="Exclude benchmark")
@click.option("--benchmark", "benchmark", default="voo", show_default=True,
              type=click.Choice(["voo", "spx"], case_sensitive=False),
              help="Benchmark: voo (ETF, includes fees) or spx (pure S&P 500 index)")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]), help="Output format")
def backtest_cmd(
    portfolio_a: str,
    portfolio_b: str,
    start: str,
    end: str | None,
    no_benchmark: bool,
    benchmark: str,
    fmt: str,
):
    """Compare two portfolio allocations over a historical period."""
    data = backtest(
        portfolio_a=portfolio_a,
        portfolio_b=portfolio_b,
        start=start,
        end=end,
        include_benchmark=not no_benchmark,
        benchmark=benchmark,
    )
    envelope = build_envelope(
        command="backtest",
        args={
            "portfolio_a": portfolio_a,
            "portfolio_b": portfolio_b,
            "start": start,
            "end": end,
        },
        data=data,
        data_freshness=data.get("actual_end"),
    )
    if fmt == "json":
        print_json(envelope)
    else:
        print_backtest_table(data)
