from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import datetime

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json, fmt_pct
from config import BENCHMARKS
from core.realestate import (BREAKEVEN_HI, BREAKEVEN_LO, PropertyInputs,
                             analyze, mortgage_rate_at)


def _money(v) -> str:
    return f"${v:,.0f}" if v is not None else "—"


def _print_table(r: dict) -> None:
    p, b = r["property"], r["benchmark_alt"]
    head = f"{r['mode'].upper()}  {r['start']} → {r['end']}"
    if r["metro"]:
        head += f"  |  {r['metro']}"
    click.echo(f"\n  PROPERTY vs {r['benchmark']} — {head}")
    if r["mode"] == "forecast":
        click.echo(f"  Assumptions: appreciation {fmt_pct(r['assumed_appreciation'])}/yr, "
                   f"rent growth {fmt_pct(r['assumed_rent_growth'])}/yr, "
                   f"{r['benchmark']} {fmt_pct(r['assumed_benchmark_return'])}/yr")

    click.echo(tabulate([
        ["Out-of-pocket at close (down + costs)", _money(p["out_of_pocket_initial"]), ""],
        ["Total out-of-pocket over hold", _money(r["total_invested_out_of_pocket"]), ""],
        ["Monthly mortgage payment", _money(p["monthly_payment"]), ""],
        ["Year-1 cap rate (unlevered, net)", fmt_pct(p["cap_rate_y1"]), ""],
        ["Negative-carry months", p["negative_carry_months"], ""],
        ["", "PROPERTY", r["benchmark"]],
        ["Terminal wealth (after tax)", _money(p["terminal_wealth"]), _money(b["terminal_wealth"])],
        ["XIRR (after-tax, levered)", fmt_pct(p["irr"]), fmt_pct(b["irr"])],
    ], tablefmt="simple"))

    click.echo(f"\n  Property terminal = net sale {_money(p['net_sale_after_tax_and_loan'])} "
               f"(sale {_money(p['sale_value'])}, tax {_money(p['tax_at_sale'])}) "
               f"+ reinvested cash flows {_money(p['side_pocket_reinvested'])}")
    winner = "PROPERTY" if (p["terminal_wealth"] or 0) > (b["terminal_wealth"] or 0) \
        else r["benchmark"]
    edge = abs((p["terminal_wealth"] or 0) - (b["terminal_wealth"] or 0))
    click.echo(f"  Winner on equal out-of-pocket dollars: {winner} by {_money(edge)}")

    if r["mode"] == "forecast":
        be = r.get("breakeven_appreciation")
        if be is None:
            click.echo(f"  Breakeven appreciation vs {r['benchmark']}: "
                       f">{BREAKEVEN_HI * 100:.0f}%/yr (property cannot win "
                       "under these assumptions).")
        elif be <= BREAKEVEN_LO + 1e-9:
            click.echo(f"  Breakeven appreciation vs {r['benchmark']}: "
                       f"≤ {BREAKEVEN_LO * 100:.0f}%/yr (property wins even at "
                       "the lower search bound).")
        else:
            click.echo(f"  Breakeven appreciation vs {r['benchmark']}: "
                       f"{fmt_pct(be)}/yr — property wins above this, loses below.")

    if r.get("risk"):
        k = r["risk"]
        click.echo(f"\n  Risk ({r['metro']}): metro 10y CAGR {fmt_pct(k['metro_cagr_10y'])}, "
                   f"max DD {fmt_pct(k['metro_max_drawdown'])}")
        click.echo(f"  Vol: smoothed index {fmt_pct(k['metro_index_vol_smoothed'])} → "
                   f"de-smoothed single-house est. {fmt_pct(k['single_house_vol_estimate'])} → "
                   f"levered at your down payment ≈ {fmt_pct(k['levered_equity_vol_initial'])}"
                   + (f"  (vs {r['benchmark']} realized {fmt_pct(k['benchmark_vol'])})"
                      if k.get("benchmark_vol") else ""))

    click.echo("\n  Caveats: single-house risk exceeds any index estimate (one asset, one "
               "metro, tenant, illiquidity, 7% round-trip exit); benchmark history is "
               "price-return only (no dividends ~1.3%/yr, roughly offset by ignoring "
               "dividend tax drag); federal taxes only (no state income tax, SALT, AMT, "
               "or itemization modeling); passive-loss $25k offset and cost-segregation "
               "bonus depreciation not modeled. Not tax advice.\n")


@click.command(name="property")
@click.option("--price", required=True, type=float, help="Purchase price ($).")
@click.option("--rent", default=0.0, type=float,
              help="Monthly gross rent (rentals) or rent you'd otherwise pay (--primary).")
@click.option("--down", default=20.0, show_default=True, type=float, help="Down payment %.")
@click.option("--rate", default=None, type=float,
              help="Mortgage rate % (default: 30y PMMS from FRED; investor loans "
                   "typically +0.5–1.0% over that).")
@click.option("--term", default=30, show_default=True, type=int, help="Mortgage term (years).")
@click.option("--hold", default=7.0, show_default=True, type=float, help="Holding period (years).")
@click.option("--metro", default=None,
              help="Zillow metro, e.g. 'Austin, TX' — enables real history.")
@click.option("--backtest-start", default=None,
              help="YYYY-MM: backtest with actual metro prices from this month.")
@click.option("--benchmark", default="voo", show_default=True,
              type=click.Choice(["voo", "spx", "nasdaq", "russell"]))
@click.option("--benchmark-return", default=9.5, show_default=True, type=float,
              help="Forecast mode: assumed benchmark annual return %.")
@click.option("--appreciation", default=None, type=float,
              help="Forecast: annual appreciation % (default: metro 10y CAGR).")
@click.option("--rent-growth", default=None, type=float,
              help="Forecast: annual rent growth % (default: metro ZORI 5y CAGR).")
@click.option("--property-tax", default=0.9, show_default=True, type=float,
              help="Annual property tax, % of value (state range 0.27–2.23).")
@click.option("--insurance", default=0.6, show_default=True, type=float,
              help="Annual insurance, % of value.")
@click.option("--maintenance", default=1.0, show_default=True, type=float,
              help="Annual maintenance, % of value.")
@click.option("--capex", default=0.5, show_default=True, type=float,
              help="Annual capital-expenditure reserve (roof/HVAC), % of value.")
@click.option("--management", default=0.0, show_default=True, type=float,
              help="Property management, % of collected rent (typical 8-12 if not self-managed).")
@click.option("--vacancy", default=5.0, show_default=True, type=float,
              help="Vacancy + credit loss, % of gross rent.")
@click.option("--hoa", default=0.0, show_default=True, type=float, help="Monthly HOA ($).")
@click.option("--closing-buy", default=3.0, show_default=True, type=float,
              help="Buy-side closing costs, % of price.")
@click.option("--closing-sell", default=7.0, show_default=True, type=float,
              help="Sell-side costs incl. ~5.5% commission, % of sale price.")
@click.option("--tax-bracket", default=32.0, show_default=True, type=float,
              help="Marginal federal income tax rate %.")
@click.option("--ltcg", default=15.0, show_default=True, type=float,
              help="Long-term capital-gains rate %.")
@click.option("--structure-share", default=80.0, show_default=True, type=float,
              help="Depreciable structure share of basis, % (land excluded).")
@click.option("--qbi", is_flag=True, default=False,
              help="Apply the 20%% QBI deduction (requires the 250-hour safe harbor).")
@click.option("--niit", is_flag=True, default=False,
              help="Apply 3.8%% NIIT (MAGI over $200k single / $250k MFJ).")
@click.option("--hold-forever", is_flag=True, default=False,
              help="1031-until-step-up: model zero tax at sale.")
@click.option("--primary", is_flag=True, default=False,
              help="Owner-occupied: imputed rent, §121 exclusion, no depreciation.")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def property_cmd(price, rent, down, rate, term, hold, metro, backtest_start,
                 benchmark, benchmark_return, appreciation, rent_growth,
                 property_tax, insurance, maintenance, capex, management,
                 vacancy, hoa, closing_buy, closing_sell, tax_bracket, ltcg,
                 structure_share, qbi, niit, hold_forever, primary, fmt):
    """Backtest or forecast a property purchase vs an index, after tax.

    \b
    Examples:
      property --price 450000 --rent 2600 --metro "Austin, TX" --backtest-start 2016-07
      property --price 450000 --rent 2600 --metro "Austin, TX" --hold 10 --niit
      property --price 700000 --rent 3200 --primary --hold 10 --benchmark nasdaq
    """
    if backtest_start:
        try:
            datetime.date.fromisoformat(backtest_start + "-01")
        except ValueError:
            raise click.BadParameter("expected YYYY-MM", param_hint="--backtest-start")
    if rate is None:
        when = (datetime.date.fromisoformat(backtest_start + "-01")
                if backtest_start else datetime.date.today())
        try:
            rate = (mortgage_rate_at(when) or 0.065) * 100
            click.echo(f"  Using 30y mortgage rate {rate:.2f}% (FRED PMMS, {when})",
                       err=True)
        except Exception:
            rate = 6.5
            click.echo("  FRED unavailable — using fallback 30y rate 6.50%", err=True)

    inp = PropertyInputs(
        price=price, rent=rent, down_pct=down / 100, rate=rate / 100,
        term_years=term, hold_years=hold, metro=metro,
        property_tax=property_tax / 100, insurance=insurance / 100,
        maintenance=maintenance / 100, capex=capex / 100,
        management=management / 100, vacancy=vacancy / 100, hoa_monthly=hoa,
        closing_buy=closing_buy / 100, closing_sell=closing_sell / 100,
        appreciation=appreciation / 100 if appreciation is not None else None,
        rent_growth=rent_growth / 100 if rent_growth is not None else None,
        benchmark_return=benchmark_return / 100,
        marginal_rate=tax_bracket / 100, ltcg_rate=ltcg / 100,
        structure_share=structure_share / 100,
        qbi=qbi, niit=niit, hold_forever=hold_forever, primary=primary,
    )
    mode = "backtest" if backtest_start else "forecast"
    click.echo(f"  Running {mode}…", err=True)
    import requests
    try:
        result = analyze(inp, mode, benchmark_ticker=BENCHMARKS.get(benchmark, "VOO"),
                         backtest_start=backtest_start)
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc))
    except requests.exceptions.RequestException as exc:
        raise click.ClickException(f"Data fetch failed (Zillow/FRED): {exc}")

    if fmt == "json":
        print_json(build_envelope(
            "property",
            {"price": price, "rent": rent, "metro": metro, "mode": mode,
             "hold": hold, "down": down, "rate": rate,
             "backtest_start": backtest_start, "benchmark": benchmark,
             "primary": primary, "hold_forever": hold_forever},
            result))
        return
    _print_table(result)
