from __future__ import annotations

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import PORTFOLIOS
from core.backtester import backtest
from app.charts import backtest_chart

router = APIRouter()
templates: Jinja2Templates = None  # injected by main.py


@router.get("/backtest")
def backtest_page(request: Request):
    return templates.TemplateResponse("backtest.html", {
        "request": request,
        "active": "backtest",
        "portfolios": list(PORTFOLIOS.keys()),
    })


@router.post("/backtest/results", response_class=HTMLResponse)
def backtest_results(
    request: Request,
    portfolio_a: str = Form("core_satellite"),
    portfolio_b: str = Form("thematic"),
    start: str = Form("2020-01-01"),
    end: str = Form(None),
    benchmark: bool = Form(True),
):
    try:
        data = backtest(
            portfolio_a=portfolio_a,
            portfolio_b=portfolio_b,
            start=start,
            end=end or None,
            include_benchmark=benchmark,
        )
        chart_json = backtest_chart(data)
        return templates.TemplateResponse("partials/backtest_results.html", {
            "request": request,
            "data": data,
            "chart_json": chart_json,
        })
    except Exception as exc:
        return HTMLResponse(
            f'<div class="error-box">Backtest error: {exc}</div>'
        )
