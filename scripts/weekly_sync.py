#!/usr/bin/env python3
"""
Weekly portfolio sync — runs every Saturday morning via launchd.

Does NOT require the web server to be running. Imports core modules directly.

What it runs:
  1. Fetch latest prices for all positions
  2. 5Y + 3Y analytics (with week-over-week delta)
  3. 7.5Y backtest vs previous portfolio (SPX benchmark)
  4. Correlation matrix
  5. Top 10 effective holdings
  6. Tier 1 optimizer: 3Y+5Y Sharpe, 3Y+5Y Omega (with v8 drift flags)
  7. Portfolio alerts (correlation, concentration, theme overlap)
  8. Research scan: scrape forums for ETF ideas, screen top candidates

Discord output: one portfolio-status message + one research message (if findings).
  - Reports STATUS (STABLE / WATCH / REVIEW) with key numbers
  - Only flags issues that warrant action or further research
  - Verbose tables are saved to the insights DB only

Usage (manual test):
    cd portfolio-intel
    python3 scripts/weekly_sync.py
    python3 scripts/weekly_sync.py --portfolio v8
"""
from __future__ import annotations

import argparse
import logging
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from config import (
    PORTFOLIOS, LOOKBACK_ALL, LOOKBACK_5Y, BENCHMARK_TICKER, DEFAULT_PORTFOLIO,
)
from core.data_fetcher import get_close_series, price_map_freshness
from core.analytics import portfolio_returns_series, compute_metrics, correlation_matrix
from core.backtester import backtest
from core.holdings import portfolio_holdings_table
from core.optimizer import optimize
from core.alerts import run_portfolio_alerts
from core.insights import get_last_sync_metrics, save_insight, save_sync_metrics
from core.notifier import post_discord
from core.research import run_research

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── v8 optimizer caps and targets ─────────────────────────────────────────────
_V8_CAPS = {"SMH": 0.35, "PPA": 0.25, "NLR": 0.15, "VDE": 0.15, "SLV": 0.10, "IAU": 0.10}
_V8_TARGETS = {
    "SMH": 0.28, "PPA": 0.21, "VDE": 0.13, "NLR": 0.11,
    "SLV": 0.10, "IAU": 0.10, "QTUM": 0.07,
}

_TIER1_OBJECTIVES = [
    ("3Y Sharpe", "sharpe", 3),
    ("5Y Sharpe", "sharpe", 5),
    ("3Y Omega",  "omega",  3),
    ("5Y Omega",  "omega",  5),
]

# Known high-corr pairs in v8 — not flagged as "new"
_KNOWN_HIGH_CORR = {frozenset(["SMH", "QTUM"]), frozenset(["SLV", "IAU"])}

# Thresholds for status determination
_SHARPE_DROP_WATCH  = 0.08   # week-over-week Sharpe drop to trigger WATCH
_SHARPE_DROP_REVIEW = 0.15
_OPT_DRIFT_WATCH    = 0.05   # Tier1 avg drifts ≥5pp from v8 target → WATCH
_OPT_DRIFT_REVIEW   = 0.10   # ≥10pp → REVIEW
_HIGH_CORR_THRESH   = 0.75


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pct(v: float | None, decimals: int = 1) -> str:
    return f"{v * 100:+.{decimals}f}%" if v is not None else "N/A"

def _f(v: float | None, decimals: int = 2) -> str:
    return f"{v:.{decimals}f}" if v is not None else "N/A"


# ── Analytics ─────────────────────────────────────────────────────────────────

def _compute_analytics(price_map: dict, weights: dict, benchmark: pd.Series) -> dict:
    series = portfolio_returns_series(price_map, weights)
    if series.empty:
        return {}
    price = (1 + series).cumprod()
    return compute_metrics(price / price.iloc[0], benchmark=benchmark)


# ── Status determination ───────────────────────────────────────────────────────

def _determine_status(
    metrics_5y: dict,
    prev_metrics: dict | None,
    opt_drifts: dict[str, float],
    new_corr_pairs: list[tuple[str, str, float]],
    alert_data: dict,
) -> tuple[str, list[str]]:
    """
    Returns (status_label, [reason_lines]).
    status_label: "STABLE" | "WATCH" | "REVIEW"
    """
    status = "STABLE"
    reasons: list[str] = []

    # Critical alerts always escalate to REVIEW
    if alert_data.get("critical"):
        status = "REVIEW"
        for a in alert_data["critical"]:
            reasons.append(f"CRITICAL alert: {a['message']}")

    # New (previously unknown) high-corr pairs
    for ta, tb, corr in new_corr_pairs:
        level = "REVIEW" if corr >= 0.90 else "WATCH"
        if status != "REVIEW":
            status = level
        reasons.append(f"NEW high-corr pair: {ta}/{tb} {corr:.3f}")

    # Week-over-week Sharpe drop
    if prev_metrics and metrics_5y:
        cur_s  = metrics_5y.get("sharpe_ratio", 0.0)
        prev_s = prev_metrics.get("sharpe_ratio", 0.0)
        drop   = prev_s - cur_s  # positive = drop
        if drop >= _SHARPE_DROP_REVIEW:
            if status != "REVIEW":
                status = "REVIEW"
            reasons.append(f"5Y Sharpe dropped {drop:.3f} this week ({prev_s:.3f} → {cur_s:.3f})")
        elif drop >= _SHARPE_DROP_WATCH:
            if status == "STABLE":
                status = "WATCH"
            reasons.append(f"5Y Sharpe -0.{abs(drop)*100:.0f}bp this week ({prev_s:.3f} → {cur_s:.3f})")

    # Optimizer signal drift
    review_drifts = [(t, d) for t, d in opt_drifts.items() if abs(d) >= _OPT_DRIFT_REVIEW]
    watch_drifts  = [(t, d) for t, d in opt_drifts.items()
                     if _OPT_DRIFT_WATCH <= abs(d) < _OPT_DRIFT_REVIEW]

    if review_drifts:
        if status != "REVIEW":
            status = "REVIEW"
        for t, d in review_drifts:
            sign = "+" if d > 0 else ""
            reasons.append(f"Optimizer: {t} Tier1 avg {sign}{d*100:.1f}pp from target — consider rebalancing")

    elif watch_drifts and status == "STABLE":
        status = "WATCH"
        for t, d in watch_drifts:
            sign = "+" if d > 0 else ""
            reasons.append(f"Optimizer: {t} Tier1 avg {sign}{d*100:.1f}pp from target — monitor")

    if not reasons:
        reasons.append("All metrics within normal bounds — no action needed.")

    return status, reasons


# ── Compact Discord message builders ──────────────────────────────────────────

def _discord_portfolio_msg(
    portfolio: str,
    freshness: str,
    metrics_5y: dict,
    metrics_3y: dict,
    prev_metrics: dict | None,
    bt: dict,
    corr_data: dict,
    holdings: list[dict],
    opt_results: list[tuple[str, dict]],
    alert_data: dict,
    new_corr_pairs: list[tuple[str, str, float]],
    opt_drifts: dict[str, float],
    status: str,
    reasons: list[str],
) -> str:
    lines: list[str] = []
    lines.append(f"{portfolio} — {freshness}")
    lines.append(f"STATUS: {status}")
    lines.append("")

    # Status reasons
    for r in reasons:
        lines.append(f"  {r}")
    lines.append("")

    # 5Y analytics headline
    s5  = metrics_5y.get("sharpe_ratio")
    so5 = metrics_5y.get("sortino_ratio")
    r5  = metrics_5y.get("annualized_return")
    d5  = metrics_5y.get("max_drawdown")
    lines.append(
        f"Analytics 5Y:  Sharpe {_f(s5)}  Sortino {_f(so5)}  "
        f"Return {_pct(r5)}  MDD {_pct(d5)}"
    )

    # Week-over-week delta for Sharpe only (most signal-rich)
    if prev_metrics and s5 is not None:
        prev_s = prev_metrics.get("sharpe_ratio")
        if prev_s is not None:
            delta = s5 - prev_s
            lines.append(f"  Δ week: Sharpe {delta:+.4f}")

    # 3Y snapshot
    s3  = metrics_3y.get("sharpe_ratio")
    so3 = metrics_3y.get("sortino_ratio")
    r3  = metrics_3y.get("annualized_return")
    d3  = metrics_3y.get("max_drawdown")
    lines.append(
        f"Analytics 3Y:  Sharpe {_f(s3)}  Sortino {_f(so3)}  "
        f"Return {_pct(r3)}  MDD {_pct(d3)}"
    )
    lines.append("")

    # Backtest vs previous — headline only
    if bt:
        a_s = bt.get(portfolio, {}).get("metrics", {}).get("sharpe_ratio")
        b_s = bt.get("previous", {}).get("metrics", {}).get("sharpe_ratio")
        if a_s and b_s:
            diff = a_s - b_s
            lines.append(
                f"Backtest vs previous (7.5Y):  "
                f"Sharpe {a_s:.3f} vs {b_s:.3f}  ({'+' if diff>=0 else ''}{diff:.3f})"
            )
    lines.append("")

    # Optimizer drift — only show watch/review tickers
    watch_drifts  = {t: d for t, d in opt_drifts.items()
                     if _OPT_DRIFT_WATCH <= abs(d) < _OPT_DRIFT_REVIEW}
    review_drifts = {t: d for t, d in opt_drifts.items() if abs(d) >= _OPT_DRIFT_REVIEW}
    if not watch_drifts and not review_drifts:
        lines.append("Optimizer:  all Tier 1 signals within 5pp of targets — stable")
    else:
        lines.append("Optimizer Tier 1 signal drift vs v8 targets:")
        for t, d in {**review_drifts, **watch_drifts}.items():
            flag = "REVIEW" if abs(d) >= _OPT_DRIFT_REVIEW else "watch"
            sign = "+" if d > 0 else ""
            lines.append(f"  {t}: {sign}{d*100:.1f}pp  [{flag}]")
        lines.append("  Rebalance if ≥10pp drift persists 3+ consecutive weeks.")
    lines.append("")

    # Correlation flags — only flag new pairs (pre-computed in run_sync)
    if new_corr_pairs:
        for ta, tb, v in new_corr_pairs:
            lines.append(f"Correlation NEW flag:  {ta}/{tb} {v:.3f} — investigate")
    else:
        # Count known high-corr pairs for informational line
        known_flags = [
            (ta, tb, corr_data.get("matrix", {}).get(ta, {}).get(tb, 0.0))
            for ta in corr_data.get("tickers", [])
            for tb in corr_data.get("tickers", [])
            if ta < tb
            and frozenset([ta, tb]) in _KNOWN_HIGH_CORR
            and abs(corr_data.get("matrix", {}).get(ta, {}).get(tb, 0.0)) >= _HIGH_CORR_THRESH
        ]
        if known_flags:
            lines.append(
                f"Correlation:  {len(known_flags)} known flag(s) — "
                + "  ".join(f"{ta}/{tb} {v:.3f}" for ta, tb, v in known_flags)
            )
    lines.append("")

    # Alerts — warnings and above only
    warnings = alert_data.get("warning", [])
    criticals = alert_data.get("critical", [])
    if criticals:
        for a in criticals:
            lines.append(f"ALERT CRITICAL: {a['message']}")
    elif warnings:
        # Only surface non-correlation warnings (corr alerts already shown above)
        non_corr = [a for a in warnings if a.get("type") != "HIGH_CORRELATION"]
        if non_corr:
            for a in non_corr:
                lines.append(f"ALERT WARNING: {a['message']}")
    lines.append("")

    # Top 3 holdings
    top3 = holdings[:3]
    if top3:
        hstr = "  ".join(f"{h['symbol']} {h['effective_weight']*100:.2f}%" for h in top3)
        lines.append(f"Top holdings:  {hstr}")

    return "\n".join(lines)


def _discord_research_msg(research: dict, portfolio: str) -> str | None:
    """Returns compact Discord message for research findings, or None if nothing interesting."""
    if not research.get("interesting"):
        return None
    lines = ["Forum research findings:"]
    lines.append("")
    for line in research["summary_lines"]:
        lines.append(line)
    return "\n".join(lines)


# ── Full detail report for insights DB ───────────────────────────────────────

def _full_report(
    portfolio: str, freshness: str,
    metrics_5y: dict, metrics_3y: dict, prev: dict | None,
    bt: dict, corr_data: dict, holdings: list[dict],
    opt_results: list[tuple[str, dict]],
    alert_data: dict, research: dict,
    status: str, reasons: list[str],
) -> str:
    sections: list[str] = []

    # ── Header
    sections.append(f"=== Weekly Sync: {portfolio} through {freshness}  STATUS: {status} ===")
    for r in reasons:
        sections.append(f"  {r}")

    # ── Analytics
    def _metric_line(label: str, key: str, scale: float = 1.0,
                     fmt: str = ".4f", unit: str = "") -> str:
        # Closes over metrics_5y, metrics_3y, prev from _full_report args
        cur  = metrics_5y.get(key)
        cur3 = metrics_3y.get(key)
        if cur is None:
            return ""
        s = f"  {label:<14}: 5Y {cur * scale:{fmt}}{unit}"
        if cur3 is not None:
            s += f"  3Y {cur3 * scale:{fmt}}{unit}"
        if prev and prev.get(key) is not None:
            delta = (cur - prev[key]) * scale
            sign = "+" if delta > 0 else ""
            s += f"  (Δ {sign}{delta:{fmt}}{unit})"
        return s

    analytics_lines = ["", "Analytics:"]
    for args in [
        ("Sharpe",       "sharpe_ratio",           1.0,   ".4f", ""),
        ("Sortino",      "sortino_ratio",           1.0,   ".4f", ""),
        ("Ann. Return",  "annualized_return",       100.0, ".2f", "%"),
        ("Volatility",   "annualized_volatility",   100.0, ".2f", "%"),
        ("Max DD",       "max_drawdown",            100.0, ".2f", "%"),
        ("Calmar",       "calmar_ratio",            1.0,   ".4f", ""),
        ("Beta",         "beta",                    1.0,   ".4f", ""),
    ]:
        line = _metric_line(*args)
        if line:
            analytics_lines.append(line)
    sections.append("\n".join(analytics_lines))

    # ── Backtest
    if bt:
        a_m  = bt.get(portfolio, {}).get("metrics", {})
        b_m  = bt.get("previous", {}).get("metrics", {})
        bm_key = f"benchmark_{bt.get('benchmark', 'SPX')}"
        bm_m = bt.get(bm_key, {}).get("metrics", {})
        bt_lines = [
            "",
            f"Backtest vs previous ({bt.get('actual_start', '?')} → {bt.get('actual_end', '?')}):",
        ]
        for label, key, scale, fmt, unit in [
            ("Sharpe",     "sharpe_ratio",           1.0,   ".4f", ""),
            ("Sortino",    "sortino_ratio",           1.0,   ".4f", ""),
            ("Ann. Return","annualized_return",       100.0, ".2f", "%"),
            ("Volatility", "annualized_volatility",   100.0, ".2f", "%"),
            ("Max DD",     "max_drawdown",            100.0, ".2f", "%"),
        ]:
            av  = a_m.get(key)
            bv  = b_m.get(key)
            bmv = bm_m.get(key)
            if av is None:
                continue
            row = f"  {label:<14}: {portfolio} {av * scale:{fmt}}{unit}"
            if bv is not None:
                row += f"  previous {bv * scale:{fmt}}{unit}"
            if bmv is not None:
                row += f"  SPX {bmv * scale:{fmt}}{unit}"
            bt_lines.append(row)
        sections.append("\n".join(bt_lines))

    # ── Correlation matrix
    tickers = corr_data.get("tickers", [])
    matrix  = corr_data.get("matrix", {})
    corr_lines = ["", "Correlation Matrix:"]
    header = "         " + "".join(f"{t:>7}" for t in tickers)
    corr_lines.append(header)
    for ta in tickers:
        row_vals = [f"{matrix[ta].get(tb, 0):>7.3f}" for tb in tickers]
        corr_lines.append(f"  {ta:<6} " + "".join(row_vals))
    high_pairs = [
        f"  {ta}/{tb}: {matrix[ta].get(tb, 0):.3f}"
        for ta in tickers for tb in tickers
        if ta < tb and abs(matrix.get(ta, {}).get(tb, 0)) >= _HIGH_CORR_THRESH
    ]
    if high_pairs:
        corr_lines.append(f"  High-correlation pairs (|r| >= {_HIGH_CORR_THRESH}):")
        corr_lines.extend(high_pairs)
    sections.append("\n".join(corr_lines))

    # ── Holdings
    h_lines = ["", "Top 10 Effective Holdings:"]
    for i, h in enumerate(holdings[:10], 1):
        h_lines.append(
            f"  {i:>2}. {h['symbol']:<7} {h['effective_weight']*100:>5.2f}%  "
            f"{h.get('name', '')[:30]}"
        )
    sections.append("\n".join(h_lines))

    # ── Optimizer
    opt_lines = ["", "Optimizer — Tier 1 Signals:"]
    if opt_results:
        obj_labels = [lbl for lbl, _ in opt_results]
        opt_lines.append(
            "  Ticker  " + "".join(f"{lbl:>10}" for lbl in obj_labels)
            + "     Avg    Target   Drift"
        )
        target_tickers = list(_V8_TARGETS.keys())
        for t in target_tickers:
            weights = [r.get("optimal_weights", {}).get(t, 0.0) for _, r in opt_results]
            avg = sum(weights) / len(weights)
            target = _V8_TARGETS.get(t, 0.0)
            drift  = avg - target
            flag   = " REVIEW" if abs(drift) >= _OPT_DRIFT_REVIEW else \
                     " watch"  if abs(drift) >= _OPT_DRIFT_WATCH  else ""
            opt_lines.append(
                f"  {t:<6}  "
                + "".join(f"{w*100:>9.1f}%" for w in weights)
                + f"  {avg*100:>5.1f}%   {target*100:.0f}%   "
                + f"{'+' if drift>0 else ''}{drift*100:.1f}pp{flag}"
            )
    sections.append("\n".join(opt_lines))

    # ── Alerts
    alert_lines = ["", "Alerts:"]
    total = alert_data.get("total", 0)
    if total == 0:
        alert_lines.append("  No alerts.")
    else:
        for level in ("critical", "warning", "info"):
            for a in alert_data.get(level, []):
                alert_lines.append(f"  [{level.upper()}] {a['type']}: {a['message']}")
    sections.append("\n".join(alert_lines))

    # ── Research
    sections.append("")
    sections.append(research.get("full_report", "=== Research ===\n  Not run."))

    return "\n".join(sections)


# ── Main sync ─────────────────────────────────────────────────────────────────

def run_sync(portfolio: str) -> None:
    positions = PORTFOLIOS.get(portfolio)
    if not positions:
        logger.error("Unknown portfolio: %s", portfolio)
        sys.exit(1)

    tickers = [pos["ticker"].upper() for pos in positions]
    weights = {pos["ticker"].upper(): pos["weight"] for pos in positions}
    logger.info("Syncing %d tickers for portfolio %s …", len(tickers), portfolio)

    # ── 1. Fetch prices ───────────────────────────────────────────────────────
    full_price_map: dict = {}
    for t in tickers:
        s = get_close_series(t, start=LOOKBACK_ALL)
        if not s.empty:
            full_price_map[t] = s
            logger.info("  %s: %d rows through %s", t, len(s), s.index[-1].date())

    freshness = price_map_freshness(full_price_map)
    bm_series = get_close_series(BENCHMARK_TICKER, start=LOOKBACK_5Y)

    _5y_ts = pd.Timestamp(LOOKBACK_5Y)
    _3y_ts = pd.Timestamp(date.today() - timedelta(days=3 * 365))

    pm_5y = {t: sl for t, s in full_price_map.items()
             if not (sl := s[s.index >= _5y_ts]).empty}
    pm_3y = {t: sl for t, s in full_price_map.items()
             if not (sl := s[s.index >= _3y_ts]).empty}
    bm_3y = bm_series[bm_series.index >= _3y_ts]

    # ── 2. Analytics ──────────────────────────────────────────────────────────
    logger.info("Computing analytics …")
    metrics_5y = _compute_analytics(pm_5y, weights, bm_series)
    metrics_3y = _compute_analytics(pm_3y, weights, bm_3y)
    prev = get_last_sync_metrics(portfolio)

    # ── 3. Backtest vs previous ───────────────────────────────────────────────
    logger.info("Running backtest vs previous …")
    bt_start = (date.today() - timedelta(days=int(365 * 7.5))).isoformat()
    try:
        bt = backtest(portfolio, "previous", start=bt_start, benchmark="spx")
    except Exception as exc:
        logger.warning("Backtest failed: %s", exc)
        bt = {}

    # ── 4. Correlation matrix ─────────────────────────────────────────────────
    logger.info("Computing correlation matrix …")
    corr_data = correlation_matrix(pm_5y)

    # Detect new high-corr pairs (not in known set)
    new_corr_pairs: list[tuple[str, str, float]] = []
    corr_tickers = corr_data.get("tickers", [])
    for ta in corr_tickers:
        for tb in corr_tickers:
            if ta >= tb:
                continue
            v = corr_data.get("matrix", {}).get(ta, {}).get(tb, 0.0)
            if abs(v) >= _HIGH_CORR_THRESH and frozenset([ta, tb]) not in _KNOWN_HIGH_CORR:
                new_corr_pairs.append((ta, tb, v))

    # ── 5. Holdings ───────────────────────────────────────────────────────────
    logger.info("Fetching top holdings …")
    try:
        holdings = portfolio_holdings_table(portfolio, top_n=10)
    except Exception as exc:
        logger.warning("Holdings failed: %s", exc)
        holdings = []

    # ── 6. Tier 1 optimizer ───────────────────────────────────────────────────
    logger.info("Running Tier 1 optimizer objectives …")
    opt_results: list[tuple[str, dict]] = []
    for label, objective, years in _TIER1_OBJECTIVES:
        start_dt = date.today() - timedelta(days=years * 365)
        logger.info("  %s …", label)
        try:
            result = optimize(tickers=tickers, objective=objective,
                              start=start_dt, per_max=_V8_CAPS)
            opt_results.append((label, result))
        except Exception as exc:
            logger.warning("  %s failed: %s", label, exc)

    # Compute Tier 1 averages and drifts vs v8 targets
    opt_drifts: dict[str, float] = {}
    if opt_results:
        for t in tickers:
            weights_list = [r.get("optimal_weights", {}).get(t, 0.0) for _, r in opt_results]
            avg = sum(weights_list) / len(weights_list)
            opt_drifts[t] = avg - _V8_TARGETS.get(t, avg)

    # ── 7. Alerts ─────────────────────────────────────────────────────────────
    logger.info("Running portfolio alerts …")
    try:
        alert_data = run_portfolio_alerts(positions, corr_data, holdings)
    except Exception as exc:
        logger.warning("Alerts failed: %s", exc)
        alert_data = {"critical": [], "warning": [], "info": [], "total": 0}

    # ── 8. Research scan ──────────────────────────────────────────────────────
    logger.info("Running forum research scan …")
    try:
        research = run_research(portfolio, active_tickers=tickers)
    except Exception as exc:
        logger.warning("Research scan failed: %s", exc)
        research = {
            "interesting": [], "screened": 0, "total_found": 0,
            "summary_lines": [f"Research: scan failed ({exc})"],
            "full_report": f"=== Research ===\n  Error: {exc}",
        }

    # ── 9. Determine status ───────────────────────────────────────────────────
    status, reasons = _determine_status(
        metrics_5y, prev, opt_drifts, new_corr_pairs, alert_data
    )
    logger.info("Portfolio status: %s", status)

    # ── 10. Build outputs ─────────────────────────────────────────────────────
    discord_portfolio = _discord_portfolio_msg(
        portfolio, freshness, metrics_5y, metrics_3y, prev,
        bt, corr_data, holdings, opt_results, alert_data,
        new_corr_pairs, opt_drifts, status, reasons,
    )
    discord_research = _discord_research_msg(research, portfolio)

    full_body = _full_report(
        portfolio, freshness, metrics_5y, metrics_3y, prev,
        bt, corr_data, holdings, opt_results,
        alert_data, research, status, reasons,
    )

    # ── 11. Save insight ──────────────────────────────────────────────────────
    if metrics_5y:
        save_sync_metrics(portfolio, metrics_5y)
    title = f"Weekly Sync: {portfolio} through {freshness}  [{status}]"
    insight_id = save_insight("weekly_auto", title, full_body, portfolio, metrics_5y or None)
    logger.info("Insight #%d saved.", insight_id)

    # ── 12. Discord ───────────────────────────────────────────────────────────
    ok = post_discord(f"Weekly Sync [{status}]: {portfolio}", discord_portfolio, portfolio)
    logger.info("Discord portfolio status: %s", "sent" if ok else "failed")

    if discord_research:
        ok2 = post_discord(f"Weekly Research: {portfolio}", discord_research, portfolio)
        logger.info("Discord research findings: %s", "sent" if ok2 else "failed")
    else:
        logger.info("Research: nothing interesting this week — Discord research message skipped.")

    logger.info("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly portfolio sync + full analytics + Discord")
    parser.add_argument("--portfolio", default=DEFAULT_PORTFOLIO,
                        help=f"Portfolio name (default: {DEFAULT_PORTFOLIO})")
    args = parser.parse_args()
    run_sync(args.portfolio)
