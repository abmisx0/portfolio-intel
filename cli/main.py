"""
Portfolio Intelligence CLI entry point.

Usage:
  python -m cli screen QQQ --portfolio core_satellite --format json
  python -m cli compare QQQ SPY --format table
  python -m cli holdings --portfolio thematic --top 20 --format table
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click

from cli.commands.screen import screen_cmd
from cli.commands.compare import compare_cmd
from cli.commands.holdings import holdings_cmd
from cli.commands.backtest import backtest_cmd
from cli.commands.analytics import analytics_cmd
from cli.commands.correlation import correlation_cmd
from cli.commands.watchlist import watchlist_cmd
from cli.commands.rebalance import rebalance_cmd
from cli.commands.alerts import alerts_cmd
from cli.commands.optimize import optimize_cmd
from cli.commands.positions import positions_cmd
from cli.commands.performance import performance_cmd
from cli.commands.exposure import exposure_cmd
from cli.commands.advise import advise_cmd
from cli.commands.analysts import analysts_cmd
from cli.commands.valuation import valuation_cmd
from cli.commands.technicals import technicals_cmd
from cli.commands.macro import macro_cmd
from cli.commands.publish import publish_cmd
from cli.commands.insider import insider_cmd
from cli.commands.news import news_cmd
from cli.commands.earnings import earnings_cmd
from cli.commands.growth import growth_cmd


@click.group()
def cli():
    """Portfolio Intelligence — ETF research and analytics tool."""
    pass


cli.add_command(screen_cmd, name="screen")
cli.add_command(compare_cmd, name="compare")
cli.add_command(holdings_cmd, name="holdings")
cli.add_command(backtest_cmd, name="backtest")
cli.add_command(analytics_cmd, name="analytics")
cli.add_command(correlation_cmd, name="correlation")
cli.add_command(watchlist_cmd, name="watchlist")
cli.add_command(rebalance_cmd, name="rebalance")
cli.add_command(alerts_cmd, name="alerts")
cli.add_command(optimize_cmd, name="optimize")
cli.add_command(positions_cmd, name="positions")
cli.add_command(performance_cmd, name="performance")
cli.add_command(exposure_cmd, name="exposure")
cli.add_command(advise_cmd, name="advise")
cli.add_command(analysts_cmd, name="analysts")
cli.add_command(valuation_cmd, name="valuation")
cli.add_command(technicals_cmd, name="technicals")
cli.add_command(macro_cmd, name="macro")
cli.add_command(publish_cmd, name="publish")
cli.add_command(insider_cmd, name="insider")
cli.add_command(news_cmd, name="news")
cli.add_command(earnings_cmd, name="earnings")
cli.add_command(growth_cmd, name="growth")


@cli.command()
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8000, show_default=True, type=int)
def start(host: str, port: int):
    """Start the web dashboard."""
    import uvicorn
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    cli()
