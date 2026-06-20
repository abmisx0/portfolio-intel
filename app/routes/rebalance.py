from __future__ import annotations

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import PORTFOLIOS
from core.rebalancer import compute_rebalance, parse_current_weights

router = APIRouter()
templates: Jinja2Templates = None


@router.get("/rebalance")
def rebalance_page(request: Request):
    return templates.TemplateResponse("rebalance.html", {
        "request": request,
        "active": "rebalance",
        "portfolios": list(PORTFOLIOS.keys()),
    })


@router.post("/rebalance/results", response_class=HTMLResponse)
def rebalance_results(
    request: Request,
    portfolio: str = Form("core_satellite"),
    value: float = Form(...),
    current: str = Form(""),
):
    try:
        current_weights = parse_current_weights(current) if current.strip() else None
        data = compute_rebalance(portfolio, value, current_weights)
        return templates.TemplateResponse("partials/rebalance_results.html", {
            "request": request,
            "data": data,
        })
    except Exception as exc:
        return HTMLResponse(f'<div class="error-box">Rebalance error: {exc}</div>')
