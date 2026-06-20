from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json
from config import BENCHMARKS
from core.broker import login
from core.performance import performance


@click.command()
@click.option("--benchmark", "benchmarks", multiple=True,
              type=click.Choice(list(BENCHMARKS), case_sensitive=False),
              help="Index benchmark(s) to compare against. Repeatable. "
                   "Default: voo, nasdaq, russell.")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def performance_cmd(benchmarks: tuple[str, ...], fmt: str):
    """
    Money-weighted (XIRR) return of your live equity book vs index benchmarks.

    Uses actual Robinhood order history: every filled buy/sell becomes a dated
    cash flow. Your return is the IRR that discounts those flows plus today's
    market value to zero. Each benchmark clones the *identical* cash flows into
    the index (same dollars, same dates) so the only difference is asset choice.
    """
    bms = tuple(b.lower() for b in benchmarks) or ("voo", "nasdaq", "russell")
    try:
        login()
        data = performance(benchmarks=bms)
    except Exception as e:
        click.echo(f"  Robinhood error: {e}", err=True)
        sys.exit(1)

    if fmt == "json":
        print_json(build_envelope(
            command="performance",
            args={"benchmarks": list(bms)},
            data=data,
            data_freshness=data.get("end_date"),
        ))
        return

    def _pct(v):
        return f"{v*100:+.2f}%" if v is not None else "—"

    cov = data["coverage_ratio"]
    click.echo(f"\n{'='*70}")
    click.echo(f"  PERFORMANCE (money-weighted)  {data['start_date']} → {data['end_date']}")
    click.echo(f"{'='*70}")
    click.echo(f"  ⚠ Order history covers {cov*100:.0f}% of your book "
               f"(${data['covered_value']:,.0f} of ${data['book_value']:,.0f}).")
    click.echo(f"    Everything below is the COVERED SLEEVE only — see caveats.\n")
    click.echo(f"  Filled orders        : {data['n_flows']}")
    click.echo(f"  Total bought         : ${data['total_bought']:,.0f}")
    click.echo(f"  Total sold           : ${data['total_sold']:,.0f}")
    click.echo(f"  Net invested         : ${data['net_invested']:,.0f}")
    click.echo(f"  Covered value today  : ${data['covered_value']:,.0f}")
    click.echo(f"  Covered gain         : ${data['total_gain']:,.0f}")
    click.echo(f"  Your return (XIRR)   : {_pct(data['user_xirr'])} / yr")

    rows = []
    for bm in data["benchmarks"]:
        rows.append([
            f"{bm['label']} ({bm['ticker']})",
            _pct(bm["xirr"]),
            _pct(bm["xirr_diff"]),
            f"${bm['terminal_value']:,.0f}",
            f"${bm['value_diff']:+,.0f}",
        ])
    if rows:
        click.echo(f"\n  Covered-sleeve contributions, cloned into each index:")
        click.echo(tabulate(
            rows,
            headers=["Benchmark", "Index XIRR", "You − Index", "Index Value", "Your Δ$"],
            tablefmt="simple",
        ))

    if data["uncovered_positions"]:
        unc = data["uncovered_positions"]
        click.echo(f"\n  Excluded (no order history — transferred in / pre-window):")
        click.echo("    " + ", ".join(r["ticker"] for r in unc))

    click.echo(f"\n  Caveats:")
    for c in data["caveats"]:
        click.echo(f"    • {c}")
    click.echo()
