from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json
import core.watchlist as wl
from core.screener import screen


def _p(v) -> str:
    return f"{v*100:+.2f}%" if v is not None else "N/A"

def _f(v) -> str:
    return f"{v:.4f}" if v is not None else "N/A"


@click.group()
def watchlist_cmd():
    """Manage your ETF watchlist."""
    pass


@watchlist_cmd.command("add")
@click.argument("ticker")
@click.option("--notes", default="", help="Optional notes about this candidate")
def wl_add(ticker: str, notes: str):
    """Add a ticker to the watchlist."""
    ok = wl.add(ticker.upper(), notes)
    if ok:
        click.echo(f"Added {ticker.upper()} to watchlist.")
    else:
        click.echo(f"{ticker.upper()} is already on the watchlist.")


@watchlist_cmd.command("remove")
@click.argument("ticker")
def wl_remove(ticker: str):
    """Remove a ticker from the watchlist."""
    ok = wl.remove(ticker.upper())
    if ok:
        click.echo(f"Removed {ticker.upper()} from watchlist.")
    else:
        click.echo(f"{ticker.upper()} was not on the watchlist.")


@watchlist_cmd.command("list")
@click.option("--format", "fmt", default="table", type=click.Choice(["json", "table"]))
def wl_list(fmt: str):
    """List all tickers on the watchlist."""
    items = wl.list_all()
    if fmt == "json":
        envelope = build_envelope("watchlist list", {}, {"watchlist": items})
        print_json(envelope)
    else:
        if not items:
            click.echo("Watchlist is empty. Use: python -m cli watchlist add TICKER")
            return
        rows = [[i["ticker"], i["added_date"], i["notes"] or "—"] for i in items]
        click.echo(tabulate(rows, headers=["Ticker", "Added", "Notes"], tablefmt="simple"))


@watchlist_cmd.command("screen")
@click.option("--portfolio", default="proposed", show_default=True)
@click.option("--format", "fmt", default="table", type=click.Choice(["json", "table"]))
def wl_screen(portfolio: str, fmt: str):
    """Run the screener on every ticker in the watchlist."""
    items = wl.list_all()
    if not items:
        click.echo("Watchlist is empty.")
        return

    results = []
    for item in items:
        ticker = item["ticker"]
        try:
            data = screen(ticker, portfolio_name=portfolio)
            results.append({
                "ticker": ticker,
                "notes": item["notes"],
                "overlap_coefficient": data["overlap"]["overlap_coefficient"],
                "sharpe_ratio": data["risk_metrics"].get("sharpe_ratio"),
                "annualized_return": data["risk_metrics"].get("annualized_return"),
                "max_drawdown": data["risk_metrics"].get("max_drawdown"),
                "trailing_1y": data["trailing_returns"].get("1Y"),
                "data_freshness": data.get("data_freshness"),
            })
        except Exception as exc:
            results.append({"ticker": ticker, "error": str(exc)})

    if fmt == "json":
        envelope = build_envelope("watchlist screen", {"portfolio": portfolio}, {"results": results})
        print_json(envelope)
    else:
        rows = []
        for r in results:
            if "error" in r:
                rows.append([r["ticker"], "ERROR", r["error"], "", "", ""])
            else:
                rows.append([
                    r["ticker"],
                    r.get("notes") or "—",
                    f"{r['overlap_coefficient']*100:.1f}%",
                    _p(r.get("trailing_1y")),
                    _f(r.get("sharpe_ratio")),
                    _p(r.get("max_drawdown")),
                ])
        click.echo(tabulate(
            rows,
            headers=["Ticker", "Notes", "Overlap", "1Y Return", "Sharpe", "Max DD"],
            tablefmt="simple",
        ))
