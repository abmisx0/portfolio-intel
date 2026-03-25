from __future__ import annotations

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import core.watchlist as wl
from core.screener import screen

router = APIRouter()
templates: Jinja2Templates = None


@router.get("/watchlist")
def watchlist_page(request: Request):
    return templates.TemplateResponse("watchlist.html", {
        "request": request,
        "active": "watchlist",
        "items": wl.list_all(),
    })


@router.post("/watchlist/add", response_class=HTMLResponse)
def watchlist_add(request: Request, ticker: str = Form(...), notes: str = Form("")):
    ticker = ticker.upper().strip()
    wl.add(ticker, notes)
    items = wl.list_all()
    return templates.TemplateResponse("partials/watchlist_rows.html", {
        "request": request,
        "items": items,
    })


@router.post("/watchlist/remove", response_class=HTMLResponse)
def watchlist_remove(request: Request, ticker: str = Form(...)):
    wl.remove(ticker.upper())
    items = wl.list_all()
    return templates.TemplateResponse("partials/watchlist_rows.html", {
        "request": request,
        "items": items,
    })


@router.get("/watchlist/screen/{ticker}", response_class=HTMLResponse)
def watchlist_screen_one(request: Request, ticker: str, portfolio: str = "proposed"):
    try:
        data = screen(ticker.upper(), portfolio_name=portfolio)
        return templates.TemplateResponse("partials/screen_results.html", {
            "request": request,
            "data": data,
            "allocation": 0.05,
        })
    except Exception as exc:
        return HTMLResponse(f'<div class="error-box">Error: {exc}</div>')
