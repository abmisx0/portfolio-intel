from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import PORTFOLIOS, LOOKBACK_5Y, BENCHMARK_TICKER, DEFAULT_PORTFOLIO, PORTFOLIO_DISPLAY_ORDER
from app.routes.portfolio import resolve_positions
from core.data_fetcher import get_close_series, price_map_freshness
from core.analytics import (
    portfolio_position_metrics,
    portfolio_returns_series,
    compute_metrics,
    correlation_matrix,
    theme_attribution,
)
from core.holdings import portfolio_holdings_table
from app.charts import correlation_heatmap, theme_bar_chart
from core.alerts import run_portfolio_alerts

router = APIRouter()
templates: Jinja2Templates = None  # injected by main.py

_5Y_START = LOOKBACK_5Y
BENCHMARK = BENCHMARK_TICKER


@router.get("/analytics")
def analytics_page(request: Request, portfolio: str = DEFAULT_PORTFOLIO):
    return templates.TemplateResponse("analytics.html", {
        "request": request,
        "active": "analytics",
        "portfolio": portfolio,
        "portfolios": PORTFOLIO_DISPLAY_ORDER + [
            p for p in PORTFOLIOS
            if p not in PORTFOLIO_DISPLAY_ORDER and p not in ("core_satellite", "thematic")
        ],
    })


@router.get("/analytics/data", response_class=HTMLResponse)
def analytics_data(request: Request, portfolio: str = DEFAULT_PORTFOLIO):
    """HTMX endpoint: compute analytics and return rendered fragment."""
    try:
        positions = resolve_positions(portfolio)

        price_map = {}
        for pos in positions:
            t = pos["ticker"].upper()
            s = get_close_series(t, start=_5Y_START)
            if not s.empty:
                price_map[t] = s

        benchmark = get_close_series(BENCHMARK, start=_5Y_START)

        position_metrics = portfolio_position_metrics(positions, price_map, benchmark)
        themes = theme_attribution(positions, price_map, trailing_days=252)
        top_holdings = portfolio_holdings_table(portfolio, top_n=20)

        cm = correlation_matrix(price_map)
        heatmap_json = correlation_heatmap(cm)
        bar_json = theme_bar_chart(themes)

        alerts = run_portfolio_alerts(positions, cm, top_holdings)

        weights = {pos["ticker"].upper(): pos["weight"] for pos in positions}
        port_series = portfolio_returns_series(price_map, weights)
        port_metrics = {}
        if not port_series.empty:
            port_price = (1 + port_series).cumprod()
            port_metrics = compute_metrics(port_price, benchmark=benchmark, label=portfolio)

        data_freshness = price_map_freshness(price_map)

        return templates.TemplateResponse("partials/analytics_data.html", {
            "request": request,
            "portfolio": portfolio,
            "port_metrics": port_metrics,
            "position_metrics": position_metrics,
            "themes": themes,
            "top_holdings": top_holdings,
            "heatmap_json": heatmap_json,
            "bar_json": bar_json,
            "alerts": alerts,
            "data_freshness": data_freshness,
        })
    except Exception as exc:
        return HTMLResponse(f'<div class="error-box">Analytics error: {exc}</div>')
