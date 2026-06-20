from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click

from cli.formatters import build_envelope, print_json, print_analytics_table
from config import LOOKBACK_ALL, BENCHMARK_TICKER, resolve_portfolio
from core.data_fetcher import get_close_series, price_map_freshness
from core.analytics import (
    portfolio_position_metrics,
    portfolio_returns_series,
    theme_attribution,
    compute_metrics,
)
from core.holdings import portfolio_holdings_table

_5Y_START = LOOKBACK_ALL  # fetch full ~10Y history so 1Y/3Y/5Y/10Y windows all resolve
BENCHMARK = BENCHMARK_TICKER


def run_analytics(portfolio_name: str, delta_adjusted: bool = False) -> dict:
    if delta_adjusted:
        from core.exposure import delta_adjusted_positions
        positions = delta_adjusted_positions()
    else:
        try:
            positions = resolve_portfolio(portfolio_name)
        except ValueError as e:
            raise click.UsageError(str(e))

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

    # Top stock exposures (reuse resolved positions to avoid a second live fetch)
    top_holdings = portfolio_holdings_table(portfolio_name, top_n=20, portfolio_override=positions)

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
@click.option("--portfolio", default="live", show_default=True,
              help="Portfolio name from config.py, or 'live' for current Robinhood holdings.")
@click.option("--delta-adjusted", "-d", is_flag=True, default=False,
              help="Use delta-adjusted economic weights (folds in option exposure). Live book only.")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]), help="Output format")
def analytics_cmd(portfolio: str, delta_adjusted: bool, fmt: str):
    """Portfolio-level analytics: per-position metrics, theme attribution, top exposures."""
    if delta_adjusted and portfolio != "live":
        raise click.UsageError(
            "--delta-adjusted analyzes the live Robinhood book; "
            "drop --portfolio or pass --portfolio live."
        )
    data = run_analytics(portfolio, delta_adjusted=delta_adjusted)
    envelope = build_envelope(
        command="analytics",
        args={"portfolio": portfolio, "delta_adjusted": delta_adjusted},
        data=data,
        data_freshness=data.get("data_freshness"),
    )
    if fmt == "json":
        print_json(envelope)
    else:
        print_analytics_table(data)
