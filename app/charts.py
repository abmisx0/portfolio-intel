"""
Plotly figure builders — return JSON strings for embedding in templates.

All charts use a consistent dark theme matching style.css.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Optional

import pandas as pd


_DARK_LAYOUT = {
    "paper_bgcolor": "#12141b",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"color": "#b9bdcc", "family": "'JetBrains Mono', monospace", "size": 12},
    "margin": {"l": 60, "r": 20, "t": 40, "b": 60},
    "hoverlabel": {"bgcolor": "#1a1e2b", "bordercolor": "#232838", "font": {"color": "#e8eaf2"}},
}

_GREEN  = "#34d399"
_RED    = "#f87171"
_BLUE   = "#6c8cff"
_GOLD   = "#fbbf24"
_PURPLE = "#a78bfa"
_TEAL   = "#2dd4bf"
_GREY   = "#7e8497"
_ORANGE = "#fb923c"

_LINE_COLOURS = [_BLUE, _GOLD, _GREEN, _RED, _PURPLE, _TEAL, _ORANGE]

_RESAMPLE_RULES = {
    "Daily":     None,
    "Weekly":    "W-FRI",
    "Monthly":   "ME",
    "Quarterly": "QE",
    "Yearly":    "YE",
}


def _chart_periods(latest: "pd.Timestamp") -> list:
    return [
        ("ALL", None),
        ("10Y", latest - pd.DateOffset(years=10)),
        ("5Y",  latest - pd.DateOffset(years=5)),
        ("3Y",  latest - pd.DateOffset(years=3)),
        ("1Y",  latest - pd.DateOffset(years=1)),
        ("YTD", pd.Timestamp(f"{latest.year}-01-01")),
        ("6M",  latest - pd.DateOffset(months=6)),
        ("3M",  latest - pd.DateOffset(months=3)),
        ("1M",  latest - pd.DateOffset(months=1)),
    ]


def _normalize_slice(s: "pd.Series", common_start, start, rule) -> tuple:
    effective_start = common_start if start is None else max(common_start, start)
    s = s[s.index >= effective_start]
    if len(s) < 2:
        return [], []
    if rule:
        s = s.resample(rule).last().dropna()
        if len(s) < 2:
            return [], []
    return _series_xy((s / s.iloc[0] - 1) * 100)


def _end_label_annotation(ticker: str, color: str, last_val: float) -> dict:
    # Two-line label (name over value) keeps long portfolio names like
    # "checkup_target" inside the right margin instead of clipping.
    sign = "+" if last_val >= 0 else ""
    return {
        "x": 1.01, "y": last_val, "xref": "paper", "yref": "y",
        "text": f"<b>{ticker}</b><br>{sign}{last_val:.1f}%",
        "showarrow": False, "xanchor": "left", "yanchor": "middle",
        "align": "left",
        "font": {"color": color, "size": 11},
    }


def _series_xy(s) -> tuple[list, list]:
    """Convert a pandas Series to (x_dates, y_values) for Plotly."""
    return [str(d)[:10] for d in s.index], [round(float(v), 2) for v in s.values]


def _align_benchmark(port_price, benchmark):
    """Reindex benchmark to portfolio index and drop leading NaNs."""
    if benchmark is None or benchmark.empty:
        return None
    aligned = benchmark.reindex(port_price.index, method="ffill").dropna()
    return aligned if not aligned.empty else None


def _safe_json(obj) -> str:
    """JSON serialise, replacing NaN/Inf with null."""
    def default(o):
        if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
            return None
        raise TypeError
    return json.dumps(obj, default=default)


# ── Backtest cumulative returns chart ─────────────────────────────────────────

def backtest_chart(data: dict) -> str:
    """
    Line chart: cumulative returns for portfolio_a, portfolio_b, and VOO.
    Values expressed as growth of $100.
    """
    a = data.get("portfolio_a", "A")
    b = data.get("portfolio_b", "B")
    traces = []

    def _make_trace(key: str, label: str, colour: str):
        series = data.get(key, {}).get("cumulative_series", [])
        if not series:
            return
        x = [pt["date"] for pt in series]
        y = [round(pt["value"] * 100, 2) for pt in series]
        traces.append({
            "type": "scatter",
            "mode": "lines",
            "name": label,
            "x": x,
            "y": y,
            "line": {"color": colour, "width": 2},
            "hovertemplate": "%{x}<br>$%{y:.2f}<extra></extra>",
        })

    _make_trace(a, a, _BLUE)
    _make_trace(b, b, _GOLD)
    _make_trace("benchmark_VOO", "VOO", _GREY)

    layout = {
        **_DARK_LAYOUT,
        "title": {"text": "Growth of $100", "font": {"size": 13}},
        "xaxis": {"gridcolor": "#1a1e2b", "showgrid": True},
        "yaxis": {"gridcolor": "#1a1e2b", "showgrid": True,
                  "tickformat": "$,.0f", "title": "Value ($)"},
        "legend": {"bgcolor": "#12141b", "bordercolor": "#232838"},
        "hovermode": "x unified",
    }

    return _safe_json({"data": traces, "layout": layout})


# ── Correlation heatmap ────────────────────────────────────────────────────────

def correlation_heatmap(matrix_data: dict) -> str:
    tickers = matrix_data.get("tickers", [])
    rows    = matrix_data.get("rows", [])

    if not tickers:
        return _safe_json({"data": [], "layout": _DARK_LAYOUT})

    # Plotly renders rows bottom-up; reverse for natural read order
    z      = [row[:] for row in rows]
    y_labs = tickers[:]

    text = [[f"{v:.2f}" if v is not None else "" for v in row] for row in z]

    trace = {
        "type": "heatmap",
        "z": z,
        "x": tickers,
        "y": y_labs,
        "text": text,
        "texttemplate": "%{text}",
        "colorscale": [
            [0.0,  "#f87171"],   # -1  red
            [0.5,  "#12141b"],   #  0  neutral (surface)
            [1.0,  "#6c8cff"],   # +1  blue
        ],
        "zmin": -1, "zmax": 1,
        "showscale": True,
        "colorbar": {"tickfont": {"color": "#b9bdcc"}},
        "hovertemplate": "%{y} / %{x}<br>corr = %{z:.3f}<extra></extra>",
    }

    n = len(tickers)
    px_per_cell = 55
    size = max(300, n * px_per_cell)

    layout = {
        **_DARK_LAYOUT,
        "title": {"text": "Correlation Matrix (daily returns)", "font": {"size": 13}},
        "width": size + 120,
        "height": size + 80,
        "margin": {"l": 80, "r": 20, "t": 50, "b": 80},
        "xaxis": {"tickangle": -30},
    }

    return _safe_json({"data": [trace], "layout": layout})


# ── Theme attribution bar chart ────────────────────────────────────────────────

def theme_bar_chart(themes: list) -> str:
    if not themes:
        return _safe_json({"data": [], "layout": _DARK_LAYOUT})

    labels  = [t["theme"] for t in themes]
    contrib = [round((t["portfolio_contribution"] or 0) * 100, 2) for t in themes]
    colours = [_GREEN if v >= 0 else _RED for v in contrib]

    trace = {
        "type": "bar",
        "orientation": "h",
        "x": contrib,
        "y": labels,
        "marker": {"color": colours},
        "hovertemplate": "%{y}<br>%{x:+.2f}%<extra></extra>",
    }

    max_label = max((len(t["theme"]) for t in themes), default=10)
    left_margin = max(90, max_label * 9)

    layout = {
        **_DARK_LAYOUT,
        "xaxis": {"gridcolor": "#1a1e2b", "ticksuffix": "%", "title": "Portfolio Contribution (%)"},
        "yaxis": {"autorange": "reversed"},
        "height": max(250, len(themes) * 44 + 60),
        "margin": {"l": left_margin, "r": 20, "t": 10, "b": 55},
    }

    return _safe_json({"data": [trace], "layout": layout})


# ── Position normalized performance chart ─────────────────────────────────────

def position_performance_chart(price_map: dict, positions: list) -> str:
    """Initial render of per-ETF normalized % return (ALL period, Daily resolution).
    Period/resolution/scale controls live in the template and call Plotly.update via
    data pre-computed by position_performance_data()."""
    if not price_map:
        return _safe_json({"data": [], "layout": _DARK_LAYOUT})

    tickers = [t for pos in positions if (t := pos["ticker"].upper()) in price_map]
    if not tickers:
        return _safe_json({"data": [], "layout": _DARK_LAYOUT})

    common_start = max(price_map[t].index[0] for t in tickers)

    traces = []
    annotations = []
    for i, ticker in enumerate(tickers):
        color = _LINE_COLOURS[i % len(_LINE_COLOURS)]
        s = price_map[ticker][price_map[ticker].index >= common_start]
        x, y = _series_xy((s / s.iloc[0] - 1) * 100) if len(s) >= 2 else ([], [])
        traces.append({
            "type": "scatter", "mode": "lines", "name": ticker,
            "x": x, "y": y,
            "line": {"color": color, "width": 1.5},
            "hovertemplate": f"<b>{ticker}</b><br>%{{x}}<br>%{{y:+.1f}}%<extra></extra>",
        })
        if y:
            annotations.append(_end_label_annotation(ticker, color, y[-1]))

    layout = {
        **_DARK_LAYOUT,
        "annotations": annotations,
        "xaxis": {"gridcolor": "#1a1e2b", "type": "date", "rangeslider": {"visible": False}},
        "yaxis": {"gridcolor": "#1a1e2b", "ticksuffix": "%",
                  "zeroline": True, "zerolinecolor": "#232838"},
        "hovermode": "closest",
        "showlegend": False,
        "height": 600,
        "margin": {"l": 60, "r": 140, "t": 20, "b": 40},
    }
    return _safe_json({"data": traces, "layout": layout})


def position_performance_data(price_map: dict, positions: list) -> str:
    """Pre-compute all (period × resolution) chart data for client-side switching.

    Returns JSON: {period: {resolution: {xs, ys, annotations}}}
    where xs/ys are lists-of-lists (one per ticker) and annotations are Plotly
    annotation objects with right-edge colored labels.
    """
    if not price_map:
        return _safe_json({})

    tickers = [t for pos in positions if (t := pos["ticker"].upper()) in price_map]
    if not tickers:
        return _safe_json({})

    latest = max(price_map[t].index[-1] for t in tickers)
    common_start = max(price_map[t].index[0] for t in tickers)

    result = {}
    for p_label, p_start in _chart_periods(latest):
        result[p_label] = {}
        for r_label, rule in _RESAMPLE_RULES.items():
            xs, ys, annotations = [], [], []
            for i, ticker in enumerate(tickers):
                x, y = _normalize_slice(price_map[ticker], common_start, p_start, rule)
                xs.append(x)
                ys.append(y)
                color = _LINE_COLOURS[i % len(_LINE_COLOURS)]
                if y:
                    annotations.append(_end_label_annotation(ticker, color, y[-1]))
            result[p_label][r_label] = {"xs": xs, "ys": ys, "annotations": annotations}

    return _safe_json(result)


# ── Portfolio comparison chart (normalized % vs SPX) ──────────────────────────

def portfolio_comparison_data(portfolio_series_map: dict, spx_series) -> str:
    """Pre-compute all (period × resolution) chart data for the portfolio comparison chart.

    portfolio_series_map: {portfolio_name: pd.Series of cumulative price (cumprod)}
    spx_series: pd.Series for ^SPX (cumulative price)

    Returns JSON: {period: {resolution: {name: {xs, ys}}}}
    where name is a portfolio key or "^SPX".
    """
    all_series: dict = {**portfolio_series_map}
    if spx_series is not None and not spx_series.empty:
        all_series["^SPX"] = spx_series

    if not all_series:
        return _safe_json({})

    latest = max(s.index[-1] for s in all_series.values())
    common_start = max(s.index[0] for s in all_series.values())

    result: dict = {}
    for p_label, p_start in _chart_periods(latest):
        result[p_label] = {}
        for r_label, rule in _RESAMPLE_RULES.items():
            result[p_label][r_label] = {}
            for name, s in all_series.items():
                x, y = _normalize_slice(s, common_start, p_start, rule)
                result[p_label][r_label][name] = {"xs": x, "ys": y}

    return _safe_json(result)


# ── Drawdown chart ─────────────────────────────────────────────────────────────

def drawdown_chart(port_price, benchmark, port_label: str = "Portfolio") -> str:
    """Underwater / drawdown from peak chart."""
    if port_price is None or port_price.empty:
        return _safe_json({"data": [], "layout": _DARK_LAYOUT})

    def _dd(s):
        roll = s.cummax()
        return (s - roll) / roll * 100

    port_dd = _dd(port_price)
    px, py = _series_xy(port_dd)
    traces = [{
        "type": "scatter", "mode": "lines", "name": port_label,
        "x": px, "y": py,
        "line": {"color": _BLUE, "width": 1.5},
        "fill": "tozeroy", "fillcolor": "rgba(108,140,255,0.12)",
        "hovertemplate": "%{x}<br>%{y:.2f}%<extra></extra>",
    }]

    bench = _align_benchmark(port_price, benchmark)
    if bench is not None:
        bench_dd = _dd(bench)
        bx, by = _series_xy(bench_dd)
        traces.append({
            "type": "scatter", "mode": "lines", "name": "VOO",
            "x": bx, "y": by,
            "line": {"color": _GREY, "width": 1.5, "dash": "dot"},
            "hovertemplate": "%{x}<br>%{y:.2f}%<extra></extra>",
        })

    layout = {
        **_DARK_LAYOUT,
        "xaxis": {"gridcolor": "#1a1e2b"},
        "yaxis": {"gridcolor": "#1a1e2b", "ticksuffix": "%"},
        "hovermode": "x unified",
        "height": 280,
        "margin": {"l": 60, "r": 20, "t": 20, "b": 40},
    }
    return _safe_json({"data": traces, "layout": layout})


# ── Risk / return scatter ──────────────────────────────────────────────────────

def risk_return_scatter(positions: list, position_metrics: list) -> str:
    """Bubble chart: volatility (x) vs 1Y return (y), sized by portfolio weight."""
    if not positions or not position_metrics:
        return _safe_json({"data": [], "layout": _DARK_LAYOUT})

    pm_lookup = {pm.get("ticker", ""): pm for pm in position_metrics if "error" not in pm}

    x_vals, y_vals, sizes, labels, hover = [], [], [], [], []
    for pos in positions:
        ticker = pos["ticker"].upper()
        pm = pm_lookup.get(ticker, {})
        m  = pm.get("metrics", {})
        tr = pm.get("trailing_returns", {})
        vol = m.get("annualized_volatility")
        ret = tr.get("1Y")
        if vol is None or ret is None:
            continue
        sharpe = m.get("sharpe_ratio")
        x_vals.append(round(vol * 100, 2))
        y_vals.append(round(ret * 100, 2))
        sizes.append(max(14, pos["weight"] * 150))
        labels.append(ticker)
        hover.append(
            f"<b>{ticker}</b><br>1Y: {ret*100:+.1f}%<br>"
            f"Vol: {vol*100:.1f}%<br>Weight: {pos['weight']*100:.0f}%"
            + (f"<br>Sharpe: {sharpe:.2f}" if sharpe else "")
        )

    trace = {
        "type": "scatter", "mode": "markers",
        "x": x_vals, "y": y_vals,
        "marker": {"size": sizes, "color": _BLUE, "opacity": 0.72,
                   "line": {"color": "#6c8cff", "width": 1.5}},
        "hovertext": hover,
        "hovertemplate": "%{hovertext}<extra></extra>",
    }

    layout = {
        **_DARK_LAYOUT,
        "xaxis": {"gridcolor": "#1a1e2b", "title": "Volatility (%)", "ticksuffix": "%"},
        "yaxis": {"gridcolor": "#1a1e2b", "title": "1Y Return (%)", "ticksuffix": "%"},
        "showlegend": False,
        "height": 300,
        "margin": {"l": 60, "r": 20, "t": 20, "b": 60},
    }
    return _safe_json({"data": [trace], "layout": layout})


# ── Trailing returns grouped bar ───────────────────────────────────────────────

def trailing_returns_bar(position_metrics: list) -> str:
    """Grouped bar: 1Y / 3Y / 5Y trailing returns per position."""
    if not position_metrics:
        return _safe_json({"data": [], "layout": _DARK_LAYOUT})

    tickers, r1y, r3y, r5y = [], [], [], []
    for pm in position_metrics:
        if "error" in pm:
            continue
        tr = pm.get("trailing_returns", {})
        tickers.append(pm.get("ticker", ""))
        r1y.append(round((tr.get("1Y") or 0) * 100, 2))
        r3y.append(round((tr.get("3Y") or 0) * 100, 2))
        r5y.append(round((tr.get("5Y") or 0) * 100, 2))

    def _bar(name, vals, color):
        return {
            "type": "bar", "name": name, "x": tickers, "y": vals,
            "marker": {"color": color},
            "hovertemplate": "%{x} " + name + ": %{y:+.1f}%<extra></extra>",
        }

    layout = {
        **_DARK_LAYOUT,
        "xaxis": {"gridcolor": "#1a1e2b"},
        "yaxis": {"gridcolor": "#1a1e2b", "ticksuffix": "%",
                  "zeroline": True, "zerolinecolor": "#232838"},
        "barmode": "group",
        "hovermode": "x unified",
        "legend": {"bgcolor": "rgba(0,0,0,0)", "bordercolor": "rgba(0,0,0,0)"},
        "height": 260,
        "margin": {"l": 50, "r": 20, "t": 20, "b": 40},
    }
    return _safe_json({"data": [_bar("1Y", r1y, _BLUE), _bar("3Y", r3y, _TEAL), _bar("5Y", r5y, _GREEN)], "layout": layout})


# ── Portfolio allocation treemap ───────────────────────────────────────────────

def allocation_treemap(positions: list, position_metrics: list) -> str:
    """Treemap of portfolio allocation by theme and ticker."""
    if not positions:
        return _safe_json({"data": [], "layout": _DARK_LAYOUT})

    # Build return lookup
    ret_lookup = {}
    for pm in position_metrics:
        t = pm.get("ticker", "")
        ret_1y = pm.get("trailing_returns", {}).get("1Y")
        ret_lookup[t] = ret_1y

    labels, parents, values, custom = [], [], [], []

    # Theme level
    theme_weights: dict = defaultdict(float)
    for pos in positions:
        theme_weights[pos["theme"]] += pos["weight"]

    for theme, wt in theme_weights.items():
        labels.append(theme)
        parents.append("")
        values.append(round(wt * 100, 2))
        custom.append("")

    # Ticker level
    for pos in positions:
        ticker  = pos["ticker"].upper()
        theme   = pos["theme"]
        wt      = pos["weight"]
        ret_1y  = ret_lookup.get(ticker)
        ret_str = f"{ret_1y*100:+.1f}%" if ret_1y is not None else "N/A"
        labels.append(ticker)
        parents.append(theme)
        values.append(round(wt * 100, 2))
        custom.append(ret_str)

    trace = {
        "type": "treemap",
        "labels": labels,
        "parents": parents,
        "values": values,
        "customdata": custom,
        "texttemplate": "<b>%{label}</b><br>%{value:.1f}%<br>%{customdata}",
        "hovertemplate": "%{label}<br>Weight: %{value:.1f}%<br>1Y: %{customdata}<extra></extra>",
        "marker": {
            "colorscale": [[0, _RED], [0.5, "#1a1e2b"], [1, _GREEN]],
            "showscale": False,
        },
    }

    layout = {
        **_DARK_LAYOUT,
        "title": {"text": "Portfolio Allocation", "font": {"size": 13}},
        "height": 380,
        "margin": {"l": 10, "r": 10, "t": 40, "b": 10},
    }

    return _safe_json({"data": [trace], "layout": layout})
