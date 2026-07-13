from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json
from core.broker import login
from core.realized import compute_realized


def _d(v: float) -> str:
    return f"${v:+,.0f}"


def _print_realized_table(r: dict) -> None:
    t = r["totals"]
    click.echo(f"\n  REALIZED GAINS — {r['year']}  (read-only reconstruction; "
               "see caveats)")
    click.echo(tabulate([
        ["Short-term capital gains (stocks + options)", _d(t["short_term"])],
        ["Long-term capital gains", _d(t["long_term"])],
        ["Net realized capital gains", _d(t["net_realized"])],
        ["  (assigned-option premium folded into stock legs)", _d(r["folded_premium"])],
        ["Dividends", _d(r["income"]["dividends"])],
        ["Cash interest", _d(r["income"]["interest"])],
        ["Margin interest paid", _d(-r["income"]["margin_interest"])],
        ["Net investment income", _d(t["net_income"])],
    ], tablefmt="simple"))

    if r["stocks"]["sales"]:
        click.echo("\n  Stock sales (incl. assignments)")
        rows = [[s["date"], s["ticker"], s["source"], f"{s['shares']:.0f}",
                 f"${s['price']:,.2f}", _d(s["short_term_pl"]), _d(s["long_term_pl"]),
                 f"{s['uncovered_shares']:.0f}" if s["uncovered_shares"] else ""]
                for s in r["stocks"]["sales"]]
        click.echo(tabulate(rows, headers=["Date", "Tkr", "Via", "Sh", "Price",
                                           "ST P/L", "LT P/L", "Sh w/o basis"],
                            tablefmt="simple"))

    if r["options"]["closed"]:
        click.echo("\n  Option P/L realized this year (by contract close)")
        rows = [[c["closed_on"], c["ticker"],
                 f"{c['option_type']} {c['strike']:g} {c['expiration']}",
                 c["how"], _d(c["premium_pl"])]
                for c in r["options"]["closed"]]
        click.echo(tabulate(rows, headers=["Closed", "Tkr", "Contract", "How",
                                           "Premium P/L"], tablefmt="simple"))
    click.echo(f"\n  Open option positions carry {_d(r['options']['open_unrealized_premium'])} "
               "of collected-but-UNREALIZED premium (not in totals).")

    click.echo("\n  Caveats:")
    for c in r["caveats"]:
        click.echo(f"    • {c}")
    click.echo()


@click.command()
@click.option("--year", default=None, type=click.IntRange(2000, 2100),
              help="Tax year (default: current year).")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def realized_cmd(year: int | None, fmt: str):
    """Realized capital gains, income, and margin cost for a tax year.

    Reconstructed read-only from Robinhood order history, option events
    (assignments/expirations), dividends, and interest records.

    \b
    Examples:
      realized
      realized --year 2025
      realized --format json
    """
    click.echo("  Logging into Robinhood and fetching history…", err=True)
    login()
    result = compute_realized(year)

    envelope = build_envelope(
        command="realized",
        args={"year": result["year"]},
        data=result,
        data_freshness=None,
    )
    if fmt == "json":
        print_json(envelope)
    else:
        _print_realized_table(result)
