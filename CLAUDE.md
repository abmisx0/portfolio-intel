# Portfolio Intelligence — Claude Guide

A local ETF research and portfolio analytics tool. Two interfaces: CLI (primary for Claude) and a web dashboard (FastAPI + HTMX). All data flows through a SQLite cache backed by yfinance.

## Quick Start

All CLI commands run from `portfolio-intel/`:

```bash
python3 -m cli <command> [options]
```

**Default portfolio is `live`.** The analysis commands (`screen`, `holdings`, `analytics`, `correlation`, `alerts`) default to `--portfolio live`, which fetches your current Robinhood holdings at call time — no flag needed to analyze your real book. Pass `--portfolio <name>` to use a hypothetical defined in `config.py` instead. See [Portfolio Names](#portfolio-names).

---

## CLI Commands

### `screen` — Screen a candidate ETF against a portfolio
```bash
python3 -m cli screen TICKER --portfolio my_portfolio
python3 -m cli screen QQQ --portfolio my_portfolio --format json
```
Output: trailing returns, risk metrics, correlation to each position, holdings overlap coefficient, effective single-stock concentration, top 10 holdings, ETF metadata (AUM, expense ratio, yield).

### `compare` — Side-by-side comparison of 2+ ETFs
```bash
python3 -m cli compare QQQ SPY                  # pairwise (full cross-overlap analysis)
python3 -m cli compare GLD IAU SGOL GLDM        # multi-ticker metrics + correlation matrix
python3 -m cli compare SMH SOXX QQQ --format json
```
- 2 tickers: detailed pairwise (metrics, trailing returns, correlation, holdings cross-overlap, commodity context)
- 3+ tickers: summary table of all metrics + correlation matrix

### `analytics` — Portfolio-level analytics
```bash
python3 -m cli analytics --portfolio my_portfolio
python3 -m cli analytics -d                       # delta-adjusted: economic weights incl. options
python3 -m cli analytics --portfolio my_portfolio --format json
```
Output: per-position metrics (Sharpe, Sortino, vol, beta, drawdown), theme attribution (1Y), top-20 single-stock exposures, portfolio-level aggregate metrics. `-d`/`--delta-adjusted` (live book only) folds option delta into weights and adds synthetic/option-only positions — see `exposure`.

### `correlation` — Correlation matrix
```bash
python3 -m cli correlation --portfolio my_portfolio
```

### `holdings` — Effective single-stock exposure
```bash
python3 -m cli holdings --portfolio my_portfolio --top 20
```
Output: aggregated stock-level exposure across all ETF positions weighted by allocation.

### `backtest` — Historical comparison of two portfolios
```bash
python3 -m cli backtest --a portfolio_a --b portfolio_b --start 2020-01-01
python3 -m cli backtest --a my_portfolio --b core_satellite --benchmark spx --format json
```
Output: cumulative return, annualized return, vol, Sharpe, Sortino, max drawdown, Calmar, calendar year returns. Benchmark is VOO by default; `--benchmark` accepts `voo` (S&P 500 ETF), `spx` (pure S&P 500 index), `nasdaq` (QQQ), or `russell` (IWM). Selectable benchmarks are defined in `config.BENCHMARKS`. Note this is a **constant-weight** backtest of the given allocation applied historically — for realized money-weighted performance from actual orders, use `performance`.

### `optimize` — Find optimal portfolio weights
```bash
python3 -m cli optimize --portfolio my_portfolio --objective sharpe
python3 -m cli optimize VOO QQQ GLD XLE XLF --objective sortino
python3 -m cli optimize --portfolio my_portfolio --objective sharpe --max-weight 0.35
python3 -m cli optimize --portfolio my_portfolio --objective sharpe \
  --max-weight VOO:0.50 --max-weight QQQ:0.25 --max-weight GLD:0.15
```
Objectives: `sharpe` | `sortino` | `min-vol` | `min-cvar` | `min-drawdown` | `max-return` | `quadratic-utility` | `omega`

`--max-weight` / `--min-weight` accept a plain float (global) or `TICKER:FLOAT` (per-ticker). Default lookback: 3 years.

### `rebalance` — Rebalancing trade list
```bash
python3 -m cli rebalance --portfolio my_portfolio --current "VOO:0.48,QQQ:0.22,GLD:0.09"
```

### `positions` — Live Robinhood holdings
```bash
python3 -m cli positions
python3 -m cli positions --portfolio my_portfolio   # adds drift vs target + trade list
```
Requires `RH_USERNAME` / `RH_PASSWORD` in `.env`. Shows portfolio %, avg cost, G/L%, first purchase date, and LTCG/STCG tax status per position. Fetches account data and purchase dates in parallel.

### `performance` — Money-weighted return vs index benchmarks
```bash
python3 -m cli performance
python3 -m cli performance --benchmark voo --benchmark nasdaq
python3 -m cli performance --format json
```
Computes your **actual realized money-weighted return (XIRR)** from Robinhood order history — every filled buy/sell becomes a dated cash flow — and compares it against the same contributions **cloned into each index** (VOO/QQQ/IWM by default; `--benchmark voo|spx|nasdaq|russell`, repeatable). Cloning holds contribution *timing* fixed so the only variable is asset choice — the honest apples-to-apples comparison, unlike the constant-weight `backtest`.

**Coverage caveat is structural, not optional.** Robinhood's `get_all_stock_orders()` only returns recent orders, and ACATS-transferred positions have no order record at all. `core/performance.py` reconciles per-ticker order shares vs. currently-held shares and computes the XIRR only over the **covered sleeve** (shares the orders actually pay for), reporting the coverage ratio and listing excluded positions. A low coverage ratio means the number describes only a slice of the book — read the printed caveats. Equity only (options/cash excluded); $0-cost grants (free shares) carry no cash flow and are excluded.

### `realized` — Realized gains, income, and margin cost for a tax year
```bash
python3 -m cli realized                 # current tax year
python3 -m cli realized --year 2025
python3 -m cli realized --format json
```
Requires Robinhood login. Reconstructs the tax year read-only from four
history sources: stock orders (FIFO lot matching with ST/LT split by matched-lot
holding period), **option order history at leg-execution level** (roll orders mix
open+close legs, so premium is attributed per leg), **option events**
(assignments/expirations — these never appear as orders; assignment equity
components flow into the stock FIFO), and dividends/interest/margin-interest
records. Output: ST/LT capital gains, per-sale and per-contract detail,
investment income net of margin interest, and collected-but-unrealized premium
on open contracts. **Read the caveats it prints**: wash sales are not modeled,
transferred-in shares have no basis (their P/L is excluded and flagged), and
Robinhood's pagination truncates silently — the app's Tax Center is ground truth.
Use before any tax-ordered trade list: losses offset the short-term option
premium (taxed at ordinary rates) first.

### `exposure` — Delta-adjusted exposure (equity + options)
```bash
python3 -m cli exposure
python3 -m cli exposure --min-weight 0.02      # hide positions under 2% delta-adjusted
python3 -m cli exposure --format json
```
Folds option delta into each ticker's weight so the book reflects **economic** exposure, not just shares held: a short put adds long-equivalent exposure (`synthetic`), a deep-ITM short call cancels equity upside (`⚠ capped`). Two tables: per-ticker equity-vs-delta-adjusted weights, and per-contract option detail with the **full Greek set** (Δ share-equiv, Γ, Θ $/day, ν $/IV-pt, ρ $/rate-pt), each signed for your short/long side and scaled by contracts×100. Header shows book-level Θ/ν/ρ totals.

**Greek source priority**: Robinhood's native per-share Greeks (from `get_option_market_data_by_id`, surfaced in `broker.py` as `broker_greeks` — accounts for American exercise + dividends, matches the app) are used field-by-field when present; `core.exposure._bs_greeks()` (Black-Scholes, European, no dividends) fills any missing field and serves as a cross-check. The `Src` column reads `RH`/`BS`; a `≠` suffix flags positions where the two models disagree on delta by >0.05/share (deep-ITM near expiry, where the European assumption is weakest). IV from the chain (`*` = realized-vol fallback). Equity-only views (`analytics`, `positions`) miss all of this.

### `advise` — Full portfolio advisory
```bash
python3 -m cli advise --portfolio my_portfolio --discoveries 5
python3 -m cli advise -d                           # reason on delta-adjusted economic exposure
python3 -m cli advise --lookback 10Y               # re-run simulation over 1Y/3Y/5Y/10Y window
```
Three sections: trim signals (with ΔSharpe if removed + tax status), watchlist candidates (ranked by marginal portfolio impact), discovery suggestions from curated thematic universe. `-d`/`--delta-adjusted` swaps share weights for economic weights: option delta is folded in, synthetic (option-only) positions are simulated, and names fully capped by short calls shrink toward zero — so trim signals reflect what you actually have at risk, not just shares held. `--lookback {1Y,3Y,5Y,10Y}` re-runs the simulation over a different window — run several to test signal stability across regimes. Positions in `config.THESIS_ANCHORS` are never auto-flagged EXIT/TRIM (shown as `ANCHOR`): a hedge/diversifier drags trailing Sharpe in a bull market by design. The trim signal is a single in-sample, pre-tax, pre-cost window — not trade advice.

Signal semantics: `EXIT`/`TRIM` = removal improves portfolio Sharpe (ΔSharpe > 0.05 / > 0.01); `WEAK` = poor standalone metrics but removal would NOT help; `REDUCE` = position > 25% of book. The simulation inner-joins members on common dates, so positions with less than ~the full lookback of history are **excluded** (reported in the header) rather than silently truncating the window for everyone; the header always prints the **effective window** (start → end, trading days) actually simulated.

### `property` — Real-estate purchase vs index, after tax
```bash
# Backtest: what a 2016 Austin purchase actually did vs the same cash in VOO
python3 -m cli property --price 450000 --rent 2600 --metro "Austin, TX" --backtest-start 2016-07 --hold 7
# Forecast: assumption-driven, with breakeven appreciation
python3 -m cli property --price 450000 --rent 2600 --metro "Austin, TX" --hold 10 --niit
# Owner-occupied variant (imputed rent, §121 exclusion)
python3 -m cli property --price 700000 --rent 3200 --primary --hold 10
```
Simulates a leveraged property month-by-month (amortization, vacancy,
maintenance/capex/insurance/tax, management, HOA) with a 2026 federal tax
layer (27.5y depreciation, passive-loss carryforward released at sale,
recapture at min(bracket, 25%), LTCG, optional `--qbi`/`--niit`,
`--hold-forever` for 1031-until-step-up, `--primary` for §121 + imputed rent)
and compares **equal out-of-pocket dollars** into a benchmark, both sides
after tax. Data: Zillow metro ZHVI/ZORI (free CSVs, weekly-cached in `data/`),
FRED keyless CSVs (Case-Shiller, PMMS mortgage rates). **Backtest** mode uses
actual metro price/rent paths and actual benchmark prices; **forecast** mode
uses metro-CAGR defaults (overridable) and an assumed benchmark return, and
prints the **breakeven appreciation** — the annual appreciation above which
the property beats the index. Risk block de-smooths index vol (×1.6 Geltner
factor + 10% idiosyncratic) and levers by the down payment — index vol badly
understates single-house risk. Defaults follow 2026 norms (0.9% property tax,
0.6% insurance, 1%+0.5% maintenance+capex, 5% vacancy, 3%/7% closing costs,
~5.5% post-NAR commissions inside the 7%). Read the caveats: federal-only,
no state tax/SALT/AMT, no cost-seg bonus depreciation, not tax advice.

### `buyrent` — Should I buy this specific listing?
```bash
python3 -m cli buyrent --price 1500000 --rent 4800 --metro "Los Angeles, CA" --hoa 550
python3 -m cli buyrent --price 700000 --rent 3000 --benchmark-return 11 --format json
```
Owner-occupied buy-vs-rent verdict for one listing: `--rent` is what the **same
unit** rents for (imputed rent). Prints the monthly own-vs-rent cost split,
the price-to-rent ratio with its conventional zone (<18 buy-leaning, >22
rent-leaning), and BUY-vs-RENT terminal wealth across 5/10/20-year holds with
the breakeven appreciation per horizon — appreciation defaults to the metro's
10-year CAGR. Thin wrapper over the `property` forecast engine (same tax and
comparison model; §121 applies at 2+ years). The verdict is financial only —
it prices stability/roots at $0 and assumes the monthly difference is
actually invested.

### `analysts` — Analyst consensus and price targets
```bash
python3 -m cli analysts MU AVGO TSM
python3 -m cli analysts --portfolio my_portfolio
python3 -m cli analysts NVDA --format json
```
Output: consensus rating (Strong Buy → Strong Sell) with 1–5 score, mean/high/low price targets, implied upside to mean, breakdown by analyst count (SB/B/H/S/SS), and last 10 rating changes with firm names and dates. Results cached in SQLite for 1 day.

### `valuation` — Valuation multiples
```bash
python3 -m cli valuation PFE LLY ABBV
python3 -m cli valuation --portfolio live
python3 -m cli valuation SMH NVDA --format json
```
Output: trailing/forward P/E, P/S, P/B, EV/EBITDA, profit margin, dividend yield, market cap for stocks; fund-level P/E / P/B / P/S, expense ratio, AUM for ETFs. yfinance unit quirks (percent-vs-fraction yields, reciprocal fund ratios) are normalised in `core/valuation.py` so output is always consistent. Results cached in SQLite for 1 day. Use this as context alongside risk metrics — a trim/add signal reads differently at 9x forward earnings than at 60x.

### `growth` — Consensus forward revenue/EPS growth
```bash
python3 -m cli growth NFLX LLY NVDA
python3 -m cli growth --portfolio live
python3 -m cli growth SMH --format json     # ETFs return no estimates — see note
```
Output: consensus revenue and EPS growth for the current (FY0) and next (FY1)
fiscal year, long-term growth (LTG) when published, forward P/E, **PEG**
(fwd P/E ÷ LTG, falling back to FY1 EPS growth — the JSON `peg_basis` field
records which), profit margin, analyst count, and the S&P 500 LTG baseline for
comparison. Results cached in SQLite for 1 day; info-derived fields are shared
with the `valuation` cache (one .info fetch per ticker per day). Transient
fetch failures are reported and **not** cached; no-coverage tickers are.
ETFs carry no consensus estimates — run `growth` on their top holdings instead
(get them from `screen`). **Reading caveats**: PEG breaks on rebound years
(COIN-style −75%→+338% swings make PEG meaningless), FY0 can hide one-time items
(e.g. a breakup fee inflating the base year), and estimates with <10 analysts are
noise. Pair with `earnings` (beat/miss history) to judge whether consensus is
credible: a rich PEG with consistent beats reads differently than one with misses.

### `earnings` — EPS surprise history and forward estimates
```bash
python3 -m cli earnings NVDA MU --quarters 4
python3 -m cli earnings AAPL --forward      # forward estimates need Finnhub paid plan
```
Historical EPS beats/misses (Finnhub). Use alongside `growth`: surprise history
is the execution-quality check on consensus estimates.

### `insider` — Insider transaction filings
```bash
python3 -m cli insider NVDA MU
python3 -m cli insider AAPL --buys-only
```
SEC Form 4 insider transactions (Finnhub) — date, insider name, transaction
type, shares, price, value. `--buys-only` filters to open-market purchases.
`--limit` caps rows per ticker (default 10).

### `news` — Company news and sentiment
```bash
python3 -m cli news NVDA MU --days 14
python3 -m cli news AAPL --headlines-only
```
Recent headlines (Finnhub) with an aggregate bullish/bearish sentiment score
vs. sector average when available. `--headlines-only` skips the sentiment
call; sentiment requires a Finnhub paid plan and degrades gracefully (noted
in output) when unavailable.

### `macro` — Market regime snapshot
```bash
python3 -m cli macro
python3 -m cli macro --format json
```
Output: VIX, S&P 500, Gold, WTI, Dollar index (level + 1M/1Y change), Treasury curve with 10Y–2Y spread and shape label, and the live risk-free rate. Pure cache/yfinance + treasury.gov — no keys required. Run first in any checkup to frame recommendations in the current regime.

### `technicals` — Price-action levels
```bash
python3 -m cli technicals COIN XLV AAPL
python3 -m cli technicals --portfolio live
```
Output: last price, SMA50/SMA200, RSI(14), 52-week range, % from 52w high, and swing support/resistance (5-day pivots over trailing 6 months). Computed entirely from the SQLite price cache — no extra API calls. Use for entry/exit zone guidance alongside `analysts` targets and `valuation` multiples; levels are zones, not lines.

### `watchlist` — Manage candidate ETFs
```bash
python3 -m cli watchlist add TICKER
python3 -m cli watchlist list
python3 -m cli watchlist remove TICKER
```

### `alerts` — Correlation/threshold alerts
```bash
python3 -m cli alerts --portfolio my_portfolio
```

### `publish` — Push a report to the dashboard
```bash
python3 -m cli publish --title "Portfolio Checkup — 2026-06-12" --body-file report.md
python3 -m cli publish --title "..." --body-file report.md --metrics '{"sharpe_3y": 1.89}'
```
Saves a markdown report as an Insights entry on the web dashboard and posts it
to Discord (`DISCORD_WEBHOOK_URL`). `--metrics` is an optional JSON dict of
headline numbers to surface on the Insights card. Used by the `/portfolio-checkup`
skill to publish the final report after a review.

### Web dashboard
```bash
python3 -m cli start   # launches FastAPI at http://127.0.0.1:8000
```

---

## Portfolio Names

Pass to `--portfolio`:

| Name | Description | Key Tickers |
|------|-------------|-------------|
| `live` | **(default)** Current Robinhood holdings, fetched live | Whatever you hold right now |
| `core_satellite` | Broad market core + thematic satellites | VOO 50%, QQQ 20%, GLD 10%, BND 10%, VNQ 10% |
| `thematic` | Sector-based by macro thesis | SMH 25%, XLE 20%, XLF 20%, GLD 15%, VHT 10%, BND 10% |

**`live` is a reserved, dynamic name** — not a static config entry. It is resolved by `config.resolve_portfolio()`, which logs into Robinhood and returns current holdings with weights normalized to sum to 1.0 across invested equity (cash excluded). Fetched once per process and cached. It is always current, never a saved snapshot. Robinhood carries no theme metadata, so `config.TICKER_THEMES` maps each ticker to a thesis label for theme attribution — add new tickers there as your book evolves; unmapped tickers fall back to `"Other"`.

`optimize` and `analysts` accept `--portfolio live` but do not default to it (they require explicit input). `backtest` and `rebalance` keep named-portfolio semantics (historical comparison / target allocation).

To add your own hypothetical portfolio, edit `PORTFOLIOS` in `config.py`:
```python
PORTFOLIOS["my_portfolio"] = [
    {"ticker": "VOO",  "weight": 0.50, "theme": "Broad Market", "role": "S&P 500 anchor"},
    {"ticker": "SMH",  "weight": 0.25, "theme": "Technology",   "role": "AI compute thesis"},
    # weights must sum to 1.0
]
```

---

## Output Format

All commands support `--format table` (default) or `--format json`. JSON always uses the same envelope:

```json
{
  "command": "compare",
  "args": {"ticker_a": "QQQ", "ticker_b": "SPY"},
  "timestamp": "2026-05-17T10:00:00Z",
  "data": { ... },
  "metadata": {"data_freshness": "2026-05-16"}
}
```

---

## Architecture

```
portfolio-intel/
├── config.py                  # Portfolio definitions, API keys, constants
├── data/cache.db              # SQLite: price history + ETF holdings cache
├── core/
│   ├── data_fetcher.py        # yfinance wrapper with SQLite cache + batch prefetch
│   ├── analytics.py           # Sharpe, Sortino, beta, correlation, drawdown, trailing returns
│   ├── backtester.py          # Historical portfolio comparison engine
│   ├── screener.py            # screen() + compare() + compare_multi()
│   ├── holdings.py            # ETF holdings decomposition, overlap analysis
│   ├── optimizer.py           # SLSQP optimizer, 8 objectives, multi-start
│   ├── macro.py               # VIX, yield curve, WTI, Gold, risk-free rate (^IRX)
│   ├── rebalancer.py          # Trade list generation
│   ├── broker.py              # Robinhood integration — read-only via robin_stocks
│   ├── research.py            # Advisory scoring engine
│   ├── valuation.py           # Valuation multiples (P/E, P/S, EV/EBITDA; fund-level for ETFs)
│   ├── growth.py              # Consensus forward revenue/EPS growth, PEG vs S&P baseline
│   ├── exposure.py            # Delta-adjusted exposure, Black-Scholes + Robinhood-native Greeks
│   ├── performance.py         # Money-weighted (XIRR) realized return vs index benchmarks
│   ├── realized.py            # Realized gains/income/margin cost for a tax year (FIFO + option ledger)
│   ├── realestate.py          # Zillow/FRED fetchers, amortization, leveraged property tax model
│   ├── analysts.py            # Analyst consensus and price targets
│   ├── technicals.py          # SMA/RSI/52w range/support-resistance from price cache
│   ├── finnhub.py             # Finnhub client — insider, news, earnings, ETF constituents
│   ├── cache.py               # Shared SQLite JSON cache (cached_json)
│   ├── watchlist.py           # Watchlist persistence
│   ├── alerts.py              # Correlation/threshold alerts
│   ├── insights.py            # Analytics snapshots and sync notes
│   └── notifier.py            # Discord webhook poster
├── cli/
│   ├── main.py                # Click group, command registration
│   ├── formatters.py          # All table + JSON output formatters
│   └── commands/              # One file per CLI command
└── app/                       # FastAPI web dashboard
```

### Key design decisions

**Data layer**: All price data cached in SQLite. `get_close_series(ticker, start, end)` checks cache first, fetches only missing date ranges from yfinance, never re-fetches full history. Ranges that return empty from all providers (pre-listing history) are negative-cached in `missing_ranges` and never re-requested. Close-only rows (batch prefetch) store OHLC/volume as NULL, never fabricated zeros. ETF holdings cached with 7-day TTL. `prefetch_prices()` batch-downloads all tickers in one yfinance call before any per-ticker loop. JSON API payload caching is centralised in `core/cache.py` (`cached_json`).

**ETF holdings source priority** (`core/holdings.py`): Finnhub full constituents (paid endpoint, used when `FINNHUB_API_KEY` plan allows — yfinance only exposes top 10, which understates overlap/concentration math) → yfinance top-10 → `seed_holdings.json`.

**yfinance unit quirks** (normalised at fetch, in `data_fetcher._fetch_etf_info_yf` and `core/valuation.py`): fund `yield` is a fraction, stock `dividendYield` and `netExpenseRatio` are in percent, fund-level P/E ratios arrive as reciprocals (earnings yield). Everything stored in cache is a fraction / true multiple.

**Robinhood (broker.py)**: Read-only. Only these robin_stocks calls are permitted: `rh.login()`, `rh.account.build_holdings()`, `rh.profiles.load_portfolio_profile()`, `rh.options.get_open_option_positions()`, `rh.options.get_option_instrument_data_by_id()`, `rh.options.get_option_market_data_by_id()`, `rh.orders.get_all_open_option_orders()`, `rh.orders.get_all_stock_orders()`, `rh.stocks.get_instrument_by_url()`, `rh.get_watchlist_by_name()`. The `rh` module object is never returned to callers — all public functions return plain Python dicts.

**Benchmark**: VOO by default for beta/correlation. `^SPX` available as `--benchmark spx` (pure index, no fees).

**Risk-free rate**: Dynamic — trailing 90-day average of ^IRX (13-week T-bill), falls back to `RISK_FREE_RATE=0.045` in config if fetch fails.

**Optimizer**: SLSQP via scipy. Non-convex objectives (sharpe, sortino, omega, min-cvar) use 5 random starts to escape local minima. Convex objectives (min-vol, max-return) use single start. Default 3Y lookback.

**Commodity context**: Energy ETFs (VDE, XLE, XOP, OIH) show WTI beta/correlation. Gold ETFs (GLD, IAU, GOAU, GDX, etc.) show Gold futures beta/correlation. Nuclear/uranium ETFs are excluded — uranium spot prices are not freely available.

**Purchase date tracking**: `get_purchase_dates()` fetches full order history from Robinhood, pre-resolves all instrument URLs in parallel (ThreadPoolExecutor, 8 workers), and returns per-ticker LTCG/STCG lot summary. Called in parallel with `get_account_data()` at the `positions` and `advise` call sites.

---

## Key Constants (config.py)

- `BENCHMARK_TICKER = "VOO"` — used for beta across all analytics
- `LOOKBACK_5Y` — rolling 5Y start date for price fetches
- `RISK_FREE_RATE = 0.045` — fallback if ^IRX unavailable
- `HOLDINGS_CACHE_TTL_DAYS = 7` — ETF holdings refresh frequency
- `TRADING_DAYS = 252` — annualization constant (defined in `core/analytics.py`)

---

## Working with Users

This tool is built for thesis-driven investors building their own portfolios. When working with a new user, Claude should:

1. **Ask for their investment thesis** — what macro themes or sectors are they expressing conviction in? What is their time horizon and risk tolerance?
2. **Ask for their current portfolio** — tickers and approximate weights, or sync live positions via `positions`
3. **Track portfolio evolution** — save named versions in `config.py` (e.g., `v1`, `v2`, `my_aggressive`) as allocations change across sessions, with comments explaining what changed and why
4. **Validate with data** — run CLI tools to confirm every claim with actual numbers. Use `compare` to evaluate ETF alternatives within a theme, `screen` to assess fit, and `optimize` to surface weight signals
5. **Ask for optimizer constraints** — some positions are thesis anchors that should not be eliminated even if the optimizer suggests it. Ask which positions are non-negotiable

### Recommended workflow for portfolio construction

```bash
# 1. Define your thesis ETFs in config.py, then screen each one:
python3 -m cli screen SMH --portfolio my_portfolio

# 2. Compare alternatives within a theme:
python3 -m cli compare SMH SOXX QQQ

# 3. Run optimizer across multiple objectives and lookbacks:
python3 -m cli optimize --portfolio my_portfolio --objective sharpe
python3 -m cli optimize --portfolio my_portfolio --objective omega --start 2020-01-01

# 4. Signal-average Tier 1 objectives (Sharpe + Omega) across 3Y and 5Y lookbacks.
#    Thesis anchors should override degenerate optimizer outputs.

# 5. Save the new allocation as a named version in config.py and backtest it:
python3 -m cli backtest --a my_portfolio_v2 --b my_portfolio_v1 --start 2020-01-01
```

### Optimizer objective reliability

| Tier | Objective | Use |
|------|-----------|-----|
| 1 | Sharpe, Omega | Primary allocation signals — average 3Y and 5Y runs |
| 2 | Sortino | Directional reference; may be inflated for ETFs with short history |
| 3 | CVaR, min-drawdown, quadratic-utility | Sanity check only — often produce degenerate outputs |

---

## Robinhood Integration Notes

- `positions` and `advise` commands require Robinhood login via `.env`
- **Set `RH_MFA_SECRET`** (authenticator-app TOTP setup key, requires `pip install pyotp`) to skip the device-approval login flow — it is flaky under rate limits and times out if the in-app prompt isn't approved quickly
- Purchase date tracking uses `rh.orders.get_all_stock_orders()` — fetches full order history
- Instrument URL → ticker resolution is cached in `_instrument_cache` (module-level dict, process lifetime)
- Tax status per position: `has_short_term_lots` uses `any(d > one_year_ago for d in dates)` — correct for multi-lot DCA positions
- LTCG boundary: `last_purchase + 1 year` = `ltcg_all_lots_date` (when all lots turn LTCG)
- Leap-day guard applied to `date.replace(year=year+1)`
