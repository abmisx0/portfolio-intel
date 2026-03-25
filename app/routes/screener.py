from __future__ import annotations

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import PORTFOLIOS
from core.screener import screen, compare

router = APIRouter()
templates: Jinja2Templates = None  # injected by main.py


@router.get("/screener")
def screener_page(request: Request):
    return templates.TemplateResponse("screener.html", {
        "request": request,
        "active": "screener",
        "portfolios": list(PORTFOLIOS.keys()),
    })


@router.post("/screener/results", response_class=HTMLResponse)
def screener_results(
    request: Request,
    ticker: str = Form(...),
    portfolio: str = Form("proposed"),
    allocation: float = Form(0.05),
):
    try:
        data = screen(ticker.upper(), portfolio_name=portfolio, candidate_allocation=allocation)
        return templates.TemplateResponse("partials/screen_results.html", {
            "request": request,
            "data": data,
            "allocation": allocation,
        })
    except Exception as exc:
        return HTMLResponse(
            f'<div class="error-box">Error screening {ticker.upper()}: {exc}</div>'
        )


@router.get("/screener/compare")
def compare_page(request: Request):
    return templates.TemplateResponse("screener.html", {
        "request": request,
        "active": "screener",
        "portfolios": list(PORTFOLIOS.keys()),
        "mode": "compare",
    })


@router.post("/screener/compare/results", response_class=HTMLResponse)
def compare_results(
    request: Request,
    ticker_a: str = Form(...),
    ticker_b: str = Form(...),
):
    try:
        data = compare(ticker_a.upper(), ticker_b.upper())
        return templates.TemplateResponse("partials/compare_results.html", {
            "request": request,
            "data": data,
        })
    except Exception as exc:
        return HTMLResponse(
            f'<div class="error-box">Error comparing {ticker_a}/{ticker_b}: {exc}</div>'
        )
