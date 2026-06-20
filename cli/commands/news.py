from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json
from core.finnhub import get_news_sentiment, get_company_news


def _sentiment_bar(bullish: float | None, bearish: float | None) -> str:
    if bullish is None:
        return "N/A"
    bull_pct = round(bullish * 100)
    bear_pct = round(bearish * 100) if bearish else 0
    return f"Bullish {bull_pct}%  /  Bearish {bear_pct}%"


@click.command()
@click.argument("tickers", nargs=-1, metavar="TICKER")
@click.option("--days", default=7, show_default=True, type=int,
              help="Number of days of news to fetch.")
@click.option("--headlines-only", is_flag=True, default=False,
              help="Skip sentiment summary, show headlines only.")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def news_cmd(tickers: tuple, days: int, headlines_only: bool, fmt: str):
    """Company news with aggregate sentiment scoring.

    \b
    Examples:
      news NVDA MU
      news AAPL --days 14
      news SMH --headlines-only
      news NLR --format json
    """
    if not tickers:
        raise click.UsageError("Provide one or more TICKER arguments.")

    results = {}
    for ticker in tickers:
        t = ticker.upper()
        entry: dict = {"ticker": t}
        if not headlines_only:
            try:
                entry["sentiment"] = get_news_sentiment(t)
            except PermissionError:
                entry["sentiment"] = None
                entry["sentiment_note"] = "sentiment requires Finnhub paid plan"
        entry["articles"] = get_company_news(t, days=days)
        results[t] = entry

    envelope = build_envelope(
        command="news",
        args={"tickers": [ticker.upper() for ticker in tickers], "days": days},
        data=results,
        data_freshness=None,
    )

    if fmt == "json":
        print_json(envelope)
        return

    for ticker, entry in results.items():
        click.echo(f"\n{'='*65}")
        click.echo(f"  NEWS & SENTIMENT — {ticker}")
        click.echo(f"{'='*65}")

        sent = entry.get("sentiment")
        if sent:
            click.echo(f"  {_sentiment_bar(sent.get('bullish_pct'), sent.get('bearish_pct'))}")
            if sent.get("score") is not None:
                click.echo(f"  News score: {sent['score']:.3f}  "
                           f"(sector avg: {sent.get('sector_score', 0):.3f})")
            if sent.get("articles_week") is not None:
                click.echo(f"  Articles (7d): {sent['articles_week']}")

        articles = entry.get("articles", [])
        if articles:
            click.echo(f"\n  Headlines (last {days}d):")
            rows = [
                [a["datetime"], a["source"][:20], a["headline"][:70]]
                for a in articles
            ]
            click.echo(tabulate(rows, headers=["Date", "Source", "Headline"], tablefmt="simple"))
        else:
            click.echo(f"\n  No articles found in the last {days} days.")

    click.echo()
