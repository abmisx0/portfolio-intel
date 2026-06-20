from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import markdown as _md

from core.insights import get_insights

router = APIRouter()
templates: Jinja2Templates = None  # injected by main.py

_MD = _md.Markdown(extensions=["tables", "sane_lists", "fenced_code"])

# Headline metric chips shown under a report title: key → (label, formatter)
_METRIC_CHIPS = [
    ("total_value",   "Value",     lambda v: f"${v:,.0f}"),
    ("sharpe_3y",     "Sharpe 3Y", lambda v: f"{v:.2f}"),
    ("sharpe_5y",     "Sharpe 5Y", lambda v: f"{v:.2f}"),
    ("ann_return_3y", "Return 3Y", lambda v: f"{v:.1%}"),
    ("sharpe",        "Sharpe",    lambda v: f"{v:.2f}"),
    ("ann_return",    "Return",    lambda v: f"{v:.1%}"),
]


def _looks_like_markdown(body: str) -> bool:
    """Checkup reports are markdown; weekly-sync notes are fixed-width text
    that must keep its alignment (rendered in a <pre> instead)."""
    return body.lstrip().startswith("#") or "\n## " in body or "**" in body


def _metric_chips(metrics: dict | None) -> list[dict]:
    if not metrics:
        return []
    chips = []
    for key, label, fmt in _METRIC_CHIPS:
        v = metrics.get(key)
        if isinstance(v, (int, float)):
            chips.append({"label": label, "value": fmt(v)})
    return chips[:5]


@router.get("/insights")
def insights_page(request: Request):
    entries = get_insights(limit=100)
    for e in entries:
        if _looks_like_markdown(e["body"]):
            _MD.reset()
            e["body_html"] = _MD.convert(e["body"])
        else:
            e["body_html"] = None
        e["chips"] = _metric_chips(e.get("metrics"))
    return templates.TemplateResponse("insights.html", {
        "request": request,
        "active": "insights",
        "entries": entries,
    })
