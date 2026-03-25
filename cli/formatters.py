"""
Output formatters for CLI commands.

Every command returns a consistent envelope:
{
  "command": str,
  "args": dict,
  "timestamp": ISO-8601,
  "data": {...},
  "metadata": {"data_freshness": str, "cache_hits": int, "cache_misses": int}
}
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from tabulate import tabulate


# ── Envelope builder ───────────────────────────────────────────────────────────

def build_envelope(
    command: str,
    args: dict,
    data: Any,
    data_freshness: Optional[str] = None,
) -> dict:
    return {
        "command": command,
        "args": args,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": data,
        "metadata": {
            "data_freshness": data_freshness,
        },
    }


# ── JSON output ────────────────────────────────────────────────────────────────

def print_json(envelope: dict) -> None:
    json.dump(envelope, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


# ── Table helpers ──────────────────────────────────────────────────────────────

def _pct(v) -> str:
    if v is None:
        return "N/A"
    return f"{v * 100:+.2f}%"


def _pct_plain(v) -> str:
    if v is None:
        return "N/A"
    return f"{v * 100:.2f}%"


def _f2(v) -> str:
    if v is None:
        return "N/A"
    return f"{v:.4f}"


def _to_sector_map(sectors: list) -> dict:
    """Return {sector: weight} for sectors with weight > 0.001, preserving sort order."""
    return {s["sector"]: s["weight"] for s in sectors if s.get("weight", 0) > 0.001}


def _money(v) -> str:
    if v is None:
        return "N/A"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


# ── Screen table ───────────────────────────────────────────────────────────────

def print_screen_table(data: dict) -> None:
    ticker = data["candidate_ticker"]
    portfolio = data["portfolio"]
    info = data.get("etf_info", {})
    metrics = data.get("risk_metrics", {})
    trailing = data.get("trailing_returns", {})
    overlap = data.get("overlap", {})
    conc = data.get("effective_concentration", {})
    correlations = data.get("correlations_to_portfolio", {})

    print(f"\n{'='*60}")
    print(f"  SCREENER: {ticker}  vs  portfolio={portfolio}")
    print(f"{'='*60}")

    # ETF info
    print(f"\n  {info.get('name', ticker)}")
    print(f"  Fund Family : {info.get('fund_family') or 'N/A'}")
    print(f"  Category    : {info.get('category') or 'N/A'}")
    print(f"  AUM         : {_money(info.get('aum'))}")
    print(f"  Expense     : {_pct_plain(info.get('expense_ratio'))}")
    print(f"  Div. Yield  : {_pct_plain(info.get('dividend_yield'))}")

    # Trailing returns
    print(f"\n  Trailing Returns")
    windows = ["1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y"]
    ret_row = [_pct(trailing.get(w)) for w in windows]
    print("  " + tabulate([ret_row], headers=windows, tablefmt="simple"))

    # Risk metrics
    print(f"\n  Risk Metrics")
    rm_rows = [
        ["Ann. Return",     _pct(metrics.get("annualized_return"))],
        ["Ann. Volatility", _pct(metrics.get("annualized_volatility"))],
        ["Sharpe Ratio",    _f2(metrics.get("sharpe_ratio"))],
        ["Sortino Ratio",   _f2(metrics.get("sortino_ratio"))],
        ["Max Drawdown",    _pct(metrics.get("max_drawdown"))],
        ["Calmar Ratio",    _f2(metrics.get("calmar_ratio"))],
        ["Beta vs VOO",     _f2(metrics.get("beta"))],
        ["Corr vs VOO",     _f2(metrics.get("correlation_to_benchmark"))],
    ]
    print("  " + tabulate(rm_rows, tablefmt="simple"))

    # Correlations to portfolio
    print(f"\n  Correlation to Portfolio Positions")
    corr_rows = [[t, _f2(v)] for t, v in correlations.items()]
    print("  " + tabulate(corr_rows, headers=["Ticker", "Correlation"], tablefmt="simple"))

    # Overlap
    oc = overlap.get("overlap_coefficient", 0)
    shared = overlap.get("shared_holdings", [])
    print(f"\n  Holdings Overlap  (coefficient: {oc:.1%})")
    if shared:
        ov_rows = [
            [h["symbol"], _pct_plain(h["candidate_weight"]),
             _pct_plain(h["portfolio_weight"]), _pct_plain(h["overlap_contribution"])]
            for h in shared[:8]
        ]
        print("  " + tabulate(
            ov_rows,
            headers=["Symbol", "Cand. Wt", "Port. Wt", "Overlap"],
            tablefmt="simple",
        ))
    else:
        print("  No shared holdings found.")

    # Effective concentration
    alloc = conc.get("candidate_allocation", 0.05)
    max_ss = conc.get("max_single_stock", 0)
    max_sym = conc.get("max_single_stock_symbol", "")
    print(f"\n  Effective Concentration if added at {alloc:.0%}")
    print(f"  Max single-stock: {max_sym} @ {max_ss:.2%}")
    top = conc.get("top_holdings", [])
    if top:
        tc_rows = [[h["symbol"], _pct_plain(h["effective_weight"]), h.get("name", "")]
                   for h in top[:10]]
        print("  " + tabulate(
            tc_rows,
            headers=["Symbol", "Eff. Weight", "Name"],
            tablefmt="simple",
        ))

    # Top candidate holdings
    print(f"\n  Top Holdings — {ticker}")
    th = data.get("top_holdings", [])
    if th:
        th_rows = [[h["symbol"], _pct_plain(h["weight"]), h.get("name", "")] for h in th[:10]]
        print("  " + tabulate(th_rows, headers=["Symbol", "Weight", "Name"], tablefmt="simple"))

    # Sector breakdown
    sw = _to_sector_map(data.get("sectors", []))
    if sw:
        print(f"\n  Sector Breakdown — {ticker}")
        sec_rows = [[sec, _pct_plain(w)] for sec, w in list(sw.items())[:8]]
        print("  " + tabulate(sec_rows, headers=["Sector", "Weight"], tablefmt="simple"))

    freshness = data.get("data_freshness")
    if freshness:
        print(f"\n  Data as of: {freshness}")
    print()


# ── Compare table ──────────────────────────────────────────────────────────────

def print_compare_table(data: dict) -> None:
    a = data["ticker_a"]
    b = data["ticker_b"]

    def _td(ticker):
        return data.get(ticker, {})

    def _m(ticker, key):
        return _td(ticker).get("risk_metrics", {}).get(key)

    def _t(ticker, key):
        return _td(ticker).get("trailing_returns", {}).get(key)

    def _i(ticker, key):
        return _td(ticker).get("etf_info", {}).get(key)

    print(f"\n{'='*60}")
    print(f"  COMPARE: {a}  vs  {b}")
    print(f"{'='*60}")

    rows = [
        ["Name",           _i(a, "name") or a,          _i(b, "name") or b],
        ["AUM",            _money(_i(a, "aum")),         _money(_i(b, "aum"))],
        ["Expense Ratio",  _pct_plain(_i(a, "expense_ratio")), _pct_plain(_i(b, "expense_ratio"))],
        ["Div. Yield",     _pct_plain(_i(a, "dividend_yield")), _pct_plain(_i(b, "dividend_yield"))],
        ["---", "---", "---"],
        ["1M Return",      _pct(_t(a, "1M")),  _pct(_t(b, "1M"))],
        ["3M Return",      _pct(_t(a, "3M")),  _pct(_t(b, "3M"))],
        ["6M Return",      _pct(_t(a, "6M")),  _pct(_t(b, "6M"))],
        ["YTD Return",     _pct(_t(a, "YTD")), _pct(_t(b, "YTD"))],
        ["1Y Return",      _pct(_t(a, "1Y")),  _pct(_t(b, "1Y"))],
        ["3Y Return",      _pct(_t(a, "3Y")),  _pct(_t(b, "3Y"))],
        ["5Y Return",      _pct(_t(a, "5Y")),  _pct(_t(b, "5Y"))],
        ["---", "---", "---"],
        ["Ann. Return",    _pct(_m(a, "annualized_return")),    _pct(_m(b, "annualized_return"))],
        ["Volatility",     _pct(_m(a, "annualized_volatility")), _pct(_m(b, "annualized_volatility"))],
        ["Sharpe",         _f2(_m(a, "sharpe_ratio")),  _f2(_m(b, "sharpe_ratio"))],
        ["Sortino",        _f2(_m(a, "sortino_ratio")), _f2(_m(b, "sortino_ratio"))],
        ["Max Drawdown",   _pct(_m(a, "max_drawdown")), _pct(_m(b, "max_drawdown"))],
        ["Beta vs VOO",    _f2(_m(a, "beta")),           _f2(_m(b, "beta"))],
    ]
    print(tabulate(rows, headers=["Metric", a, b], tablefmt="simple"))

    print(f"\n  Correlation between {a} and {b}: {_f2(data.get('correlation'))}")
    print(f"  Holdings overlap coefficient: {data.get('cross_overlap_coefficient', 0):.1%}")
    print(f"  Shared holdings: {data.get('shared_holdings_count', 0)}")

    # Commodity context — shown when either ticker is an energy or gold-miner ETF
    for ticker in [a, b]:
        ctx = data.get(ticker, {}).get("commodity_context")
        if ctx:
            print(f"\n  {ticker} — Commodity Context ({ctx['commodity']})")
            cc_rows = [
                ["Beta to commodity",    _f2(ctx.get("beta_to_commodity"))],
                ["Correlation 1Y",       _f2(ctx.get("correlation_1y"))],
                ["Correlation (full)",   _f2(ctx.get("correlation_full"))],
            ]
            print("  " + tabulate(cc_rows, tablefmt="simple"))

    # Sector breakdown — side-by-side for both tickers
    wa = _to_sector_map(data.get(a, {}).get("sectors", []))
    wb = _to_sector_map(data.get(b, {}).get("sectors", []))
    if wa or wb:
        sec_rows = [
            [sec, _pct_plain(wa[sec]) if sec in wa else "-",
                  _pct_plain(wb[sec]) if sec in wb else "-"]
            for sec in sorted(wa.keys() | wb.keys())
        ]
        print(f"\n  Sector Breakdown")
        print("  " + tabulate(sec_rows, headers=["Sector", a, b], tablefmt="simple"))

    freshness = data.get("data_freshness")
    if freshness:
        print(f"\n  Data as of: {freshness}")
    print()


def print_compare_multi_table(data: dict) -> None:
    tickers = data["tickers"]

    def _m(t, key):
        return data["metrics"].get(t, {}).get(key)

    def _tr(t, key):
        return data["trailing_returns"].get(t, {}).get(key)

    def _i(t, key):
        return data["etf_info"].get(t, {}).get(key)

    width = max(60, 20 + 12 * len(tickers))
    print(f"\n{'='*width}")
    print(f"  COMPARE: {' | '.join(tickers)}")
    print(f"{'='*width}")

    rows = [
        ["AUM"]           + [_money(_i(t, "aum")) for t in tickers],
        ["Expense Ratio"] + [_pct_plain(_i(t, "expense_ratio")) for t in tickers],
        ["Div. Yield"]    + [_pct_plain(_i(t, "dividend_yield")) for t in tickers],
        ["---"]           + ["---"] * len(tickers),
        ["1Y Return"]     + [_pct(_tr(t, "1Y")) for t in tickers],
        ["3Y Return"]     + [_pct(_tr(t, "3Y")) for t in tickers],
        ["5Y Return"]     + [_pct(_tr(t, "5Y")) for t in tickers],
        ["---"]           + ["---"] * len(tickers),
        ["Ann. Return"]   + [_pct(_m(t, "annualized_return")) for t in tickers],
        ["Volatility"]    + [_pct(_m(t, "annualized_volatility")) for t in tickers],
        ["Sharpe"]        + [_f2(_m(t, "sharpe_ratio")) for t in tickers],
        ["Sortino"]       + [_f2(_m(t, "sortino_ratio")) for t in tickers],
        ["Max Drawdown"]  + [_pct(_m(t, "max_drawdown")) for t in tickers],
        ["Beta vs VOO"]   + [_f2(_m(t, "beta")) for t in tickers],
    ]
    print(tabulate(rows, headers=["Metric"] + tickers, tablefmt="simple"))

    # Correlation matrix
    corr = data.get("correlation_matrix", {})
    matrix = corr.get("matrix", {})
    if matrix:
        print(f"\n  Correlation Matrix")
        corr_rows = [
            [t1] + [_f2(matrix.get(t1, {}).get(t2)) for t2 in tickers]
            for t1 in tickers
        ]
        print("  " + tabulate(corr_rows, headers=[""] + tickers, tablefmt="simple"))

    # Commodity context for any relevant tickers
    for t in tickers:
        ctx = data.get("commodity_context", {}).get(t)
        if ctx:
            print(f"\n  {t} — Commodity Context ({ctx['commodity']})")
            cc_rows = [
                ["Beta to commodity", _f2(ctx.get("beta_to_commodity"))],
                ["Correlation 1Y",    _f2(ctx.get("correlation_1y"))],
                ["Correlation (full)",_f2(ctx.get("correlation_full"))],
            ]
            print("  " + tabulate(cc_rows, tablefmt="simple"))

    # Sector breakdown matrix
    sectors_map = data.get("sectors", {})
    if sectors_map:
        sec_weights = {t: _to_sector_map(secs) for t, secs in sectors_map.items()}
        all_secs = sorted(set(sec for w in sec_weights.values() for sec in w))
        if all_secs:
            sec_rows = [
                [sec] + [
                    _pct_plain(sec_weights.get(t, {}).get(sec)) if sec in sec_weights.get(t, {}) else "-"
                    for t in tickers
                ]
                for sec in all_secs
            ]
            print(f"\n  Sector Breakdown")
            print("  " + tabulate(sec_rows, headers=["Sector"] + tickers, tablefmt="simple"))

    freshness = data.get("data_freshness")
    if freshness:
        print(f"\n  Data as of: {freshness}")
    print()


# ── Holdings table ─────────────────────────────────────────────────────────────

def print_backtest_table(data: dict) -> None:
    a = data["portfolio_a"]
    b = data["portfolio_b"]
    bm_short = data.get("benchmark", "VOO")   # "VOO" or "SPX"
    bm_label = f"benchmark_{bm_short}"
    has_bm = bm_label in data

    print(f"\n{'='*65}")
    print(f"  BACKTEST: {a}  vs  {b}  ({data.get('actual_start')} → {data.get('actual_end')})")
    print(f"{'='*65}")

    def _m(key, portfolio):
        return data.get(portfolio, {}).get("metrics", {}).get(key)

    def _bm(key):
        return data.get(bm_label, {}).get("metrics", {}).get(key)

    bm_header = [bm_short] if has_bm else []
    bm_col = lambda key: [_pct(_bm(key))] if has_bm else []

    rows = [
        ["Total Return",
         _pct(_m("total_return", a)), _pct(_m("total_return", b))] + bm_col("total_return"),
        ["Ann. Return",
         _pct(_m("annualized_return", a)), _pct(_m("annualized_return", b))] + bm_col("annualized_return"),
        ["Ann. Volatility",
         _pct(_m("annualized_volatility", a)), _pct(_m("annualized_volatility", b))] + bm_col("annualized_volatility"),
        ["Sharpe Ratio",
         _f2(_m("sharpe_ratio", a)), _f2(_m("sharpe_ratio", b))] + ([_f2(_bm("sharpe_ratio"))] if has_bm else []),
        ["Sortino Ratio",
         _f2(_m("sortino_ratio", a)), _f2(_m("sortino_ratio", b))] + ([_f2(_bm("sortino_ratio"))] if has_bm else []),
        ["Max Drawdown",
         _pct(_m("max_drawdown", a)), _pct(_m("max_drawdown", b))] + bm_col("max_drawdown"),
        ["Calmar Ratio",
         _f2(_m("calmar_ratio", a)), _f2(_m("calmar_ratio", b))] + ([_f2(_bm("calmar_ratio"))] if has_bm else []),
    ]
    print(tabulate(rows, headers=["Metric", a, b] + bm_header, tablefmt="simple"))

    # Calendar year returns
    years_a = data.get(a, {}).get("calendar_year_returns", {})
    years_b = data.get(b, {}).get("calendar_year_returns", {})
    years_bm = data.get(bm_label, {}).get("calendar_year_returns", {}) if has_bm else {}
    all_years = sorted(set(years_a) | set(years_b) | set(years_bm))

    if all_years:
        print(f"\n  Calendar Year Returns")
        yr_rows = []
        for yr in all_years:
            row = [yr, _pct(years_a.get(yr)), _pct(years_b.get(yr))]
            if has_bm:
                row.append(_pct(years_bm.get(yr)))
            yr_rows.append(row)
        print("  " + tabulate(yr_rows, headers=["Year", a, b] + bm_header, tablefmt="simple"))

    # Summary
    summary = data.get("summary", {})
    if summary:
        print(f"\n  Summary")
        print(f"  Best return : {summary.get('winner_return')}")
        print(f"  Best Sharpe : {summary.get('winner_sharpe')}")
        print(f"  Less drawdown: {summary.get('winner_drawdown')}")
        if has_bm:
            print(f"  {a} vs {bm_short}  : {_pct(summary.get('a_vs_benchmark'))} / yr")
            print(f"  {b} vs {bm_short}  : {_pct(summary.get('b_vs_benchmark'))} / yr")
    print()


def print_analytics_table(data: dict) -> None:
    portfolio = data.get("portfolio", "")
    port_m = data.get("portfolio_metrics", {})
    positions = data.get("position_metrics", [])
    themes = data.get("theme_attribution", [])
    top_stocks = data.get("top_stock_exposures", [])

    print(f"\n{'='*75}")
    print(f"  ANALYTICS: portfolio={portfolio}")
    print(f"{'='*75}")

    # Portfolio-level summary
    if port_m:
        print(f"\n  Portfolio Summary (5Y)")
        pm_rows = [
            ["Ann. Return",    _pct(port_m.get("annualized_return"))],
            ["Ann. Volatility",_pct(port_m.get("annualized_volatility"))],
            ["Sharpe Ratio",   _f2(port_m.get("sharpe_ratio"))],
            ["Sortino Ratio",  _f2(port_m.get("sortino_ratio"))],
            ["Max Drawdown",   _pct(port_m.get("max_drawdown"))],
            ["Beta vs VOO",    _f2(port_m.get("beta"))],
            ["Corr vs VOO",    _f2(port_m.get("correlation_to_benchmark"))],
        ]
        print("  " + tabulate(pm_rows, tablefmt="simple"))

    # Per-position table
    if positions:
        print(f"\n  Per-Position Metrics  (trailing 1Y | rolling windows)")
        hdr = ["Ticker", "Wt", "Theme", "1Y Ret", "Ann Vol", "Sharpe", "Beta",
               "30d Vol", "90d Vol", "1Y Vol"]
        pos_rows = []
        for p in positions:
            if "error" in p:
                pos_rows.append([p["ticker"], _pct_plain(p["weight"]), p.get("theme",""),
                                  "ERR","","","","","",""])
                continue
            trailing = p.get("trailing_returns", {})
            metrics = p.get("metrics", {})
            rolling = p.get("rolling", {})
            pos_rows.append([
                p["ticker"],
                _pct_plain(p["weight"]),
                p.get("theme", ""),
                _pct(trailing.get("1Y")),
                _pct(metrics.get("annualized_volatility")),
                _f2(metrics.get("sharpe_ratio")),
                _f2(metrics.get("beta")),
                _pct(rolling.get("30d", {}).get("annualized_volatility")),
                _pct(rolling.get("90d", {}).get("annualized_volatility")),
                _pct(rolling.get("1Y",  {}).get("annualized_volatility")),
            ])
        print("  " + tabulate(pos_rows, headers=hdr, tablefmt="simple"))

    # Theme attribution
    if themes:
        print(f"\n  Theme Attribution  (trailing 1Y)")
        th_rows = [
            [t["theme"],
             _pct_plain(t["theme_weight"]),
             _pct(t["theme_return"]),
             _pct(t["portfolio_contribution"]),
             ", ".join(t["tickers"])]
            for t in themes
        ]
        print("  " + tabulate(
            th_rows,
            headers=["Theme", "Wt", "Theme Ret", "Port Contrib", "Tickers"],
            tablefmt="simple",
        ))

    # Top stock exposures
    if top_stocks:
        print(f"\n  Top Stock Exposures (effective weight across all ETFs)")
        st_rows = [
            [i+1, h["symbol"], _pct_plain(h["effective_weight"]), h.get("name", "")]
            for i, h in enumerate(top_stocks[:15])
        ]
        print("  " + tabulate(
            st_rows, headers=["#", "Symbol", "Eff. Wt", "Name"], tablefmt="simple"
        ))

    freshness = data.get("data_freshness")
    if freshness:
        print(f"\n  Data as of: {freshness}")
    print()


def print_correlation_table(data: dict) -> None:
    portfolio = data.get("portfolio", "")
    cm = data.get("correlation_matrix", {})
    tickers = cm.get("tickers", [])
    rows = cm.get("rows", [])

    print(f"\n{'='*60}")
    print(f"  CORRELATION MATRIX: portfolio={portfolio}")
    print(f"{'='*60}")

    if not tickers or not rows:
        print("  No data.")
        print()
        return

    table_rows = []
    for i, ticker in enumerate(tickers):
        row_vals = []
        for j, val in enumerate(rows[i]):
            if val is None:
                row_vals.append("N/A")
            elif i == j:
                row_vals.append("1.000")
            else:
                row_vals.append(f"{val:.3f}")
        table_rows.append([ticker] + row_vals)

    print(tabulate(table_rows, headers=[""] + tickers, tablefmt="simple"))

    freshness = data.get("data_freshness")
    if freshness:
        print(f"\n  Data as of: {freshness}")
    print()


def print_holdings_table(data: dict) -> None:
    portfolio = data.get("portfolio", "")
    holdings = data.get("holdings", [])

    print(f"\n{'='*55}")
    print(f"  HOLDINGS: portfolio={portfolio}")
    print(f"{'='*55}")
    rows = [
        [i + 1, h["symbol"], _pct_plain(h["effective_weight"]), h.get("name", "")]
        for i, h in enumerate(holdings)
    ]
    print(tabulate(rows, headers=["#", "Symbol", "Eff. Weight", "Name"], tablefmt="simple"))
    print()


# ── Optimize table ─────────────────────────────────────────────────────────────

_OBJECTIVE_LABELS = {
    "sharpe":            "Maximize Sharpe Ratio",
    "sortino":           "Maximize Sortino Ratio",
    "min-vol":           "Minimize Volatility",
    "min-cvar":          "Minimize CVaR",
    "max-return":        "Maximize Return",
    "quadratic-utility": "Maximize Quadratic Utility",
    "omega":             "Maximize Omega Ratio",
}

# Which metrics key each objective directly optimizes (marked with * in output)
_OBJECTIVE_METRIC = {
    "sharpe":            "sharpe_ratio",
    "sortino":           "sortino_ratio",
    "min-vol":           "annualized_volatility",
    "max-return":        "annualized_return",
    "quadratic-utility": "annualized_return",  # proxy
    "omega":             "omega_ratio",
}


def print_optimize_table(data: dict) -> None:
    objective    = data.get("objective", "")
    tickers      = data.get("tickers", [])
    period       = data.get("period", {})
    params       = data.get("parameters", {})
    opt_weights  = data.get("optimal_weights", {})
    cur_weights  = data.get("current_weights")
    weight_chg   = data.get("weight_changes", {})
    metrics      = data.get("metrics", {})
    opt_m        = metrics.get("optimal", {})
    cur_m        = metrics.get("current")
    opt_info     = data.get("optimization", {})
    portfolio_lbl = data.get("portfolio_label") or "custom tickers"

    confidence = params.get("confidence", 0.95)
    cvar_key   = f"cvar_{int(confidence * 100)}"
    target_metric = _OBJECTIVE_METRIC.get(objective, cvar_key if objective == "min-cvar" else "")

    label = _OBJECTIVE_LABELS.get(objective, objective)
    title_line = f"  OPTIMIZE: {label}  |  portfolio: {portfolio_lbl}"
    period_line = f"  Period: {period.get('start')} → {period.get('end')}  |  {period.get('trading_days')} trading days"

    width = max(len(title_line), len(period_line)) + 2
    print(f"\n{'='*width}")
    print(title_line)
    print(period_line)
    print(f"{'='*width}")

    # ── Weight allocation ──────────────────────────────────────────────────────
    print(f"\n  Weight Allocation")
    per_max = params.get("per_max", {})
    per_min = params.get("per_min", {})
    has_per_constraints = bool(per_max or per_min)
    has_current = cur_weights is not None

    def _cap_str(t):
        parts = []
        if t in per_max:
            parts.append(f"≤{per_max[t]*100:.0f}%")
        if t in per_min:
            parts.append(f"≥{per_min[t]*100:.0f}%")
        return " ".join(parts) if parts else ""

    if has_current:
        w_rows = []
        for t in tickers:
            cur = cur_weights.get(t, 0.0)
            opt = opt_weights.get(t, 0.0)
            delta = weight_chg.get(t, 0.0)
            row = [t, f"{cur * 100:.1f}%", f"{opt * 100:.1f}%", f"{delta * 100:+.1f}pp"]
            if has_per_constraints:
                row.append(_cap_str(t))
            w_rows.append(row)
        headers = ["Ticker", "Current", "Optimal", "Δ"]
        if has_per_constraints:
            headers.append("Cap")
        print("  " + tabulate(w_rows, headers=headers, tablefmt="simple"))
    else:
        w_rows = sorted(
            [[t, f"{opt_weights.get(t, 0.0) * 100:.1f}%"] for t in tickers],
            key=lambda r: -(opt_weights.get(r[0], 0.0) or 0.0),
        )
        if has_per_constraints:
            w_rows = [[r[0], r[1], _cap_str(r[0])] for r in w_rows]
            print("  " + tabulate(w_rows, headers=["Ticker", "Optimal", "Cap"], tablefmt="simple"))
        else:
            print("  " + tabulate(w_rows, headers=["Ticker", "Optimal"], tablefmt="simple"))

    # ── Metrics comparison ─────────────────────────────────────────────────────
    def _fmt_metric(key, v):
        if v is None:
            return "N/A"
        if key in ("annualized_return", "annualized_volatility", "max_drawdown", cvar_key):
            return f"{v * 100:+.2f}%" if key in ("max_drawdown", cvar_key) else f"{v * 100:.2f}%"
        return f"{v:.4f}"

    def _delta_metric(key, opt_v, cur_v):
        if opt_v is None or cur_v is None:
            return "N/A"
        diff = opt_v - cur_v
        if key in ("annualized_return", "annualized_volatility", "max_drawdown", cvar_key):
            return f"{diff * 100:+.2f}pp"
        return f"{diff:+.4f}"

    metric_defs = [
        ("annualized_return",    "Ann. Return"),
        ("annualized_volatility","Ann. Volatility"),
        ("sharpe_ratio",         "Sharpe Ratio"),
        ("sortino_ratio",        "Sortino Ratio"),
        ("max_drawdown",         "Max Drawdown"),
        ("calmar_ratio",         "Calmar Ratio"),
        (cvar_key,               f"CVaR ({int(confidence*100)}%)"),
        ("omega_ratio",          "Omega Ratio"),
    ]

    print(f"\n  Portfolio Metrics")
    if has_current and cur_m:
        m_rows = []
        for key, label_m in metric_defs:
            marker = " *" if key == target_metric else ""
            m_rows.append([
                label_m + marker,
                _fmt_metric(key, cur_m.get(key)),
                _fmt_metric(key, opt_m.get(key)),
                _delta_metric(key, opt_m.get(key), cur_m.get(key)),
            ])
        print("  " + tabulate(m_rows, headers=["Metric", "Current", "Optimal", "Δ"], tablefmt="simple"))
        print("  (* = optimized metric)")
    else:
        m_rows = [
            [label_m + (" *" if key == target_metric else ""), _fmt_metric(key, opt_m.get(key))]
            for key, label_m in metric_defs
        ]
        print("  " + tabulate(m_rows, headers=["Metric", "Optimal"], tablefmt="simple"))
        print("  (* = optimized metric)")

    # ── Optimization diagnostics ───────────────────────────────────────────────
    status = "converged" if opt_info.get("success") else "did not converge"
    iters  = opt_info.get("iterations", "?")
    print(f"\n  Optimization: {status}  ({iters} iterations)")
    if not opt_info.get("success"):
        print(f"  Warning: {opt_info.get('message', '')}")

    print()
