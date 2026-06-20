from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import date, timedelta

import click
from tabulate import tabulate

from cli.formatters import build_envelope, print_json
from core.macro import get_macro, get_yield_curve, get_risk_free_rate

# Series shown in the regime snapshot: key → (label, value format)
_SNAPSHOT = [
    ("VIX",  "VIX (vol regime)",   "{:.1f}"),
    ("SPX",  "S&P 500",            "{:,.0f}"),
    ("GOLD", "Gold ($/oz)",        "{:,.0f}"),
    ("WTI",  "WTI crude ($/bbl)",  "{:.1f}"),
    ("DXY",  "Dollar index",       "{:.1f}"),
]


def _chg(series, days: int) -> float | None:
    """Trailing percent change over ~days calendar days."""
    cutoff = series.index[-1] - timedelta(days=days)
    base = series[series.index <= cutoff]
    if base.empty:
        return None
    return float(series.iloc[-1] / base.iloc[-1] - 1)


@click.command()
@click.option("--format", "fmt", default="table", show_default=True,
              type=click.Choice(["json", "table"]))
def macro_cmd(fmt: str):
    """Market regime snapshot: VIX, index levels, commodities, dollar, yield curve.

    \b
    Examples:
      macro
      macro --format json
    """
    start = date.today() - timedelta(days=400)

    rows_data = []
    for key, label, vfmt in _SNAPSHOT:
        s = get_macro(key, start=start)
        if s.empty:
            rows_data.append({"key": key, "label": label, "level": None,
                              "chg_1m": None, "chg_1y": None, "_fmt": vfmt})
            continue
        rows_data.append({
            "key": key, "label": label,
            "level": float(s.iloc[-1]),
            "chg_1m": _chg(s, 30),
            "chg_1y": _chg(s, 365),
            "_fmt": vfmt,
        })

    curve = get_yield_curve()
    rfr = get_risk_free_rate()

    if fmt == "json":
        print_json(build_envelope(
            command="macro",
            args={},
            data={
                "snapshot": [{k: v for k, v in r.items() if k != "_fmt"} for r in rows_data],
                "yield_curve": curve,
                "risk_free_rate": rfr,
            },
            data_freshness=curve.get("as_of"),
        ))
        return

    def _pct(v):
        return f"{v * 100:+.1f}%" if v is not None else "—"

    table = [
        [r["label"],
         r["_fmt"].format(r["level"]) if r["level"] is not None else "—",
         _pct(r["chg_1m"]), _pct(r["chg_1y"])]
        for r in rows_data
    ]
    click.echo("\n  Market Regime")
    click.echo(tabulate(table, headers=["", "Level", "1M", "1Y"], tablefmt="simple"))

    tenors = [t for t in ("3M", "2Y", "5Y", "10Y", "30Y") if curve.get(t) is not None]
    if tenors:
        click.echo(f"\n  Treasury curve ({curve['as_of']}):  "
                   + "   ".join(f"{t} {curve[t]:.2f}%" for t in tenors))
        spread = curve.get("spread_10y_2y")
        if spread is not None:
            shape = "normal" if spread > 0.1 else ("flat" if spread > -0.1 else "INVERTED")
            click.echo(f"  10Y–2Y spread: {spread:+.2f}%  ({shape})")
    click.echo(f"  Risk-free rate (90d avg ^IRX): {rfr:.2%}\n")
