from __future__ import annotations

import json
import pandas as pd

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import PORTFOLIOS, LOOKBACK_5Y, LOOKBACK_ALL, BENCHMARK_TICKER, BENCHMARK_SPX, DEFAULT_PORTFOLIO, PORTFOLIO_DISPLAY_ORDER
from core.data_fetcher import get_close_series, price_map_freshness
from core.analytics import (
    portfolio_position_metrics,
    portfolio_returns_series,
    compute_metrics,
    theme_attribution,
)
from datetime import date as _today_date
from core.insights import (
    days_since_last_sync,
    get_last_sync_metrics,
    save_insight,
    save_sync_metrics,
)
from core.holdings import portfolio_holdings_table
from app.charts import (
    allocation_treemap,
    portfolio_comparison_data,
    drawdown_chart,
    position_performance_chart,
    position_performance_data,
    risk_return_scatter,
    trailing_returns_bar,
)

router = APIRouter()
templates: Jinja2Templates = None  # injected by main.py

_5Y_START = LOOKBACK_5Y
BENCHMARK = BENCHMARK_TICKER


def _fetch_portfolio_series(port_name: str) -> pd.Series | None:
    """Fetch price data and compute full-history cumprod series for one portfolio."""
    positions = PORTFOLIOS.get(port_name, [])
    if not positions:
        return None
    price_map: dict = {}
    for pos in positions:
        t = pos["ticker"].upper()
        s = get_close_series(t, start=LOOKBACK_ALL)
        if not s.empty:
            price_map[t] = s
    weights = {pos["ticker"].upper(): pos["weight"] for pos in positions}
    ret = portfolio_returns_series(price_map, weights)
    if ret.empty:
        return None
    return (1 + ret).cumprod()


@router.get("/")
def overview(request: Request, portfolio: str = DEFAULT_PORTFOLIO):
    positions = PORTFOLIOS.get(portfolio, [])
    if not positions and portfolio != DEFAULT_PORTFOLIO:
        return RedirectResponse(url=f"/?portfolio={DEFAULT_PORTFOLIO}", status_code=302)

    full_price_map: dict = {}
    for pos in positions:
        t = pos["ticker"].upper()
        s = get_close_series(t, start=LOOKBACK_ALL)
        if not s.empty:
            full_price_map[t] = s

    benchmark = get_close_series(BENCHMARK, start=_5Y_START)
    spx_full  = get_close_series(BENCHMARK_SPX, start=LOOKBACK_ALL)

    _5y_ts = pd.Timestamp(_5Y_START)
    price_map = {t: s[s.index >= _5y_ts] for t, s in full_price_map.items()
                 if not s[s.index >= _5y_ts].empty}

    position_metrics = portfolio_position_metrics(positions, price_map, benchmark)
    themes = theme_attribution(positions, price_map, trailing_days=252)
    top_holdings = portfolio_holdings_table(portfolio, top_n=15)

    weights = {pos["ticker"].upper(): pos["weight"] for pos in positions}
    port_series_full = portfolio_returns_series(full_price_map, weights)
    port_price_full = (1 + port_series_full).cumprod() if not port_series_full.empty else None

    port_price = None
    port_metrics = {}
    if port_price_full is not None:
        _5y_slice = port_price_full[port_price_full.index >= _5y_ts]
        if not _5y_slice.empty:
            port_price = _5y_slice / _5y_slice.iloc[0]
            port_metrics = compute_metrics(port_price, benchmark=benchmark, label=portfolio)

    # Inline only the active portfolio + SPX — comparison portfolios are fetched lazily
    # via the /api/comparison-data/{name} endpoint to keep page size small.
    spx_price = spx_full if not spx_full.empty else None
    cum_data_json = portfolio_comparison_data(
        {portfolio: port_price_full} if port_price_full is not None else {},
        spx_price,
    )

    treemap_json    = allocation_treemap(positions, position_metrics)
    dd_chart_json   = drawdown_chart(port_price, benchmark, portfolio)
    rr_chart_json   = risk_return_scatter(positions, position_metrics)
    tr_chart_json   = trailing_returns_bar(position_metrics)
    perf_chart_json = position_performance_chart(full_price_map, positions)
    perf_data_json  = position_performance_data(full_price_map, positions)

    data_freshness = price_map_freshness(price_map)

    _sync_days = days_since_last_sync()
    if _sync_days is None and data_freshness:
        try:
            _sync_days = (_today_date.today() - _today_date.fromisoformat(data_freshness)).days
        except ValueError:
            _sync_days = None

    pinned = PORTFOLIO_DISPLAY_ORDER
    sorted_portfolios = pinned + [p for p in PORTFOLIOS.keys() if p not in pinned]

    portfolio_compositions = {
        name: [{"ticker": p["ticker"].upper(), "weight": p["weight"], "theme": p["theme"]}
               for p in pos]
        for name, pos in PORTFOLIOS.items()
    }

    return templates.TemplateResponse("index.html", {
        "request": request,
        "active": "overview",
        "portfolio": portfolio,
        "portfolios": sorted_portfolios,
        "positions": positions,
        "position_metrics": {pm["ticker"]: pm for pm in position_metrics if "error" not in pm},
        "port_metrics": port_metrics,
        "themes": themes,
        "top_holdings": top_holdings,
        "treemap_json":              treemap_json,
        "cum_data_json":             cum_data_json,
        "portfolio_compositions_json": json.dumps(portfolio_compositions),
        "dd_chart_json":             dd_chart_json,
        "rr_chart_json":             rr_chart_json,
        "tr_chart_json":             tr_chart_json,
        "perf_chart_json":           perf_chart_json,
        "perf_data_json":            perf_data_json,
        "data_freshness":            data_freshness,
        "days_since_sync":           _sync_days,
    })


@router.post("/api/sync")
def sync_data(portfolio: str = DEFAULT_PORTFOLIO):
    """Re-fetch latest price data for all positions, compute analytics delta, save insight."""
    positions = PORTFOLIOS.get(portfolio, [])
    if not positions:
        return Response(content=json.dumps({"error": "unknown portfolio"}),
                        media_type="application/json", status_code=404)

    # Delta-fetch — get_close_series only fetches missing date ranges
    full_price_map: dict = {}
    for pos in positions:
        t = pos["ticker"].upper()
        s = get_close_series(t, start=LOOKBACK_ALL)
        if not s.empty:
            full_price_map[t] = s

    benchmark = get_close_series(BENCHMARK, start=_5Y_START)

    _5y_ts = pd.Timestamp(_5Y_START)
    price_map = {t: s[s.index >= _5y_ts] for t, s in full_price_map.items()
                 if not s[s.index >= _5y_ts].empty}

    weights = {pos["ticker"].upper(): pos["weight"] for pos in positions}
    port_series = portfolio_returns_series(price_map, weights)
    port_metrics: dict = {}
    if not port_series.empty:
        port_price = (1 + port_series).cumprod()
        port_price_norm = port_price / port_price.iloc[0]
        port_metrics = compute_metrics(port_price_norm, benchmark=benchmark, label=portfolio)

    freshness = price_map_freshness(price_map)

    # Build delta analysis note
    prev = get_last_sync_metrics(portfolio)

    def _line(label: str, key: str, scale: float = 1.0, fmt: str = ".4f", unit: str = "") -> str:
        cur_v = port_metrics.get(key)
        if cur_v is None:
            return ""
        cur_s = f"{cur_v * scale:{fmt}}{unit}"
        if prev is None:
            return f"  {label}: {cur_s}"
        prev_v = prev.get(key)
        if prev_v is None:
            return f"  {label}: {cur_s}"
        delta = (cur_v - prev_v) * scale
        arrow = "+" if delta > 0 else ("-" if delta < 0 else " ")
        return f"  {label}: {prev_v * scale:{fmt}}{unit} → {cur_s}  ({arrow}{abs(delta):{fmt}}{unit})"

    lines = [f"Data synced through {freshness} for portfolio {portfolio}.", ""]
    if port_metrics:
        lines.append("5Y Analytics:")
        for item in [
            ("Sharpe",      "sharpe_ratio",          1.0,   ".4f", ""),
            ("Sortino",     "sortino_ratio",          1.0,   ".4f", ""),
            ("Ann. Return", "annualized_return",      100.0, ".2f", "%"),
            ("Max DD",      "max_drawdown",           100.0, ".2f", "%"),
            ("Calmar",      "calmar_ratio",            1.0,   ".4f", ""),
        ]:
            line = _line(*item)
            if line:
                lines.append(line)
    if prev is None:
        lines.append("\n(No prior snapshot — future syncs will show deltas.)")

    title = f"Sync: {portfolio} through {freshness}"
    body = "\n".join(lines)

    if port_metrics:
        save_sync_metrics(portfolio, port_metrics)
    insight_id = save_insight("manual_sync", title, body, portfolio, port_metrics or None)

    return Response(
        content=json.dumps({
            "freshness": freshness,
            "days_since_sync": 0,
            "insight": {"id": insight_id, "title": title, "body": body},
        }),
        media_type="application/json",
    )


@router.get("/api/comparison-data/{portfolio_name}")
def comparison_data_api(portfolio_name: str):
    """Return all (period × resolution) chart data for one comparison portfolio.

    The client fetches this lazily when the user picks a comparison portfolio
    from the dropdown, then merges the result into the in-memory _cd object.
    Data is keyed as {period: {resolution: {portfolio_name: {xs, ys}}}}.
    """
    port_price = _fetch_portfolio_series(portfolio_name)
    if port_price is None:
        return Response(content="{}", media_type="application/json", status_code=404)
    result = portfolio_comparison_data({portfolio_name: port_price}, None)
    return Response(content=result, media_type="application/json")
