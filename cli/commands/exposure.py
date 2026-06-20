from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json
from core.broker import login
from core.exposure import compute_exposure, CAP_RATIO


@click.command()
@click.option("--min-weight", default=0.005, show_default=True, type=float,
              help="Hide positions whose delta-adjusted weight is below this (e.g. 0.005 = 0.5%).")
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def exposure_cmd(min_weight: float, fmt: str):
    """
    Delta-adjusted exposure across equity + options.

    Folds Black-Scholes option delta into each ticker's weight so the book
    reflects economic exposure: short puts add long-equivalent exposure, short
    (covered) calls cancel equity upside. IV is approximated by trailing-1Y
    realized vol of the underlying.
    """
    try:
        login()
        data = compute_exposure()
    except Exception as e:
        click.echo(f"  Robinhood error: {e}", err=True)
        sys.exit(1)

    if fmt == "json":
        print_json(build_envelope(
            command="exposure",
            args={"min_weight": min_weight},
            data=data,
        ))
        return

    total = data["total_value"]
    s = data["summary"]

    click.echo(f"\n{'='*78}")
    click.echo("  DELTA-ADJUSTED EXPOSURE")
    click.echo(f"{'='*78}")
    click.echo(f"  Portfolio: ${total:,.0f}  |  Equity: ${s['equity_total']:,.0f}  "
               f"|  Option Δ: ${s['option_delta_total']:+,.0f}  "
               f"|  Delta-adjusted: ${s['delta_adjusted_total']:,.0f}")
    prem = s.get("premium_total", 0.0)
    if prem:
        click.echo(f"  Net option premium collected: ${prem:+,.0f}"
                   f"  ({prem/total*100:+.2f}% of portfolio)")

    # Book-level Greek totals (only meaningful when options are held)
    theta_t = s.get("theta_dollars_total", 0.0)
    vega_t = s.get("vega_dollars_total", 0.0)
    rho_t = s.get("rho_dollars_total", 0.0)
    if theta_t or vega_t or rho_t:
        click.echo(f"  Book Greeks:  Theta ${theta_t:+,.2f}/day  "
                   f"|  Vega ${vega_t:+,.2f}/IV-pt  |  Rho ${rho_t:+,.2f}/rate-pt")

    # ── Per-ticker exposure (equity vs delta-adjusted) ──────────────────────────
    rows = []
    for p in data["positions"]:
        if abs(p["delta_weight"]) < min_weight and abs(p["equity_weight"]) < min_weight:
            continue
        opt = f"{p['option_delta_dollars']:+,.0f}" if p["has_options"] else "—"
        flag = ""
        if p["has_options"]:
            # Note when options materially move the weight vs equity-only
            if p["equity_value"] > 0 and p["delta_value"] < p["equity_value"] * CAP_RATIO:
                flag = "⚠ capped"        # short call eating equity upside
            elif p["equity_value"] == 0:
                flag = "synthetic"        # exposure exists only via options
        rows.append([
            p["ticker"],
            f"${p['equity_value']:,.0f}",
            f"{p['equity_weight']*100:.1f}%",
            opt,
            f"${p['delta_value']:,.0f}",
            f"{p['delta_weight']*100:.1f}%",
            flag,
        ])

    click.echo()
    click.echo(tabulate(
        rows,
        headers=["Ticker", "Equity $", "Eq %", "Option Δ$", "Delta-adj $", "Δ-adj %", ""],
        tablefmt="simple",
    ))

    # ── Option detail + position Greeks ─────────────────────────────────────────
    opt_rows = []
    for o in data["options"]:
        if o["underlying"] is None:
            opt_rows.append([o["ticker"], o["position_type"], o["option_type"][:4],
                             f"{o['strike']:.0f}", o["expiration"],
                             "no price data", "", "", "", "", "", "", "", f"${o.get('premium', 0):+,.0f}"])
            continue
        iv_tag = f"{o['iv']*100:.0f}%{'*' if o.get('iv_source')=='realized' else ''}"
        src = "RH" if o.get("greek_source") == "broker" else "BS"
        # Flag a position whose RH/Black-Scholes delta disagree by > 0.05/share.
        div = o.get("delta_divergence")
        if div is not None and div > 0.05:
            src += "≠"
        opt_rows.append([
            o["ticker"],
            o["position_type"],
            o["option_type"][:4],
            f"{o['strike']:.0f}",
            o["expiration"],
            f"{o['underlying']:.2f}",
            iv_tag,
            f"{o['pos_delta_shares']:+.1f}",                     # Δ share-equivalents
            f"{o['pos_gamma']:+.3f}",                            # Γ shares per $1
            f"${o['pos_theta_dollars']:+.2f}",                   # Θ $/day
            f"${o['pos_vega_dollars']:+.2f}",                    # ν $/IV-pt
            f"${o['pos_rho_dollars']:+.2f}",                     # ρ $/rate-pt
            "ITM" if o["itm"] else "OTM",
            src,
            f"${o.get('premium', 0):+,.0f}",
        ])

    if opt_rows:
        click.echo(f"\n{'─'*78}")
        click.echo("  OPTION POSITIONS — position-level Greeks (signed for your short/long side)")
        click.echo(f"{'─'*78}")
        click.echo(tabulate(
            opt_rows,
            headers=["Tk", "Pos", "Type", "Strike", "Expiry", "Und$", "IV",
                     "Δ sh", "Γ", "Θ/day", "ν/pt", "ρ/pt", "ITM", "Src", "Premium"],
            tablefmt="simple",
        ))
        click.echo("\n  Greeks are per-position (×contracts ×100, signed for your side):")
        click.echo("  Δ sh = share-equiv exposure  |  Γ = Δ-change per $1 move  |  Θ = P&L per day "
                   "(+ = decay earns you)")
        click.echo("  ν = P&L per +1 IV point  |  ρ = P&L per +1 rate point. IV from chain "
                   "(* = realized-vol fallback).")
        click.echo("  Src = Greek source: RH = Robinhood's model (American + dividends), "
                   "BS = our Black-Scholes fallback, ≠ = the two disagree on delta by >0.05/sh.")
    click.echo()
