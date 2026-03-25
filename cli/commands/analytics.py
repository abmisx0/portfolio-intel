from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click

from cli.formatters import build_envelope, print_json, print_analytics_table
from config import PORTFOLIOS, LOOKBACK_5Y, BENCHMARK_TICKER
from core.data_fetcher import get_close_series, price_map_freshness
from core.analytics import (
    portfolio_position_metrics,
    portfolio_returns_series,
    theme_attribution,
    compute_metrics,
)
from core.holdings import portfolio_holdings_table

_5Y_START = LOOKBACK_5Y
BENCHMARK = BENCHMARK_TICKER


def run_analytics(portfolio_name: str) -> dict:
    positions = PORTFOLIOS.get(portfolio_name)
    if not positions:
        raise ValueError(f"Portfolio '{portfolio_name}' not found")

    # Fetch all price series
    price_map = {}
    for pos in positions:
        t = pos["ticker"].upper()
        s = get_close_series(t, start=_5Y_START)
        if not s.empty:
            price_map[t] = s

    benchmark = get_close_series(BENCHMARK, start=_5Y_START)

    # Per-position metrics
    position_metrics = portfolio_position_metrics(positions, price_map, benchmark)

    # Theme attribution (trailing 1Y = 252 trading days)
    themes = theme_attribution(positions, price_map, trailing_days=252)

    # Top stock exposures
    top_holdings = portfolio_holdings_table(portfolio_name, top_n=20)

    # Portfolio-level metrics (build weighted return series)
    weights = {pos["ticker"].upper(): pos["weight"] for pos in positions}
    port_series = portfolio_returns_series(price_map, weights)
    if not port_series.empty:
        # Convert daily returns back to price index for metrics
        port_price = (1 + port_series).cumprod()
        port_metrics = compute_metrics(port_price, benchmark=benchmark, label=portfolio_name)
    else:
        port_metrics = {}

    data_freshness = price_map_freshness(price_map)

    return {
        "portfolio": portfolio_name,
        "portfolio_metrics": port_metrics,
        "position_metrics": position_metrics,
        "theme_attribution": themes,
        "top_stock_exposures": top_holdings,
        "data_freshness": data_freshness,
    }


@click.command()
@click.option("--portfolio", default="proposed", show_default=True,
              help="Portfolio name (proposed|previous)")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]), help="Output format")
def analytics_cmd(portfolio: str, fmt: str):
    """Portfolio-level analytics: per-position metrics, theme attribution, top exposures."""
    data = run_analytics(portfolio)
    envelope = build_envelope(
        command="analytics",
        args={"portfolio": portfolio},
        data=data,
        data_freshness=data.get("data_freshness"),
    )
    if fmt == "json":
        print_json(envelope)
    else:
        print_analytics_table(data)
