"""
Forum research module.

Scrapes public investor forums for ETF mentions, validates candidates,
screens them against the active portfolio, and returns only finds that
pass quality filters.

Sources (no authentication required):
  - Reddit public JSON API: r/ETFs, r/investing, r/stocks, r/dividends
  - Extensible: add sources to SOURCES list

Quality filters applied before flagging as interesting:
  - quoteType == "ETF" (not individual stocks)
  - 5Y Sharpe >= MIN_SHARPE
  - Max correlation to any existing portfolio position < MAX_CORR
  - Max drawdown > MAX_DD (not catastrophic)
  - Mentioned >= MIN_MENTIONS times across all sources

Usage:
    from core.research import run_research
    result = run_research("my_portfolio", active_tickers=["VOO", "QQQ", ...])
"""
from __future__ import annotations

import json
import logging
import re
import sys
import os
import urllib.error
import urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf

from core.screener import screen

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_MENTIONS  = 2     # min times a ticker must appear to be checked
MAX_SCREEN    = 8     # max ETFs to fully screen (API calls are slow)
MIN_SHARPE    = 0.45  # minimum 5Y Sharpe to flag as interesting
MAX_CORR      = 0.88  # skip if too correlated to any existing position
MAX_DD        = -0.60 # skip if max drawdown worse than -60%

# ── Reddit sources ────────────────────────────────────────────────────────────
_SUBREDDITS = ["ETFs", "investing", "stocks", "dividends"]
_POSTS_PER_SUB = 50
_HEADERS = {"User-Agent": "PortfolioIntel/1.0 (weekly research scan)"}

# ── Noise words — common English + finance terms that look like tickers ───────
_NOISE: frozenset[str] = frozenset({
    # 1-2 char
    "A", "I", "AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IF", "IN", "IS",
    "IT", "ME", "MY", "NO", "OF", "OK", "ON", "OR", "PE", "SO", "TO", "UP", "US", "WE",
    # 3-char english
    "ALL", "AND", "ANY", "ARE", "BUT", "CAN", "DID", "FOR", "GET", "GOT", "HAD",
    "HAS", "HIM", "HIS", "HOW", "ITS", "LET", "MAY", "MRS", "NOT", "NOW", "OLD",
    "ONE", "OUT", "OWN", "PUT", "SAY", "SEE", "SET", "SHE", "THE", "TOO", "TWO",
    "USE", "WAS", "WAY", "WHO", "WHY", "YOU", "YOY",
    # finance/markets acronyms
    "APR", "APY", "ATH", "ATL", "BOJ", "CFO", "CEO", "CPI", "DCA", "DCF", "DXY",
    "ECB", "EMH", "EPS", "ETF", "FED", "GDP", "GNP", "IMF", "IPO", "IRA", "IRS",
    "MBS", "MFE", "NFP", "PBR", "PMI", "PPI", "RMD", "ROE", "ROI", "SEC", "VIX",
    "CAGR", "CAPE", "CNBC", "DJIA", "FDIC", "FOMC", "GAAP", "REIT", "SPAC",
    "NYSE", "AMEX",
    # common 4-5 char english
    "ALSO", "BEST", "BLUE", "BOND", "BONDS", "BULL", "BULLS", "BEAR", "BEARS",
    "CALL", "CASH", "COLA", "DEBT", "DONE", "EACH", "EVEN", "EVER", "FOMO",
    "FROM", "FUND", "FUNDS", "GAIN", "GOLD", "GOOD", "HAVE", "HERE", "HIGH",
    "HOLD", "HOME", "HOPE", "HUGE", "INTO", "JUST", "KEEP", "KNOW", "LAST",
    "LIKE", "LONG", "LOOK", "LOSS", "LOVE", "MADE", "MAKE", "MANY", "MORE",
    "MOST", "MOVE", "MUCH", "NEED", "NEXT", "ONLY", "OPEN", "OVER", "PAST",
    "PEAK", "PLUS", "RISK", "SAID", "SAME", "SELL", "SOME", "STAY", "SURE",
    "TAKE", "THAN", "THAT", "THEM", "THEN", "THEY", "THIS", "TIME", "VERY",
    "WANT", "WEEK", "WELL", "WERE", "WHEN", "WITH", "YEAR", "YOUR", "ZERO",
    "ABOUT", "ABOVE", "AFTER", "AGAIN", "AMONG", "BEING", "BONDS", "COULD",
    "DAILY", "DOING", "EARLY", "EVERY", "FIRST", "FUNDS", "GIVEN", "GOING",
    "GREAT", "INDEX", "LARGE", "LOWER", "MIGHT", "MONEY", "MONTH", "MOVED",
    "NEVER", "OTHER", "POINT", "PRICE", "PRIOR", "QUITE", "RANGE", "RATES",
    "RALLY", "RATIO", "RISKS", "ROUND", "SHARE", "SINCE", "SMALL", "SOLID",
    "SPENT", "STILL", "STOCK", "TAXES", "THINK", "THOSE", "THREE", "TODAY",
    "TOTAL", "TREND", "UNDER", "UNTIL", "USING", "VALUE", "WEEKS", "WHERE",
    "WHILE", "WOULD", "YEARS",
    # well-known individual stocks (not ETFs) — avoid false positives
    "AAPL", "AMZN", "BABA", "BRKB", "BRKA", "COIN", "COST", "GOOG", "GOOGL",
    "HOOD", "LCID", "LYFT", "META", "MSTR", "MSFT", "NFLX", "NVDA", "PLTR",
    "RIVN", "SNAP", "SOFI", "TSLA", "UBER", "ZOOM",
})

# Tickers that will never surface as interesting discoveries regardless of mentions.
# Extend this list with tickers you've already evaluated and rejected for your thesis.
_KNOWN_REJECTS: frozenset[str] = frozenset({
    # Passive ultra-broad market — trivially known, not discovery candidates
    "SPY", "IVV", "VTI", "VT", "VXUS", "SCHB", "ITOT", "SPTM", "SCHX",
    "BRKB", "BRKA",
    # Dividend-focused income ETFs — below typical growth Sharpe thresholds
    "SCHD", "VYM", "DVY", "HDV", "DGRO", "NOBL",
    # Bond ETFs — fail MIN_SHARPE but waste screening API calls
    "TLT", "IEF", "SHY", "BND", "AGG", "VCIT", "LQD", "HYG", "JNK",
})


# ── Reddit scraping ───────────────────────────────────────────────────────────

def _fetch_reddit_mentions(subreddits: list[str], limit: int) -> dict[str, list[str]]:
    """
    Fetch hot posts from each subreddit and extract ticker mentions.

    $TICKER mentions (intentional) get 2 entries; bare UPPERCASE get 1.
    Returns {ticker: [source_label, ...]} where len == weighted mention count.
    """
    mentions: dict[str, list[str]] = {}

    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit={limit}"
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            logger.warning("Reddit r/%s fetch failed: %s", sub, exc)
            continue

        for post in data.get("data", {}).get("children", []):
            pdata = post.get("data", {})
            title = pdata.get("title", "")
            body = pdata.get("selftext", "")[:1500]
            text = title + " " + body
            label = f"r/{sub}: {title[:55]}"

            # $TICKER pattern — high signal, intentional mention
            dollar_hits = set(re.findall(r'\$([A-Z]{2,6})\b', text))
            # bare UPPERCASE — lower signal, filter noise aggressively
            bare_hits = set(re.findall(r'\b([A-Z]{3,5})\b', text)) - dollar_hits

            for t in dollar_hits:
                if t not in _NOISE:
                    mentions.setdefault(t, [])
                    mentions[t].extend([label, label])  # double weight

            for t in bare_hits:
                if t not in _NOISE:
                    mentions.setdefault(t, [])
                    mentions[t].append(label)

    return mentions


# ── ETF validation ────────────────────────────────────────────────────────────

def _check_etf(ticker: str) -> tuple[bool, str]:
    """
    Quick yfinance check: is this an ETF, and what's its name?
    Returns (is_etf, name).
    """
    try:
        info = yf.Ticker(ticker).info
        if info.get("quoteType", "").upper() == "ETF":
            name = info.get("longName") or info.get("shortName") or ticker
            return True, name
    except Exception:
        pass
    return False, ""


# ── Verdict helpers ───────────────────────────────────────────────────────────

def _sharpe_tier(sharpe: float) -> str:
    """Label Sharpe into broad quality tiers (5Y reference)."""
    if sharpe >= 0.85:
        return "top-tier Sharpe (≥0.85)"
    if sharpe >= 0.70:
        return "strong Sharpe (0.70–0.85)"
    if sharpe >= 0.55:
        return "competitive Sharpe (0.55–0.70)"
    return "marginal Sharpe (above minimum threshold)"


def _corr_action(max_corr: float, max_corr_ticker: str) -> str:
    """Describe portfolio fit based on highest correlation to any existing position."""
    if max_corr >= 0.80:
        return (f"high overlap with {max_corr_ticker} (corr {max_corr:.2f}) — "
                f"would displace it, not add alongside")
    if max_corr >= 0.65:
        return (f"moderate overlap with {max_corr_ticker} (corr {max_corr:.2f}) — "
                f"compare head-to-head before adding")
    return f"low correlation to portfolio (max {max_corr:.2f} vs {max_corr_ticker}) — additive"


def _fmt_aum(aum: float | None) -> str:
    if aum is None:
        return "N/A"
    if aum >= 1e9:
        return f"${aum/1e9:.1f}B"
    return f"${aum/1e6:.0f}M"


# ── Main entry point ──────────────────────────────────────────────────────────

def run_research(portfolio: str, active_tickers: list[str]) -> dict:
    """
    Full research pipeline: scrape → filter → validate → screen → report.

    Args:
        portfolio:       Portfolio name to screen candidates against (from config.py).
        active_tickers:  Tickers already in the active portfolio — excluded from results.

    Returns dict with keys:
        interesting    — list of qualified candidates with metrics
        screened       — number of ETFs fully screened
        total_found    — number of unique tickers extracted from forums
        summary_lines  — compact lines for Discord
        full_report    — detailed text for insights DB
    """
    logger.info("Research: scraping forums …")
    raw = _fetch_reddit_mentions(_SUBREDDITS, _POSTS_PER_SUB)

    active_upper = {t.upper() for t in active_tickers}
    rejects = _KNOWN_REJECTS | active_upper

    # Filter by mention count and exclude known/active tickers
    candidates = {
        t: srcs for t, srcs in raw.items()
        if len(srcs) >= MIN_MENTIONS and t not in rejects
    }

    # Sort by mention count, cap at MAX_SCREEN
    ranked = sorted(candidates.items(), key=lambda x: len(x[1]), reverse=True)[:MAX_SCREEN]
    logger.info("Research: %d unique tickers, %d to check (top %d)",
                len(raw), len(candidates), len(ranked))

    interesting: list[dict] = []
    screened = 0

    for ticker, sources in ranked:
        mention_count = len(sources)
        logger.info("Research: checking %s (%d weighted mentions) …", ticker, mention_count)

        # Stage 1: confirm it's an ETF
        is_etf, etf_name = _check_etf(ticker)
        if not is_etf:
            logger.info("  %s: not an ETF — skip", ticker)
            continue

        screened += 1

        # Stage 2: full screen against portfolio
        try:
            result = screen(ticker, portfolio_name=portfolio)
        except Exception as exc:
            logger.warning("  %s: screen() error: %s", ticker, exc)
            continue

        metrics = result.get("risk_metrics", {})
        sharpe  = metrics.get("sharpe_ratio")
        max_dd  = metrics.get("max_drawdown")
        corrs   = result.get("correlations_to_portfolio", {})
        max_corr_val    = max(corrs.values(), default=0.0)
        max_corr_ticker = max(corrs, key=corrs.get) if corrs else ""

        # Stage 3: quality filter
        if sharpe is None or sharpe < MIN_SHARPE:
            logger.info("  %s: 5Y Sharpe %.3f < %.2f — skip", ticker, sharpe or 0, MIN_SHARPE)
            continue
        if max_dd is not None and max_dd < MAX_DD:
            logger.info("  %s: max DD %.1f%% too deep — skip", ticker, max_dd * 100)
            continue
        if max_corr_val >= MAX_CORR:
            logger.info("  %s: corr %.3f vs %s >= %.2f — too correlated — skip",
                        ticker, max_corr_val, max_corr_ticker, MAX_CORR)
            continue

        # Deduplicate source subreddits for display
        unique_subs = list(dict.fromkeys(s.split(":")[0] for s in sources))

        trailing  = result.get("trailing_returns", {})
        etf_info  = result.get("etf_info", {})
        top_holds = result.get("top_holdings", [])[:3]

        interesting.append({
            "ticker":          ticker,
            "name":            etf_name,
            "sharpe":          round(sharpe, 4),
            "sortino":         round(metrics.get("sortino_ratio", 0.0), 4),
            "max_dd":          round(max_dd, 4) if max_dd is not None else None,
            "ann_return":      round(metrics.get("annualized_return", 0.0), 4),
            "max_corr":        round(max_corr_val, 3),
            "max_corr_ticker": max_corr_ticker,
            "mentions":        mention_count,
            "sources":         unique_subs[:4],
            "return_1y":       trailing.get("1Y"),
            "return_5y":       trailing.get("5Y"),
            "aum":             etf_info.get("aum"),
            "expense_ratio":   etf_info.get("expense_ratio"),
            "top_holdings":    top_holds,
            "verdict":         (f"{_sharpe_tier(sharpe)} | "
                                f"{_corr_action(max_corr_val, max_corr_ticker)}"),
        })
        logger.info("  %s: INTERESTING — Sharpe %.3f  max corr %.3f vs %s",
                    ticker, sharpe, max_corr_val, max_corr_ticker)

    # ── Build outputs ─────────────────────────────────────────────────────────
    summary_lines: list[str] = []
    full_lines: list[str] = [
        f"=== Research Scan — {len(raw)} tickers found across "
        f"{len(_SUBREDDITS)} subreddits, {screened} ETFs screened ===",
        f"  Quality filters: 5Y Sharpe ≥ {MIN_SHARPE}  |  "
        f"Max portfolio corr < {MAX_CORR}  |  Max DD > {MAX_DD * 100:.0f}%",
        "",
    ]

    if not interesting:
        summary_lines.append(
            f"Research: no new ETFs passed filters this week "
            f"({screened} screened from {len(raw)} mentions)."
        )
        full_lines.append("  No candidates passed quality filters.")
        top_checked = [t for t, _ in ranked[:6]]
        if top_checked:
            full_lines.append(f"  Top tickers checked: {', '.join(top_checked)}")
    else:
        summary_lines.append(
            f"Research: {len(interesting)} ETF(s) flagged "
            f"({screened} screened, {len(raw)} total mentions):"
        )
        for c in interesting:
            dd_s  = f"{c['max_dd'] * 100:.1f}%" if c["max_dd"] is not None else "N/A"
            r1y_s = f"{c['return_1y'] * 100:+.1f}%" if c["return_1y"] is not None else "N/A"
            r5y_s = f"{c['return_5y'] * 100:+.1f}%" if c["return_5y"] is not None else "N/A"
            er_s  = (f"{c['expense_ratio'] * 100:.2f}%"
                     if c["expense_ratio"] is not None else "N/A")
            holds_s = "  ".join(
                f"{h.get('symbol','?')} {h.get('weight',0)*100:.1f}%"
                for h in c["top_holdings"]
            ) if c["top_holdings"] else "N/A"

            summary_lines.append(
                f"  {c['ticker']} — {c['name'][:55]}  "
                f"({c['mentions']} mentions: {', '.join(c['sources'])})"
            )
            summary_lines.append(
                f"    Sharpe {c['sharpe']:.2f} | Sortino {c['sortino']:.2f} | "
                f"MDD {dd_s} | Ann. Return {c['ann_return']*100:+.1f}%"
            )
            summary_lines.append(
                f"    AUM {_fmt_aum(c['aum'])} | ER {er_s} | "
                f"1Y {r1y_s} | 5Y {r5y_s}"
            )
            summary_lines.append(f"    Verdict: {c['verdict']}")
            if c["top_holdings"]:
                summary_lines.append(f"    Top holdings: {holds_s}")
            summary_lines.append("")

            full_lines.append(f"  {c['ticker']} — {c['name']}")
            full_lines.append(
                f"    5Y Sharpe {c['sharpe']:.4f} | Sortino {c['sortino']:.4f} | "
                f"Max DD {dd_s} | Ann. Return {c['ann_return']*100:+.2f}%"
            )
            full_lines.append(
                f"    AUM {_fmt_aum(c['aum'])} | ER {er_s} | "
                f"1Y {r1y_s} | 5Y {r5y_s}"
            )
            full_lines.append(f"    Verdict: {c['verdict']}")
            full_lines.append(
                f"    Max corr: {c['max_corr']:.3f} vs {c['max_corr_ticker']}"
            )
            if c["top_holdings"]:
                full_lines.append(f"    Top holdings: {holds_s}")
            full_lines.append(
                f"    {c['mentions']} mentions via: {', '.join(c['sources'])}"
            )
            full_lines.append("")

    return {
        "interesting":   interesting,
        "screened":      screened,
        "total_found":   len(raw),
        "summary_lines": summary_lines,
        "full_report":   "\n".join(full_lines),
    }
