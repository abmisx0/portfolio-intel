# Portfolio Intelligence

A local ETF research and portfolio analytics tool built for thesis-driven investors. Two interfaces: a CLI (structured JSON output, Claude-friendly) and a FastAPI + HTMX web dashboard.

All market data flows through a SQLite cache backed by yfinance — no paid data subscriptions required.

---

## Features

**Portfolio analysis**

| Command | What it does |
|---------|-------------|
| `screen` | Screen a candidate ETF against your portfolio — overlap, correlation, risk metrics, top holdings |
| `compare` | Side-by-side comparison of 2+ ETFs with full cross-overlap analysis |
| `analytics` | Portfolio-level Sharpe, Sortino, beta, drawdown, theme attribution, top single-stock exposures |
| `correlation` | Full pairwise correlation matrix |
| `holdings` | Effective single-stock exposure aggregated across all ETF positions |
| `backtest` | Historical comparison of two portfolio allocations vs benchmark (VOO/SPX/Nasdaq/Russell) |
| `optimize` | Find optimal weights via SLSQP (Sharpe, Sortino, Omega, min-CVaR, and more) |
| `rebalance` | Compute drift from target and generate a trade list |
| `alerts` | Flag high-correlation pairs and concentration breaches |

**Live Robinhood (read-only)**

| Command | What it does |
|---------|-------------|
| `positions` | Sync live holdings with tax-lot status (LTCG/STCG) |
| `exposure` | Delta-adjusted exposure across equity + options (folds option delta into economic weights) |
| `performance` | Money-weighted (XIRR) realized return vs index benchmarks, from actual order history |
| `advise` | Full advisory: trim signals, watchlist screening, discovery suggestions |
| `realized` | Realized capital gains, income, and margin cost for a tax year, from order/event history |

**Market data & research**

| Command | What it does |
|---------|-------------|
| `valuation` | Valuation multiples — P/E, P/S, P/B, EV/EBITDA (stocks); fund-level ratios (ETFs) |
| `growth` | Consensus forward revenue/EPS growth (FY0/FY1), long-term growth, and PEG vs the S&P baseline |
| `analysts` | Analyst consensus, price targets, and recent rating changes |
| `technicals` | SMA50/200, RSI(14), 52-week range, swing support/resistance |
| `macro` | Market regime snapshot — VIX, index levels, commodities, Treasury curve, risk-free rate |
| `insider` | Insider transaction filings from SEC Form 4 (Finnhub) |
| `news` | Company news with aggregate sentiment scoring (Finnhub) |
| `earnings` | EPS surprises and forward analyst estimates (Finnhub) |
| `watchlist` | Manage a list of candidate ETFs |

**Real estate**

| Command | What it does |
|---------|-------------|
| `property` | Leveraged real-estate backtest/forecast vs an index, after tax, equal out-of-pocket dollars both sides |
| `buyrent` | Owner-occupied buy-vs-rent verdict for a specific listing, with breakeven appreciation |

**Web**

| Command | What it does |
|---------|-------------|
| `publish` | Publish a checkup report to the dashboard Insights page |
| `start` | Launch the web dashboard at `http://127.0.0.1:8000` |

---

## Quick Start

### 1. Install dependencies

Requires **Python 3.11+**.

```bash
cd portfolio-intel
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Choose a portfolio

Two starter examples (`core_satellite` and `thematic`) are defined out of the box, so you can explore immediately:

```bash
python3 -m cli analytics --portfolio core_satellite
```

Define your own by editing the `PORTFOLIOS` dict in `config.py`, or — to keep them out of git — by dropping a `data/portfolios.json` file (same schema, gitignored):

```python
PORTFOLIOS["my_portfolio"] = [
    {"ticker": "VOO",  "weight": 0.40, "theme": "Broad Market",  "role": "S&P 500 anchor"},
    {"ticker": "SMH",  "weight": 0.30, "theme": "Technology",    "role": "AI compute thesis"},
    {"ticker": "XLE",  "weight": 0.20, "theme": "Energy",        "role": "Commodity / geopolitical hedge"},
    {"ticker": "GLD",  "weight": 0.10, "theme": "Commodities",   "role": "Inflation hedge"},
    # weights must sum to 1.0
]
```

The reserved name **`live`** is the default for analysis commands — it fetches your current Robinhood holdings at call time (requires credentials; see step 3). Pass `--portfolio <name>` to analyze a static allocation instead.

### 3. Configure credentials (optional)

```bash
cp .env.example .env
# Edit .env with your values
```

Credentials are only needed for the `positions` and `advise` commands (Robinhood integration). All analytics, screening, comparison, optimization, and backtesting work without any credentials.

### 4. Run a command

All commands run from the `portfolio-intel/` directory:

```bash
python3 -m cli screen QQQ --portfolio my_portfolio
python3 -m cli compare QQQ SPY
python3 -m cli analytics --portfolio my_portfolio --format json
python3 -m cli backtest --a my_portfolio --b core_satellite --start 2020-01-01 --benchmark spx
python3 -m cli optimize --portfolio my_portfolio --objective sharpe --max-weight SMH:0.35
python3 -m cli start   # web dashboard
```

> **First run:** The SQLite cache is empty on a fresh clone. Price data and ETF holdings are fetched on demand from yfinance — expect the first command to take 10–30 seconds. Subsequent runs are fast. ETF holdings (used by `screen`, `holdings`, `analytics`) occasionally return incomplete data from yfinance for smaller ETFs; `N/A` in a holdings column is normal. To clear the cache and force a full re-fetch: `rm data/cache.db`.

---

## CLI Reference

### `screen` — ETF screener

```bash
python3 -m cli screen TICKER --portfolio PORTFOLIO [--format json|table]
```

Output: trailing returns (1M/3M/6M/YTD/1Y/3Y/5Y), risk metrics (Sharpe, Sortino, beta, max drawdown), correlation to each portfolio position, holdings overlap coefficient, effective single-stock concentration, top 10 holdings, ETF metadata (AUM, expense ratio, yield).

### `compare` — Side-by-side ETF comparison

```bash
python3 -m cli compare TICKER1 TICKER2 [TICKER3 ...]
```

- **2 tickers:** Full pairwise analysis — metrics, trailing returns, correlation, cross-holdings overlap, commodity context
- **3+ tickers:** Summary metrics table + full correlation matrix

### `analytics` — Portfolio analytics

```bash
python3 -m cli analytics --portfolio PORTFOLIO [--format json|table]
```

Output: per-position metrics, 1Y theme attribution, top-20 single-stock exposures, portfolio-level aggregate (Sharpe, Sortino, vol, beta, drawdown).

### `backtest` — Historical backtesting

```bash
python3 -m cli backtest --a PORTFOLIO_A --b PORTFOLIO_B [--start DATE] [--benchmark voo|spx]
```

Output: cumulative return, annualized return, volatility, Sharpe, Sortino, max drawdown, Calmar, calendar year returns table. `--benchmark` accepts `voo` (S&P 500 ETF), `spx` (pure S&P 500 index, no fees), `nasdaq` (QQQ), or `russell` (IWM). Note this is a **constant-weight** backtest of an allocation applied historically — for realized money-weighted performance from your actual orders, use [`performance`](#performance--realized-return-vs-indices).

### `optimize` — Weight optimizer

```bash
python3 -m cli optimize --portfolio PORTFOLIO --objective OBJECTIVE [OPTIONS]
python3 -m cli optimize TICKER1 TICKER2 ... --objective OBJECTIVE
```

**Objectives:** `sharpe` | `sortino` | `min-vol` | `min-cvar` | `min-drawdown` | `max-return` | `quadratic-utility` | `omega`

**Weight constraints:**
```bash
--max-weight 0.35               # global cap (all positions)
--max-weight SMH:0.35           # per-ticker cap
--min-weight QQQ:0.10           # per-ticker floor
# Mix global and per-ticker:
--max-weight 0.40 --max-weight SMH:0.30 --max-weight GLD:0.15
```

Default lookback: 3 years. Use `--start 2020-01-01` for 5Y, `--start 2018-01-01` for 7Y+.

### `holdings` — Effective single-stock exposure

```bash
python3 -m cli holdings --portfolio PORTFOLIO [--top N] [--format json|table]
```

Aggregates underlying stock holdings across all ETF positions, weighted by allocation. Shows how much NVDA, AAPL, MSFT, etc. you effectively own through your ETFs.

### `correlation` — Correlation matrix

```bash
python3 -m cli correlation --portfolio PORTFOLIO [--format json|table]
```

Full pairwise correlation matrix for all positions over trailing 1Y.

### `alerts` — Concentration and correlation alerts

```bash
python3 -m cli alerts --portfolio PORTFOLIO
```

Flags positions that breach correlation or concentration thresholds. Useful for spotting unexpected overlap between ETFs.

### `rebalance` — Trade list

```bash
python3 -m cli rebalance --portfolio PORTFOLIO [--current "TICKER:WEIGHT,..."] [--value DOLLARS]
python3 -m cli rebalance --portfolio PORTFOLIO --from-robinhood   # fetch live positions automatically
```

### `positions` — Live Robinhood holdings

```bash
python3 -m cli positions [--portfolio my_portfolio] [--format json|table]
```

Requires `RH_USERNAME` and `RH_PASSWORD` in `.env`. Shows shares, price, portfolio %, avg cost, G/L%, first purchase date, and tax status (LTCG / STCG→LTCG date). With `--portfolio`, also shows drift vs target and recommended trades.

### `advise` — Portfolio advisory

```bash
python3 -m cli advise [--portfolio my_portfolio] [--discoveries N]
```

Three sections:
1. **Trim signals** — which positions drag portfolio Sharpe/Sortino; includes tax status
2. **Watchlist candidates** — scored by marginal portfolio impact at 5% allocation
3. **Discovery suggestions** — top N from a curated thematic universe

Add `-d`/`--delta-adjusted` to reason on economic (option-delta-folded) exposure; `--lookback {1Y,3Y,5Y,10Y}` re-runs the simulation over a different window to test signal stability.

### `property` — Leveraged real-estate purchase vs an index, after tax

```bash
python3 -m cli property --price 450000 --rent 2600 --metro "Austin, TX" --backtest-start 2016-07
python3 -m cli property --price 450000 --rent 2600 --metro "Austin, TX" --hold 10 --niit
```

Simulates a leveraged property month-by-month (amortization, vacancy, maintenance/capex/insurance/tax, management, HOA) with a full federal tax layer (depreciation, passive-loss carryforward, recapture, LTCG) and compares **equal out-of-pocket dollars** into a benchmark, both sides after tax. Data from Zillow metro ZHVI/ZORI and FRED (both free, keyless). `--backtest-start` runs on actual historical metro/benchmark paths; without it, `property` forecasts off metro-CAGR defaults and prints the **breakeven appreciation** — the annual appreciation above which the property beats the index.

### `buyrent` — Should I buy this specific listing?

```bash
python3 -m cli buyrent --price 1500000 --rent 4800 --metro "Los Angeles, CA" --hoa 550
```

Owner-occupied buy-vs-rent verdict for one listing (`--rent` = the same unit's market rent). Prints the monthly own-vs-rent cost split, the price-to-rent ratio with its conventional zone (<18 buy-leaning, >22 rent-leaning), and BUY-vs-RENT terminal wealth across 5/10/20-year holds with the breakeven appreciation per horizon. Thin wrapper over the `property` forecast engine.

### `exposure` — Delta-adjusted exposure (equity + options)

```bash
python3 -m cli exposure [--min-weight 0.02] [--format json|table]
```

Folds option delta into each ticker's weight so the book reflects **economic** exposure, not just shares held: a short put adds long-equivalent exposure, a deep-ITM short call caps equity upside. Shows per-ticker equity-vs-delta-adjusted weights and per-contract option detail with the full Greek set (Δ, Γ, Θ, ν, ρ). Requires Robinhood credentials.

### `performance` — Realized return vs indices

```bash
python3 -m cli performance [--benchmark voo|spx|nasdaq|russell ...] [--format json|table]
```

Computes your **actual money-weighted return (XIRR)** from Robinhood order history — every filled buy/sell becomes a dated cash flow — and compares it against the same contributions *cloned into each index* (VOO/QQQ/IWM by default). Holding contribution timing fixed isolates asset choice, unlike the constant-weight `backtest`. Reconciles per-ticker order shares against current holdings and reports the XIRR only over the **covered sleeve** (positions the order history accounts for), with a coverage ratio — transferred-in (ACATS) or pre-window positions are excluded. Equity only.

### `realized` — Realized gains, income, and margin cost for a tax year

```bash
python3 -m cli realized --year 2026
python3 -m cli realized --format json
```

Reconstructs a tax year read-only from Robinhood history: stock orders (FIFO lot matching with ST/LT split), option order history at leg-execution level, option events (assignments/expirations folded into stock cost basis per 1099 rules), and dividends/interest/margin-interest records. Output: ST/LT capital gains, per-sale and per-contract detail, investment income net of margin interest. Requires Robinhood login; read the printed caveats (wash sales not modeled, transferred-in shares have no basis).

### `valuation` — Valuation multiples

```bash
python3 -m cli valuation TICKER [TICKER ...]
python3 -m cli valuation --portfolio PORTFOLIO [--format json|table]
```

Trailing/forward P/E, P/S, P/B, EV/EBITDA, profit margin, dividend yield, market cap (stocks); fund-level P/E / P/B / P/S, expense ratio, AUM (ETFs). yfinance unit quirks are normalised. Cached 1 day.

### `growth` — Consensus forward revenue/EPS growth

```bash
python3 -m cli growth NFLX LLY NVDA
python3 -m cli growth --portfolio live
```

Consensus revenue and EPS growth for the current (FY0) and next (FY1) fiscal year, long-term growth (LTG), forward P/E, PEG (fwd P/E ÷ LTG, falling back to FY1 EPS growth), profit margin, analyst count, and the S&P 500 LTG baseline for comparison. Cached 1 day. ETFs carry no consensus estimates — run `growth` on their top holdings instead.

### `analysts` — Analyst consensus and price targets

```bash
python3 -m cli analysts TICKER [TICKER ...]
python3 -m cli analysts --portfolio PORTFOLIO [--format json|table]
```

Consensus rating (Strong Buy → Strong Sell) with 1–5 score, mean/high/low price targets, implied upside, analyst-count breakdown, and recent rating changes with firm names. Cached 1 day.

### `technicals` — Price-action levels

```bash
python3 -m cli technicals TICKER [TICKER ...]
python3 -m cli technicals --portfolio PORTFOLIO
```

Last price, SMA50/SMA200, RSI(14), 52-week range, % from 52w high, and swing support/resistance (5-day pivots over trailing 6 months). Computed entirely from the price cache — no extra API calls.

### `macro` — Market regime snapshot

```bash
python3 -m cli macro [--format json|table]
```

VIX, S&P 500, Gold, WTI, Dollar index (level + 1M/1Y change), Treasury curve with 10Y–2Y spread and shape label, and the live risk-free rate. No API keys required (cache/yfinance + treasury.gov).

### `insider` / `news` / `earnings` — Finnhub research

```bash
python3 -m cli insider TICKER      # SEC Form 4 insider transactions
python3 -m cli news TICKER         # company news + aggregate sentiment
python3 -m cli earnings TICKER     # EPS surprises + forward estimates
```

Require a free `FINNHUB_API_KEY` in `.env`. Results cached in SQLite.

### `publish` — Push a report to the dashboard

```bash
python3 -m cli publish --title "Weekly checkup" --body-file report.md
```

Saves a markdown report to the Insights page of the web dashboard.

### `watchlist` — Candidate ETF list

```bash
python3 -m cli watchlist add TICKER [--notes "why you're watching"]
python3 -m cli watchlist list
python3 -m cli watchlist remove TICKER
python3 -m cli watchlist screen --portfolio PORTFOLIO   # screen all watchlist tickers at once
```

---

## Portfolio Names

Defined in `config.py`. Pass to `--portfolio`:

| Name | Description | Key Tickers |
|------|-------------|-------------|
| `core_satellite` | Broad market core + thematic satellites | VOO 50%, QQQ 20%, GLD 10%, BND 10%, VNQ 10% |
| `thematic` | Sector-based by macro thesis | SMH 25%, XLE 20%, XLF 20%, GLD 15%, VHT 10%, BND 10% |

These are starter examples. Define your own portfolios by editing `PORTFOLIOS` in `config.py`:

```python
PORTFOLIOS["my_portfolio"] = [
    {"ticker": "VOO",  "weight": 0.50, "theme": "Broad Market", "role": "S&P 500 anchor"},
    {"ticker": "SMH",  "weight": 0.25, "theme": "Technology",   "role": "AI compute thesis"},
    {"ticker": "GLD",  "weight": 0.15, "theme": "Commodities",  "role": "Inflation hedge"},
    {"ticker": "BND",  "weight": 0.10, "theme": "Fixed Income", "role": "Volatility buffer"},
    # weights must sum to 1.0
]
```

---

## Output Format

All commands support `--format table` (default) or `--format json`. JSON always uses a consistent envelope:

```json
{
  "command": "screen",
  "args": {"ticker": "QQQ", "portfolio": "core_satellite"},
  "timestamp": "2026-05-17T10:00:00Z",
  "data": { ... },
  "metadata": {"data_freshness": "2026-05-16"}
}
```

This makes every command directly consumable by Claude or any script without parsing table output.

---

## Architecture

```
portfolio-intel/
├── config.py                  # Portfolio definitions, constants
├── requirements.txt
├── .env.example               # Credential template (copy to .env)
├── data/
│   ├── cache.db               # SQLite: prices + ETF holdings + JSON caches (gitignored)
│   ├── seed_holdings.json     # Static top-10 holdings fallback (shipped)
│   └── portfolios.json        # Your personal saved portfolios (gitignored)
├── core/
│   ├── data_fetcher.py        # yfinance wrapper — delta-fetching, SQLite cache, batch prefetch
│   ├── cache.py               # Shared SQLite JSON cache (cached_json)
│   ├── analytics.py           # Sharpe, Sortino, beta, correlation, drawdown, trailing returns
│   ├── backtester.py          # Historical portfolio comparison engine
│   ├── performance.py         # Money-weighted (XIRR) realized return vs index benchmarks
│   ├── realized.py            # Realized gains/income/margin cost for a tax year (FIFO + option ledger)
│   ├── screener.py            # screen() + compare() + compare_multi()
│   ├── holdings.py            # ETF holdings decomposition, overlap analysis, theme attribution
│   ├── optimizer.py           # SLSQP optimizer, 8 objectives, multi-start
│   ├── exposure.py            # Delta-adjusted exposure, Black-Scholes Greeks
│   ├── realestate.py          # Zillow/FRED fetchers, amortization, leveraged property tax model
│   ├── valuation.py           # Valuation multiples (stocks + fund-level)
│   ├── growth.py              # Consensus forward revenue/EPS growth, PEG
│   ├── analysts.py            # Analyst consensus and price targets
│   ├── technicals.py          # SMA/RSI/52w range/support-resistance from price cache
│   ├── macro.py               # VIX, yield curve, WTI, Gold, risk-free rate (^IRX)
│   ├── finnhub.py             # Finnhub client — insider, news, earnings, ETF constituents
│   ├── rebalancer.py          # Drift calculation, trade list generation
│   ├── broker.py              # Robinhood integration (read-only) via robin_stocks
│   ├── research.py            # Advisory scoring engine
│   ├── watchlist.py           # Watchlist persistence (SQLite)
│   ├── alerts.py              # Correlation/concentration alerts
│   ├── insights.py            # Sync notes and analytics snapshots (SQLite)
│   └── notifier.py            # Discord webhook poster
├── cli/
│   ├── main.py                # Click group, command registration
│   ├── formatters.py          # JSON envelope + table formatters
│   └── commands/              # One file per CLI command (27 commands)
├── tests/                     # unittest suite (pure math, no network)
├── app/                       # FastAPI web dashboard
│   ├── main.py
│   ├── routes/
│   ├── templates/
│   └── static/
└── scripts/
    └── weekly_sync.py         # Standalone weekly portfolio sync → Discord
```

### Key design decisions

**Data caching:** All price data cached in SQLite. `get_close_series()` checks cache first, fetches only the missing date range from yfinance, and never re-fetches full history. ETF holdings cached with a 7-day TTL. `prefetch_prices()` batch-downloads multiple tickers in one yfinance call before any per-ticker loop.

**Robinhood integration:** Read-only. The `rh` module object is never returned to callers — all public functions return plain Python dicts. Credentials are read from `.env`; the OAuth token is cached to `~/.tokens/robinhood.pickle` by robin_stocks after first login.

**Optimizer:** SLSQP via scipy. Non-convex objectives (Sharpe, Sortino, Omega, min-CVaR) use 5 random starts to escape local minima. Convex objectives (min-vol, max-return) use a single start. Default 3Y lookback; override with `--start`.

**Risk-free rate:** Dynamic — trailing 90-day average of ^IRX (13-week T-bill). Falls back to `RISK_FREE_RATE = 0.045` in config if fetch fails.

**Benchmark:** VOO by default for beta and correlation. `^SPX` available as `--benchmark spx` (pure index, no expense ratio).

**Commodity context:** Energy ETFs (VDE, XLE, XOP, OIH) automatically show WTI beta/correlation. Gold ETFs (GLD, IAU, GOAU, GDX, etc.) show Gold futures beta/correlation.

---

## Testing

The test suite covers the pure, deterministic, money-affecting math (Black-Scholes Greeks, return/Sharpe/drawdown primitives, delta-adjusted weights). It runs with no network and no Robinhood:

```bash
python3 -m unittest discover tests
```

---

## Weekly Sync (optional)

Automatically posts a portfolio analytics update to a Discord channel every Saturday:

```bash
python3 scripts/weekly_sync.py --portfolio my_portfolio
```

To schedule on macOS with launchd, add a plist to `~/Library/LaunchAgents/`. The script fetches the latest prices, diffs metrics against the prior snapshot, saves an insight note, and posts to Discord.

Requires `DISCORD_WEBHOOK_URL` in `.env`.

---

## Requirements

- Python 3.11+
- Dependencies in `requirements.txt`
- Robinhood account (only for `positions`, `exposure`, `performance`, `advise`)
- Finnhub API key (optional, free — for `insider`/`news`/`earnings` and full ETF constituents)
- FRED API key (optional, free — for yield curve data)

---

## License

MIT
