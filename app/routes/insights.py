from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.insights import get_insights

router = APIRouter()
templates: Jinja2Templates = None  # injected by main.py


@router.get("/insights")
def insights_page(request: Request):
    entries = get_insights(limit=100)
    return templates.TemplateResponse("insights.html", {
        "request": request,
        "active": "insights",
        "entries": entries,
    })
