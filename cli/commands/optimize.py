from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import date

import click

from cli.formatters import build_envelope, print_json, print_optimize_table
from config import PORTFOLIOS
from core.optimizer import optimize, OBJECTIVES


@click.command()
@click.argument("tickers", nargs=-1, metavar="TICKER")
@click.option("--portfolio", default=None,
              help="Portfolio name from config (proposed|previous|v3). "
                   "Provides tickers and current weights for comparison.")
@click.option(
    "--objective", default="sharpe", show_default=True,
    type=click.Choice(sorted(OBJECTIVES), case_sensitive=False),
    help="Optimization objective.",
)
@click.option("--start", default=None,
              help="History start date YYYY-MM-DD (default: 3 years ago).")
@click.option("--end", default=None,
              help="History end date YYYY-MM-DD (default: today).")
@click.option("--confidence", default=0.95, show_default=True, type=float,
              help="CVaR tail probability (e.g. 0.95 = worst 5% of days).")
@click.option("--risk-aversion", default=3.0, show_default=True, type=float,
              help="λ for quadratic-utility objective.")
@click.option("--max-weight", "max_weight_args", multiple=True, metavar="[TICKER:]FLOAT",
              help="Global or per-ticker cap. Repeat for per-ticker: "
                   "--max-weight 0.40 or --max-weight PPA:0.20 --max-weight NLR:0.15")
@click.option("--min-weight", "min_weight_args", multiple=True, metavar="[TICKER:]FLOAT",
              help="Global or per-ticker floor. Same syntax as --max-weight.")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]), help="Output format.")
def optimize_cmd(
    portfolio: str | None,
    tickers: tuple,
    objective: str,
    start: str | None,
    end: str | None,
    confidence: float,
    risk_aversion: float,
    max_weight_args: tuple,
    min_weight_args: tuple,
    fmt: str,
):
    """Find optimal portfolio weights for a given objective.

    \b
    Examples:
      optimize --portfolio v4 --objective sharpe
      optimize VOO VGT SMH URA ITA VDE QTUM --objective sortino
      optimize --portfolio v4 --objective min-cvar --confidence 0.95
      optimize --portfolio v4 --objective sharpe --max-weight 0.30
      optimize --portfolio v5 --objective sharpe --max-weight PPA:0.20 --max-weight NLR:0.15
    """
    if not portfolio and not tickers:
        raise click.UsageError("Provide --portfolio or one or more TICKER arguments.")
    if portfolio and tickers:
        raise click.UsageError("Provide --portfolio or TICKER arguments, not both.")

    # Resolve tickers and current weights
    if portfolio:
        positions = PORTFOLIOS.get(portfolio)
        if not positions:
            raise click.BadParameter(
                f"Portfolio '{portfolio}' not found. "
                f"Valid names: {list(PORTFOLIOS.keys())}",
                param_hint="--portfolio",
            )
        ticker_list = [p["ticker"] for p in positions]
        current_weights = {p["ticker"].upper(): p["weight"] for p in positions}
        portfolio_label = portfolio
    else:
        ticker_list = list(tickers)
        current_weights = None
        portfolio_label = None

    start_date = date.fromisoformat(start) if start else None
    end_date = date.fromisoformat(end) if end else None

    def _parse_weight_args(args: tuple, default: float) -> tuple[float, dict]:
        """Parse e.g. ('0.40', 'PPA:0.20', 'NLR:0.15') → (0.40, {'PPA': 0.20, 'NLR': 0.15})."""
        global_val = default
        per_ticker: dict = {}
        for v in args:
            if ":" in v:
                ticker, weight = v.split(":", 1)
                per_ticker[ticker.upper()] = float(weight)
            else:
                global_val = float(v)
        return global_val, per_ticker

    max_weight, per_max = _parse_weight_args(max_weight_args, 1.0)
    min_weight, per_min = _parse_weight_args(min_weight_args, 0.0)

    data = optimize(
        tickers=ticker_list,
        objective=objective,
        start=start_date,
        end=end_date,
        current_weights=current_weights,
        confidence=confidence,
        risk_aversion=risk_aversion,
        min_weight=min_weight,
        max_weight=max_weight,
        per_min=per_min or None,
        per_max=per_max or None,
    )
    data["portfolio_label"] = portfolio_label

    envelope = build_envelope(
        command="optimize",
        args={
            "portfolio": portfolio_label,
            "tickers": ticker_list,
            "objective": objective,
            "start": start,
            "end": end,
        },
        data=data,
        data_freshness=data["period"]["end"],
    )

    if fmt == "json":
        print_json(envelope)
    else:
        print_optimize_table(data)
