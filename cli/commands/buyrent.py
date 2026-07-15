from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import datetime

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json, fmt_pct
from core.realestate import (BREAKEVEN_HI, BREAKEVEN_LO, PropertyInputs,
                             buy_vs_rent, mortgage_rate_at)


def _money(v) -> str:
    return f"${v:,.0f}" if v is not None else "—"


def _breakeven_label(be) -> str:
    if be is None:
        return f">{BREAKEVEN_HI * 100:.0f}%"
    if be <= BREAKEVEN_LO + 1e-9:
        return f"≤ {BREAKEVEN_LO * 100:.0f}%"
    return fmt_pct(be)


def _print_table(r: dict) -> None:
    m = r["monthly"]
    click.echo(f"\n  SHOULD I BUY THIS LISTING?  ${r['price']:,.0f}"
               + (f"  |  {r['metro']}" if r["metro"] else ""))
    click.echo(f"  Same unit rents for ${r['rent']:,.0f}/mo  |  vs the same dollars "
               f"invested at {fmt_pct(r['assumed_benchmark_return'])}/yr\n")

    click.echo(tabulate([
        ["Monthly to OWN (P&I + tax/ins/maint/capex/HOA)",
         f"{_money(m['own_total'])}  ({_money(m['mortgage_payment'])} + {_money(m['carry_costs'])})"],
        ["Monthly to RENT the same unit", _money(m["rent"])],
        ["Premium to own", f"{_money(m['premium_to_own'])}/mo"],
        ["Price-to-rent ratio",
         f"{r['price_to_rent']:.1f}x  ({r['price_to_rent_zone']}; <18 buy-leaning, >22 rent-leaning)"],
    ], tablefmt="simple"))

    rows = []
    for s in r["scenarios"]:
        own, alt = s["own_terminal"], s["rent_invest_terminal"]
        winner = "BUY" if (own or 0) > (alt or 0) else "RENT"
        rows.append([f"{s['hold_years']}y", _money(own), _money(alt), winner,
                     _breakeven_label(s["breakeven_appreciation"])])
    click.echo("\n" + tabulate(
        rows, headers=["Hold", "BUY terminal", "RENT+invest terminal",
                       "Winner", "Breakeven appr/yr"], tablefmt="simple"))
    appr = r["scenarios"][0]["assumed_appreciation"]
    source = ("user-supplied" if r["appreciation_overridden"]
              else (f"{r['metro']} 10y trend" if r["metro"] else "Case-Shiller 10y trend"))
    click.echo(f"\n  Assumed appreciation: {fmt_pct(appr)}/yr ({source})"
               f"  |  VERDICT: {r['verdict']} "
               f"({r['votes_buy']}/{r['votes_total']} horizons favor buying)")
    click.echo("\n  This is the financial verdict only — stability, roots, and "
               "renovation freedom are real but priced here at $0. Assumes the "
               "monthly difference actually gets invested. Federal taxes only; "
               "§121 needs 2+ years (default assumes MFJ $500k — use --s121 250 "
               "if single). Not tax or lifestyle advice.\n")


@click.command(name="buyrent")
@click.option("--price", required=True, type=float, help="Listing price ($).")
@click.option("--rent", required=True, type=click.FloatRange(min=1),
              help="Market rent of the SAME unit ($/mo) — check the listing's rent Zestimate.")
@click.option("--metro", default=None,
              help="Zillow metro for the appreciation default, e.g. 'Los Angeles, CA'.")
@click.option("--down", default=20.0, show_default=True, type=float, help="Down payment %.")
@click.option("--rate", default=None, type=float,
              help="Mortgage rate % (default: current 30y PMMS from FRED).")
@click.option("--term", default=30, show_default=True, type=int, help="Mortgage term (years).")
@click.option("--benchmark-return", default=9.5, show_default=True, type=float,
              help="Assumed annual return % on the rent-and-invest alternative.")
@click.option("--appreciation", default=None, type=float,
              help="Annual appreciation % (default: metro 10y CAGR, else Case-Shiller).")
@click.option("--rent-growth", default=None, type=float,
              help="Annual rent growth % (default: metro ZORI 5y CAGR, else 3).")
@click.option("--property-tax", default=0.9, show_default=True, type=float,
              help="Annual property tax, % of value.")
@click.option("--insurance", default=0.6, show_default=True, type=float,
              help="Annual insurance, % of value.")
@click.option("--maintenance", default=1.0, show_default=True, type=float,
              help="Annual maintenance, % of value.")
@click.option("--capex", default=0.5, show_default=True, type=float,
              help="Annual capital-expenditure reserve (roof/HVAC), % of value.")
@click.option("--hoa", default=0.0, show_default=True, type=float, help="Monthly HOA ($).")
@click.option("--ltcg", default=15.0, show_default=True, type=float,
              help="Long-term capital-gains rate % (applies to the invested alternative too).")
@click.option("--s121", default=500.0, show_default=True, type=float,
              help="§121 exclusion in $k (500 MFJ / 250 single).")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def buyrent_cmd(price, rent, metro, down, rate, term, benchmark_return,
                appreciation, rent_growth, property_tax, insurance, maintenance,
                capex, hoa, ltcg, s121, fmt):
    """Buy this listing, or keep renting it and invest the difference?

    Owner-occupied framing: --rent is what the SAME unit rents for. Compares
    buying vs renting-and-investing identical dollars over 5/10/20-year holds.
    The invested alternative is defined by --benchmark-return (a forecast has
    no real index prices).

    \b
    Examples:
      buyrent --price 1500000 --rent 4800 --metro "Los Angeles, CA" --hoa 550
      buyrent --price 700000 --rent 3000 --benchmark-return 11 --format json
    """
    if rate is None:
        try:
            rate = (mortgage_rate_at(datetime.date.today()) or 0.065) * 100
            click.echo(f"  Using 30y mortgage rate {rate:.2f}% (FRED PMMS)", err=True)
        except Exception:
            rate = 6.5
            click.echo("  FRED unavailable — using fallback 30y rate 6.50%", err=True)

    inp = PropertyInputs(
        price=price, rent=rent, metro=metro,
        down_pct=down / 100, rate=rate / 100, term_years=term,
        property_tax=property_tax / 100, insurance=insurance / 100,
        maintenance=maintenance / 100, capex=capex / 100, hoa_monthly=hoa,
        appreciation=appreciation / 100 if appreciation is not None else None,
        rent_growth=rent_growth / 100 if rent_growth is not None else None,
        benchmark_return=benchmark_return / 100, ltcg_rate=ltcg / 100,
        primary=True, s121_exclusion=s121 * 1000,
    )
    click.echo("  Running buy-vs-rent…", err=True)
    import requests
    try:
        result = buy_vs_rent(inp)
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc))
    except requests.exceptions.RequestException as exc:
        raise click.ClickException(f"Data fetch failed (Zillow/FRED): {exc}")

    if fmt == "json":
        print_json(build_envelope(
            "buyrent",
            {"price": price, "rent": rent, "metro": metro, "down": down,
             "rate": rate, "benchmark_return": benchmark_return},
            result))
        return
    _print_table(result)
