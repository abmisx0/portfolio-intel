"""
Portfolio Intelligence — FastAPI web application.

Run with:
  cd portfolio-intel
  python3 -m uvicorn app.main:app --reload --port 8000
Or via CLI:
  python3 -m cli start
"""
from __future__ import annotations

import math
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.routes import portfolio, screener, backtest, analytics, watchlist, rebalance, insights

BASE_DIR = Path(__file__).parent

# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Portfolio Intelligence", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ── Jinja2 filters ─────────────────────────────────────────────────────────────

def _pct(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v * 100:+.2f}%"

def _pct_plain(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v * 100:.2f}%"

def _f2(v):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "—"
    return f"{v:.4f}"

def _money(v):
    if v is None:
        return "—"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"

def _pct_class(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "num"
    if v > 0:
        return "num pos"
    if v < 0:
        return "num neg"
    return "num"

def _sign_class(v):
    """For non-return values like Sharpe — red if <0, else normal."""
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "num"
    return "num neg" if v < 0 else "num"

templates.env.filters["pct"]       = _pct
templates.env.filters["pct_plain"] = _pct_plain
templates.env.filters["f2"]        = _f2
templates.env.filters["money"]     = _money
templates.env.filters["pct_class"] = _pct_class
templates.env.filters["sign_class"]= _sign_class

# ── Make templates available to route modules ─────────────────────────────────

portfolio.templates  = templates
screener.templates   = templates
backtest.templates   = templates
analytics.templates  = templates
watchlist.templates  = templates
rebalance.templates  = templates
insights.templates   = templates

# ── Routers ────────────────────────────────────────────────────────────────────

app.include_router(portfolio.router)
app.include_router(screener.router)
app.include_router(backtest.router)
app.include_router(analytics.router)
app.include_router(watchlist.router)
app.include_router(rebalance.router)
app.include_router(insights.router)
