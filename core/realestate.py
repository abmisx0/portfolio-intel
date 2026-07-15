"""
Real-estate analysis: leveraged property backtest/forecast vs index investing.

Data sources (all free, no API keys):
  - Zillow Research public CSVs (metro-level ZHVI home values, ZORI rents),
    downloaded to data/ and refreshed weekly. Zillow occasionally renames
    files, so each kind carries fallback URLs.
  - FRED keyless CSV endpoint (fredgraph.csv?id=SERIES) for Case-Shiller and
    30-year mortgage rates.

Comparison methodology (equal out-of-pocket, both sides after tax):
  Both strategies invest identical out-of-pocket dollars on identical dates.
  The property's positive interim cash flows (after-tax rental profit) are
  reinvested into the benchmark ("side pocket"). At horizon:
      property wealth  = net after-tax sale proceeds + after-tax side pocket
      benchmark wealth = the same contributions in the benchmark, LTCG applied
                         to the terminal gain
  Backtest mode uses actual metro price/rent paths and actual benchmark
  prices; forecast mode uses assumption-driven paths and an assumed benchmark
  return (future index prices don't exist). Benchmark history is price-return
  only (no dividends, ~1.3%/yr understatement) — roughly offset by ignoring
  annual dividend tax drag; noted in output caveats.

Tax model (2026 federal rules, post-OBBBA; parameterized):
  rentals — 27.5y straight-line depreciation on the structure share, ordinary
  tax on net rental income (optional 20% QBI haircut when the safe harbor
  applies; optional 3.8% NIIT), passive losses carry forward and release at
  sale, depreciation recapture at min(marginal, 25%), LTCG (+NIIT) on the
  remaining gain. `hold_forever` models 1031-until-step-up (no sale tax).
  primary — no depreciation; rent is imputed (untaxed housing cost you stop
  paying); §121 exclusion at sale.

Risk model: metro index vol is smoothed — outputs report a de-smoothed
single-house estimate: sigma_house = sqrt((idx_vol*1.6)^2 + 0.10^2) per
Geltner-style unsmoothing + ~10% idiosyncratic single-home vol, then levered
by the initial equity fraction. This UNDERSTATES nothing on purpose: one
asset, one metro, illiquid, with a tenant.
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from datetime import date

import pandas as pd
import requests

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATA_DIR
from core.data_fetcher import get_close_series
from core.performance import _xirr

logger = logging.getLogger(__name__)

_ZILLOW_URLS = {
    "zhvi": [
        "https://files.zillowstatic.com/research/public_csvs/zhvi/"
        "Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv",
    ],
    "zori": [
        "https://files.zillowstatic.com/research/public_csvs/zori/"
        "Metro_zori_uc_sfrcondomfr_sm_month.csv",
        "https://files.zillowstatic.com/research/public_csvs/zori/"
        "Metro_zori_uc_sfrcondomfr_sm_sa_month.csv",
    ],
}
_ZILLOW_TTL_SECONDS = 7 * 86400
_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"

DESMOOTH_FACTOR = 1.6      # Geltner-style unsmoothing multiplier on index vol
IDIOSYNCRATIC_VOL = 0.10   # single-home vol component beyond the metro index
BREAKEVEN_LO = -0.05       # breakeven-appreciation bisection search bounds —
BREAKEVEN_HI = 0.15        # renderers import these; don't re-encode the values


# ── Data layer ─────────────────────────────────────────────────────────────────

def _zillow_frame(kind: str) -> pd.DataFrame:
    """Download (weekly-cached to data/) and parse a Zillow metro CSV."""
    path = DATA_DIR / f"zillow_{kind}_metro.csv"
    if not path.exists() or (time.time() - path.stat().st_mtime) > _ZILLOW_TTL_SECONDS:
        last_exc = None
        for url in _ZILLOW_URLS[kind]:
            try:
                logger.info("Downloading Zillow %s metro data…", kind)
                resp = requests.get(url, timeout=120)
                resp.raise_for_status()
                path.write_bytes(resp.content)
                break
            except Exception as exc:      # try the fallback filename
                last_exc = exc
        else:
            if path.exists():             # stale beats broken
                logger.warning("Zillow %s download failed (%s) — using stale "
                               "cache from %s", kind, last_exc,
                               time.ctime(path.stat().st_mtime))
            else:
                raise RuntimeError(
                    f"All Zillow {kind} URLs failed (files may have been renamed "
                    f"— check zillow.com/research/data): {last_exc}")
    return pd.read_csv(path)


def zillow_metro_series(metro: str, kind: str = "zhvi") -> tuple[str, pd.Series]:
    """Monthly series for a metro ('Austin, TX' — case-insensitive substring).

    Returns (matched_region_name, series). Raises ValueError when no or
    multiple metros match.
    """
    df = _zillow_frame(kind)
    mask = df["RegionName"].str.contains(metro, case=False, regex=False)
    matches = df[mask]
    if matches.empty:
        raise ValueError(f"No Zillow metro matches '{metro}'")
    if len(matches) > 1:
        names = ", ".join(matches["RegionName"].head(5))
        raise ValueError(f"'{metro}' is ambiguous — matches: {names}")
    row = matches.iloc[0]
    date_cols = [c for c in df.columns if c[:2] in ("19", "20")]
    s = pd.Series(row[date_cols].astype(float).values,
                  index=pd.to_datetime(date_cols)).dropna()
    return row["RegionName"], s


def fred_series(series_id: str) -> pd.Series:
    """FRED series via the keyless CSV endpoint (no cache — small + fast)."""
    resp = requests.get(_FRED_CSV.format(sid=series_id), timeout=60)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return pd.Series(df["value"].values, index=pd.to_datetime(df["date"])).dropna()


def mortgage_rate_at(when: date) -> float | None:
    """30y fixed rate (fraction) nearest to `when` from MORTGAGE30US.
    Note: PMMS is a primary-residence average — investor loans typically
    price 50–100bp higher (caller's concern)."""
    s = fred_series("MORTGAGE30US")
    s = s[s.index <= pd.Timestamp(when)]
    return float(s.iloc[-1]) / 100 if len(s) else None


# ── Mortgage math ──────────────────────────────────────────────────────────────

def monthly_payment(principal: float, annual_rate: float, years: int) -> float:
    r = annual_rate / 12
    n = years * 12
    if r == 0:
        return principal / n
    return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)


# ── Inputs ─────────────────────────────────────────────────────────────────────

@dataclass
class PropertyInputs:
    price: float
    down_pct: float = 0.20            # fraction of price
    rate: float = 0.065               # annual mortgage rate (fraction)
    term_years: int = 30
    rent: float = 0.0                 # monthly gross rent (or rent saved if primary)
    hold_years: float = 7.0
    metro: str | None = None
    # operating assumptions (annual fractions of value unless noted)
    property_tax: float = 0.009
    insurance: float = 0.006
    maintenance: float = 0.010
    capex: float = 0.005              # roof/HVAC reserve, separate from maintenance
    management: float = 0.0           # fraction of collected rent
    vacancy: float = 0.05             # fraction of gross rent
    hoa_monthly: float = 0.0
    closing_buy: float = 0.03         # fraction of price
    closing_sell: float = 0.07        # fraction of sale price (commission+costs)
    # growth assumptions (forecast mode; backtest uses actual paths)
    appreciation: float | None = None  # annual; None → metro 10y CAGR
    rent_growth: float | None = None   # annual; None → ZORI 5y CAGR or 3%
    benchmark_return: float = 0.095    # forecast-mode index assumption (annual)
    # tax parameters (2026 federal)
    marginal_rate: float = 0.32
    ltcg_rate: float = 0.15
    structure_share: float = 0.80     # depreciable share of basis
    qbi: bool = False                 # 20% QBI deduction (250-hr safe harbor)
    niit: bool = False                # 3.8% net investment income tax
    hold_forever: bool = False        # 1031-until-step-up: no tax at sale
    primary: bool = False             # owner-occupied: imputed rent + §121
    s121_exclusion: float = 500_000.0


# ── Simulation ─────────────────────────────────────────────────────────────────

def _simulate(inp: PropertyInputs, value_path: list[float], rent_path: list[float],
              dates: list[date]) -> dict:
    """Monthly cash-flow simulation. Paths are per-month value/rent levels
    aligned with `dates` (first date = purchase, last = sale)."""
    months = len(dates) - 1
    loan = inp.price * (1 - inp.down_pct)
    pay = monthly_payment(loan, inp.rate, inp.term_years)
    balance = loan
    monthly_rate = inp.rate / 12
    income_tax_rate = inp.marginal_rate * (0.80 if inp.qbi else 1.0) \
        + (0.038 if inp.niit else 0.0)

    out_of_pocket_0 = inp.price * inp.down_pct + inp.price * inp.closing_buy
    flows = [-out_of_pocket_0]
    flow_dates = [dates[0]]

    depreciable_total = ((inp.price + inp.price * inp.closing_buy)
                         * inp.structure_share) if not inp.primary else 0.0
    depreciation_m = depreciable_total / 27.5 / 12
    accum_depr = 0.0
    loss_carryforward = 0.0
    year_taxable = 0.0
    year_flows: list[float] = []
    negative_carry_months = 0
    opex_rate = inp.property_tax + inp.insurance + inp.maintenance + inp.capex

    for m in range(1, months + 1):
        value, rent = value_path[m], rent_path[m]
        interest = balance * monthly_rate
        principal_paid = min(pay - interest, balance)
        balance -= principal_paid

        collected = rent * (1 - inp.vacancy) if not inp.primary else 0.0
        opex = (value * opex_rate / 12 + inp.hoa_monthly
                + collected * inp.management)
        cash = collected - opex - (interest + principal_paid)
        if not inp.primary and cash < 0:
            negative_carry_months += 1
        if inp.primary:
            cash += rent  # imputed rent: housing cost you stop paying, untaxed

        if not inp.primary:
            # capex is a cash cost but NOT deductible (it capitalizes);
            # depreciation is capped at the 27.5-year depreciable basis.
            depr = min(depreciation_m, depreciable_total - accum_depr)
            deductible_opex = opex - value * inp.capex / 12
            year_taxable += collected - deductible_opex - interest - depr
            accum_depr += depr
        year_flows.append(cash)

        if m % 12 == 0 or m == months:      # settle taxes annually
            if year_taxable > 0:
                offset = min(loss_carryforward, year_taxable)
                loss_carryforward -= offset
                year_flows[-1] -= (year_taxable - offset) * income_tax_rate
            elif year_taxable < 0:
                loss_carryforward += -year_taxable
            year_taxable = 0.0
            flows.extend(year_flows)
            flow_dates.extend(dates[m - len(year_flows) + 1: m + 1])
            year_flows = []

    # Sale at final date. hold_forever = never actually sell (1031 chains to a
    # step-up at death): terminal wealth marks equity to market, no sale costs,
    # no tax — the most favorable, and honest about being unrealized.
    sale_value = value_path[months]
    sale_net = sale_value if inp.hold_forever else sale_value * (1 - inp.closing_sell)
    basis = inp.price + inp.price * inp.closing_buy - accum_depr
    gain = sale_net - basis
    tax_sale = 0.0
    # Gains on holds under 1 year are short-term (ordinary rates).
    base_cg = inp.ltcg_rate if inp.hold_years >= 1 else inp.marginal_rate
    cg_rate = base_cg + (0.038 if inp.niit else 0.0)
    if inp.hold_forever:
        tax_sale = 0.0                       # 1031 exchanges until step-up at death
    elif inp.primary:
        # §121 requires 2-of-5 years of ownership AND use.
        exclusion = inp.s121_exclusion if inp.hold_years >= 2 else 0.0
        taxable_gain = max(0.0, gain - exclusion)
        tax_sale = taxable_gain * cg_rate
    elif gain > 0:
        recapture = min(accum_depr, gain)
        tax_sale = (recapture * min(inp.marginal_rate, 0.25)
                    + (gain - recapture) * cg_rate)
    if not inp.primary and not inp.hold_forever:
        # suspended passive losses release at sale against ordinary income
        tax_sale -= loss_carryforward * inp.marginal_rate
    flows.append(sale_net - balance - tax_sale)
    flow_dates.append(dates[months])

    return {
        "flows": flows, "dates": flow_dates, "irr": _xirr(flows, flow_dates),
        "out_of_pocket_0": out_of_pocket_0,
        "monthly_payment": pay,
        "sale_value": sale_value, "sale_net_after_tax": sale_net - balance - tax_sale,
        "accumulated_depreciation": accum_depr,
        "loan_balance_end": balance, "tax_at_sale": tax_sale,
        "negative_carry_months": negative_carry_months,
        "cap_rate_y1": ((rent_path[1] * 12 * (1 - inp.vacancy)
                         - value_path[1] * opex_rate - inp.hoa_monthly * 12)
                        / inp.price) if inp.rent else None,
    }


# ── Benchmark clone (equal out-of-pocket, after tax) ───────────────────────────

def _after_tax(terminal: float, contributed: float, ltcg_rate: float) -> float:
    return terminal - max(0.0, terminal - contributed) * ltcg_rate


def _benchmark_backtest(flows: list[float], dates: list[date],
                        ticker: str, ltcg_rate: float) -> dict:
    """Actual benchmark prices: negatives buy the index, positive property
    flows buy the side pocket. LTCG applied to terminal gains on both."""
    px = get_close_series(ticker, str(dates[0]), str(dates[-1])).sort_index()
    if px.empty:
        raise ValueError(f"No price data for benchmark {ticker} over this window")
    if pd.Timestamp(dates[0]) < px.index[0] - pd.Timedelta(days=14):
        raise ValueError(
            f"Benchmark {ticker} has no data before {px.index[0].date()} — "
            f"start the backtest there or later, or use --benchmark spx "
            "(index history reaches further back)")

    def price_at(d: date) -> float:
        sub = px[px.index <= pd.Timestamp(d)]
        return float(sub.iloc[-1]) if len(sub) else float(px.iloc[0])

    end_price = price_at(dates[-1])
    bench_shares = side_shares = bench_in = side_in = 0.0
    bench_flows, bench_dates = [], []
    for cf, d in zip(flows[:-1], dates[:-1]):   # exclude terminal sale flow
        p = price_at(d)
        if cf < 0:
            bench_shares += -cf / p
            bench_in += -cf
            bench_flows.append(cf)
            bench_dates.append(d)
        elif cf > 0:
            side_shares += cf / p
            side_in += cf
    bench_terminal = _after_tax(bench_shares * end_price, bench_in, ltcg_rate)
    bench_flows.append(bench_terminal)
    bench_dates.append(dates[-1])
    rets = px.pct_change().dropna()
    return {
        "benchmark_terminal": bench_terminal,
        "side_pocket_terminal": _after_tax(side_shares * end_price, side_in, ltcg_rate),
        "benchmark_irr": _xirr(bench_flows, bench_dates),
        "benchmark_vol": float(rets.std() * (252 ** 0.5)) if len(rets) > 50 else None,
    }


def _benchmark_forecast(flows: list[float], dates: list[date],
                        annual_return: float, ltcg_rate: float) -> dict:
    """Assumed constant benchmark return (future prices don't exist)."""
    t_end = dates[-1]
    bench = side = bench_in = side_in = 0.0
    bench_flows, bench_dates = [], []
    for cf, d in zip(flows[:-1], dates[:-1]):
        years = (t_end - d).days / 365.0
        growth = (1 + annual_return) ** years
        if cf < 0:
            bench += -cf * growth
            bench_in += -cf
            bench_flows.append(cf)
            bench_dates.append(d)
        elif cf > 0:
            side += cf * growth
            side_in += cf
    bench_terminal = _after_tax(bench, bench_in, ltcg_rate)
    bench_flows.append(bench_terminal)
    bench_dates.append(dates[-1])
    return {
        "benchmark_terminal": bench_terminal,
        "side_pocket_terminal": _after_tax(side, side_in, ltcg_rate),
        "benchmark_irr": _xirr(bench_flows, bench_dates),
        "benchmark_vol": None,
    }


def _monthly_dates(start: date, months: int) -> list[date]:
    idx = pd.date_range(pd.Timestamp(start), periods=months + 1, freq="MS")
    return [d.date() for d in idx]


# ── Public API ─────────────────────────────────────────────────────────────────

def analyze(inp: PropertyInputs, mode: str, benchmark_ticker: str = "VOO",
            backtest_start: str | None = None) -> dict:
    """mode='forecast' (assumption-driven) or 'backtest' (actual metro paths)."""
    months = int(round(inp.hold_years * 12))
    appr = rg = None

    metro_name, zhvi, zori = None, None, None
    if inp.metro:
        metro_name, zhvi = zillow_metro_series(inp.metro, "zhvi")
        try:
            _, zori = zillow_metro_series(inp.metro, "zori")
        except (ValueError, RuntimeError):
            zori = None

    if mode == "backtest":
        if zhvi is None:
            raise ValueError("Backtest requires --metro (uses actual Zillow history)")
        start = pd.Timestamp(backtest_start)
        hist = zhvi[zhvi.index >= start]
        if len(hist) < months + 1:
            raise ValueError(
                f"Only {len(hist)-1} months of {metro_name} data after "
                f"{backtest_start}; shorten --hold or move the start earlier")
        hist = hist.iloc[:months + 1]
        value_path = [inp.price * float(v / hist.iloc[0]) for v in hist]
        rh = zori[zori.index >= start].iloc[:months + 1] if zori is not None else None
        # ZORI starts ~2015 — only use it when it actually covers the window
        # from the same starting month; positional slicing would silently map
        # later-year rents onto earlier simulation months.
        if (rh is not None and len(rh) >= months + 1
                and abs((rh.index[0] - hist.index[0]).days) <= 45):
            rent_path = [inp.rent * float(r / rh.iloc[0]) for r in rh]
        else:
            g = (1 + (inp.rent_growth if inp.rent_growth is not None else 0.03)) ** (1 / 12)
            rent_path = [inp.rent * g ** m for m in range(months + 1)]
        dates = [d.date() for d in hist.index]
    else:
        if inp.appreciation is not None:
            appr = inp.appreciation
        elif zhvi is not None and len(zhvi) > 121:
            appr = float((zhvi.iloc[-1] / zhvi.iloc[-121]) ** (1 / 10) - 1)  # 10y CAGR
        else:
            cs = fred_series("CSUSHPINSA")
            appr = float((cs.iloc[-1] / cs.iloc[-121]) ** (1 / 10) - 1)
        if inp.rent_growth is not None:
            rg = inp.rent_growth
        elif zori is not None and len(zori) > 61:
            rg = float((zori.iloc[-1] / zori.iloc[-61]) ** (1 / 5) - 1)      # 5y CAGR
        else:
            rg = 0.03
        am, rm = (1 + appr) ** (1 / 12), (1 + rg) ** (1 / 12)
        value_path = [inp.price * am ** m for m in range(months + 1)]
        rent_path = [inp.rent * rm ** m for m in range(months + 1)]
        dates = _monthly_dates(date.today(), months)

    sim = _simulate(inp, value_path, rent_path, dates)
    if mode == "backtest":
        bench = _benchmark_backtest(sim["flows"], sim["dates"],
                                    benchmark_ticker, inp.ltcg_rate)
    else:
        bench = _benchmark_forecast(sim["flows"], sim["dates"],
                                    inp.benchmark_return, inp.ltcg_rate)

    property_terminal = sim["sale_net_after_tax"] + bench["side_pocket_terminal"]

    # Risk block: de-smoothed single-house vol, levered by initial equity.
    risk = None
    if zhvi is not None:
        idx_vol = float(zhvi.pct_change().dropna().std() * (12 ** 0.5))
        house_vol = ((idx_vol * DESMOOTH_FACTOR) ** 2 + IDIOSYNCRATIC_VOL ** 2) ** 0.5
        risk = {
            "metro_index_vol_smoothed": idx_vol,
            "single_house_vol_estimate": house_vol,
            "levered_equity_vol_initial": (house_vol / inp.down_pct
                                           if inp.down_pct > 0 else None),
            "benchmark_vol": bench.get("benchmark_vol"),
            "metro_cagr_10y": float((zhvi.iloc[-1] / zhvi.iloc[-121]) ** (1 / 10) - 1)
            if len(zhvi) > 121 else None,
            "metro_max_drawdown": float((zhvi / zhvi.cummax() - 1).min()),
        }

    result = {
        "mode": mode, "metro": metro_name, "benchmark": benchmark_ticker.upper(),
        "months": months, "start": str(dates[0]), "end": str(dates[-1]),
        "assumed_appreciation": appr, "assumed_rent_growth": rg,
        "assumed_benchmark_return": inp.benchmark_return if mode == "forecast" else None,
        "property": {
            "irr": sim["irr"],
            "terminal_wealth": property_terminal,
            "sale_value": sim["sale_value"],
            "net_sale_after_tax_and_loan": sim["sale_net_after_tax"],
            "side_pocket_reinvested": bench["side_pocket_terminal"],
            "out_of_pocket_initial": sim["out_of_pocket_0"],
            "monthly_payment": sim["monthly_payment"],
            "cap_rate_y1": sim["cap_rate_y1"],
            "accumulated_depreciation": sim["accumulated_depreciation"],
            "tax_at_sale": sim["tax_at_sale"],
            "negative_carry_months": sim["negative_carry_months"],
        },
        "benchmark_alt": {
            "irr": bench["benchmark_irr"],
            "terminal_wealth": bench["benchmark_terminal"],
        },
        "total_invested_out_of_pocket": -sum(f for f in sim["flows"][:-1] if f < 0),
        "risk": risk,
    }

    # Breakeven appreciation (forecast only): the annual appreciation at which
    # property terminal wealth equals the benchmark alternative.
    if mode == "forecast":
        result["breakeven_appreciation"] = _breakeven_appreciation(
            inp, months, rent_path, benchmark_ticker)
    return result


def _breakeven_appreciation(inp: PropertyInputs, months: int,
                            rent_path: list[float], ticker: str) -> float | None:
    dates = _monthly_dates(date.today(), months)

    def wealth_gap(appr: float) -> float:
        am = (1 + appr) ** (1 / 12)
        vp = [inp.price * am ** m for m in range(months + 1)]
        sim = _simulate(inp, vp, rent_path, dates)
        bench = _benchmark_forecast(sim["flows"], sim["dates"],
                                    inp.benchmark_return, inp.ltcg_rate)
        prop = sim["sale_net_after_tax"] + bench["side_pocket_terminal"]
        return prop - bench["benchmark_terminal"]

    lo, hi = BREAKEVEN_LO, BREAKEVEN_HI
    try:
        if wealth_gap(lo) > 0:
            return lo   # property wins even at the lower search bound
        if wealth_gap(hi) < 0:
            return None  # can't win within the upper search bound
        for _ in range(40):
            mid = (lo + hi) / 2
            if wealth_gap(mid) > 0:
                hi = mid
            else:
                lo = mid
        return (lo + hi) / 2
    except Exception:
        return None


# ── Buy-vs-rent verdict for a specific listing ─────────────────────────────────

def buy_vs_rent(inp: PropertyInputs, holds: tuple = (5, 10, 20)) -> dict:
    """Should I buy this listing or keep renting it and invest the difference?

    Owner-occupied framing (primary=True is applied to per-hold copies; the
    caller's `inp` is never mutated): `inp.rent` is the market rent of the
    SAME unit (imputed rent). For each hold, runs the forecast comparison of
    buying vs renting-and-investing identical out-of-pocket dollars at
    `inp.benchmark_return` — the invested alternative is defined by that
    assumed return, not by a benchmark ticker (forecast mode has no real
    prices). Also returns the monthly own-vs-rent cost split and the
    price-to-rent ratio with its conventional zone.
    """
    if inp.rent <= 0:
        raise ValueError("buy_vs_rent requires the unit's market rent (> 0)")
    loan = inp.price * (1 - inp.down_pct)
    pay = monthly_payment(loan, inp.rate, inp.term_years)
    carry = (inp.price * (inp.property_tax + inp.insurance + inp.maintenance
                          + inp.capex) / 12 + inp.hoa_monthly)
    own_monthly = pay + carry
    p2r = inp.price / (inp.rent * 12) if inp.rent else None
    if p2r is None:
        zone = "unknown"
    elif p2r < 18:
        zone = "buy-leaning"
    elif p2r <= 22:
        zone = "gray zone"
    else:
        zone = "rent-leaning"

    scenarios = []
    for hold in holds:
        run = PropertyInputs(**{**inp.__dict__, "hold_years": float(hold),
                                "primary": True})
        r = analyze(run, "forecast")
        scenarios.append({
            "hold_years": hold,
            "own_terminal": r["property"]["terminal_wealth"],
            "rent_invest_terminal": r["benchmark_alt"]["terminal_wealth"],
            "own_irr": r["property"]["irr"],
            "breakeven_appreciation": r.get("breakeven_appreciation"),
            "assumed_appreciation": r["assumed_appreciation"],
        })

    votes_buy = sum(1 for s in scenarios
                    if (s["own_terminal"] or 0) > (s["rent_invest_terminal"] or 0))
    return {
        "price": inp.price, "rent": inp.rent, "metro": inp.metro,
        "assumed_benchmark_return": inp.benchmark_return,
        "appreciation_overridden": inp.appreciation is not None,
        "monthly": {
            "mortgage_payment": pay,
            "carry_costs": carry,
            "own_total": own_monthly,
            "rent": inp.rent,
            "premium_to_own": own_monthly - inp.rent,
        },
        "price_to_rent": p2r, "price_to_rent_zone": zone,
        "scenarios": scenarios,
        "verdict": "BUY" if votes_buy > len(scenarios) / 2 else "RENT",
        "votes_buy": votes_buy, "votes_total": len(scenarios),
    }
