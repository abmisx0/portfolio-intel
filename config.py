"""
Central configuration: portfolio definitions, API settings, and constants.
"""
from __future__ import annotations

from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).parent
DATA_DIR = ROOT_DIR / "data"
CACHE_DB_PATH = DATA_DIR / "cache.db"
PORTFOLIOS_JSON_PATH = DATA_DIR / "portfolios.json"

DATA_DIR.mkdir(exist_ok=True)

# ── Portfolio Definitions ─────────────────────────────────────────────────────

# Each entry: {"ticker": str, "weight": float (0–1), "theme": str, "role": str}

PORTFOLIOS: dict[str, list[dict]] = {
    "proposed": [
        {"ticker": "VOO",  "weight": 0.30, "theme": "Broad Market",      "role": "S&P 500 core anchor"},
        {"ticker": "NLR",  "weight": 0.15, "theme": "Nuclear Energy",    "role": "Nuclear renaissance — full value chain"},
        {"ticker": "SMH",  "weight": 0.12, "theme": "Technology",        "role": "Semiconductor / AI conviction bet"},
        {"ticker": "GOAU", "weight": 0.10, "theme": "Precious Metals",   "role": "Gold royalty & streaming companies"},
        {"ticker": "XLRE", "weight": 0.10, "theme": "Real Estate",       "role": "Real asset ballast, rate-sensitivity diversification"},
        {"ticker": "LIT",  "weight": 0.05, "theme": "Critical Materials","role": "Battery/EV demand-side rare earth exposure"},
        {"ticker": "VDE",  "weight": 0.05, "theme": "Traditional Energy","role": "Oil & gas — geopolitical / commodity cycle"},
        {"ticker": "QTUM", "weight": 0.03, "theme": "Frontier Tech",     "role": "Quantum computing — speculative, long-dated"},
        # 10% flex (cash/bonds) — excluded from analytics unless explicitly included
    ],
    "previous": [
        {"ticker": "VOO",  "weight": 0.25, "theme": "Broad Market",    "role": ""},
        {"ticker": "VGT",  "weight": 0.15, "theme": "Technology",      "role": ""},
        {"ticker": "SMH",  "weight": 0.15, "theme": "Technology",      "role": ""},
        {"ticker": "URA",  "weight": 0.15, "theme": "Nuclear/Uranium", "role": ""},
        {"ticker": "RING", "weight": 0.10, "theme": "Gold Miners",     "role": ""},
        {"ticker": "XLRE", "weight": 0.10, "theme": "Real Estate",     "role": ""},
        {"ticker": "REMX", "weight": 0.05, "theme": "Rare Earth Miners","role": ""},
        {"ticker": "QTUM", "weight": 0.05, "theme": "Quantum Computing","role": ""},
    ],
    # v6: active portfolio — AMLP removed, SLV added — 2026-03-22
    # AMLP dropped: 10Y Sharpe 0.345, -72.6% max DD, negative 4 of 7 years 2014-2020
    # SLV added: 10Y Sharpe 0.532 (beats AMLP and VDE on 10Y), selected in 4/4 optimizer objectives
    # AMLP's 5% redistributed to QTUM (restores full 10% frontier weight)
    # All positions evaluated on 5Y and 10Y Sharpe before inclusion
    "v6": [
        {"ticker": "SMH",  "weight": 0.30, "theme": "Technology",    "role": "AI compute — 10Y Sharpe 0.935, 10Y +1439%; best long-run performer in universe"},
        {"ticker": "PPA",  "weight": 0.20, "theme": "Defense",       "role": "Aerospace & defense — 10Y Sharpe 0.791, 10Y +421%; cost-plus inflation pass-through"},
        {"ticker": "NLR",  "weight": 0.15, "theme": "Nuclear Energy","role": "Nuclear renaissance — 10Y Sharpe 0.553; AI power demand tailwind"},
        {"ticker": "SLV",  "weight": 0.15, "theme": "Precious Metals","role": "Silver — 10Y Sharpe 0.532, 10Y +308%; industrial demand (solar, EV, AI hardware); selected 4/4 optimizer objectives"},
        {"ticker": "VDE",  "weight": 0.10, "theme": "Energy",        "role": "Geopolitical energy hedge — 10Y Sharpe 0.400, beats AMLP on every long-run metric"},
        {"ticker": "QTUM", "weight": 0.10, "theme": "Frontier Tech", "role": "Quantum computing — 5Y Sortino optimizer selected at 32.5%; only purpose-built quantum ETF"},
    ],

    # ── v6 Constrained: per-ticker caps enforced — 2026-03-22 ────────────────────
    # Caps: PPA ≤ 25%, NLR ≤ 20%, VDE ≤ 15%, SLV ≤ 10% (per-ticker optimizer confirmed)
    # SLV hits cap in 6/6 runs. NLR hits cap in 4/6. PPA hits cap in 3/6.
    # QTUM avg signal 18.8% across 5Y runs — strong uncapped preference.
    # Two candidates differ only in QTUM vs VDE for the residual 5%:

    # v6_qtum: residual goes to QTUM (favored by 5Y Sortino 34%, CVaR 30%)
    "v6_qtum": [
        {"ticker": "SMH",  "weight": 0.30, "theme": "Technology",    "role": "AI compute — 5Y avg 24% uncapped, hits 35% ceiling in 3/4 5Y runs"},
        {"ticker": "PPA",  "weight": 0.25, "theme": "Defense",       "role": "Defense — at 25% cap; hits ceiling in 3/6 runs"},
        {"ticker": "NLR",  "weight": 0.20, "theme": "Nuclear Energy","role": "Nuclear renaissance — at 20% cap; hits ceiling in 4/6 runs"},
        {"ticker": "QTUM", "weight": 0.10, "theme": "Frontier Tech", "role": "Quantum — avg 18.8% uncapped signal; 34% in 5Y Sortino"},
        {"ticker": "SLV",  "weight": 0.10, "theme": "Precious Metals","role": "Silver — at 10% cap; hits ceiling in 6/6 runs (always wants more)"},
        {"ticker": "VDE",  "weight": 0.05, "theme": "Energy",        "role": "Geopolitical hedge — minimal; 5Y avg 10.6% but weakest signal"},
    ],
    # v6_vde: residual goes to VDE (geopolitical hedge preserved at 10%)
    "v6_vde": [
        {"ticker": "SMH",  "weight": 0.30, "theme": "Technology",    "role": "AI compute — 5Y avg 24% uncapped signal"},
        {"ticker": "PPA",  "weight": 0.25, "theme": "Defense",       "role": "Defense — at 25% cap"},
        {"ticker": "NLR",  "weight": 0.20, "theme": "Nuclear Energy","role": "Nuclear renaissance — at 20% cap"},
        {"ticker": "VDE",  "weight": 0.10, "theme": "Energy",        "role": "Geopolitical energy hedge — preserved at 10%; crucial in 2022/2026 oil shocks"},
        {"ticker": "SLV",  "weight": 0.10, "theme": "Precious Metals","role": "Silver — at 10% cap"},
        {"ticker": "QTUM", "weight": 0.05, "theme": "Frontier Tech", "role": "Quantum — reduced; VDE holds geopolitical protection"},
    ],

    # ── v6 Constrained Variants (2026-03-22) ─────────────────────────────────────
    # Caps enforced: PPA ≤ 25%, NLR ≤ 20%, VDE ≤ 15%, SLV ≤ 10%
    # SLV surplus (~11pp vs unconstrained signal) redistributed to SMH (uncapped, top 5Y pick)
    # PPA raised to cap — hits 25% in 3/6 optimizer runs

    # v6_caps_a: signal-faithful redistribution
    # SMH absorbs most of SLV surplus; QTUM holds at 10% (strong 5Y Sortino signal)
    "v6_caps_a": [
        {"ticker": "SMH",  "weight": 0.30, "theme": "Technology",    "role": "AI compute — uncapped; absorbs SLV surplus"},
        {"ticker": "PPA",  "weight": 0.25, "theme": "Defense",       "role": "Defense — raised to 25% cap; hits ceiling in 3/6 optimizer runs"},
        {"ticker": "NLR",  "weight": 0.15, "theme": "Nuclear Energy","role": "Nuclear renaissance — within 20% cap"},
        {"ticker": "SLV",  "weight": 0.10, "theme": "Precious Metals","role": "Silver industrial demand — capped at 10%"},
        {"ticker": "VDE",  "weight": 0.10, "theme": "Energy",        "role": "Geopolitical energy hedge — within 15% cap"},
        {"ticker": "QTUM", "weight": 0.10, "theme": "Frontier Tech", "role": "Quantum computing — selected 16-25% in 5Y Sortino/CVaR runs"},
    ],
    # v6_caps_b: tech-max — SMH absorbs all SLV surplus, QTUM trimmed
    "v6_caps_b": [
        {"ticker": "SMH",  "weight": 0.35, "theme": "Technology",    "role": "AI compute — maximum conviction; 10Y Sharpe 0.935"},
        {"ticker": "PPA",  "weight": 0.25, "theme": "Defense",       "role": "Defense — at 25% cap"},
        {"ticker": "NLR",  "weight": 0.15, "theme": "Nuclear Energy","role": "Nuclear renaissance — within 20% cap"},
        {"ticker": "SLV",  "weight": 0.10, "theme": "Precious Metals","role": "Silver industrial demand — capped at 10%"},
        {"ticker": "VDE",  "weight": 0.10, "theme": "Energy",        "role": "Geopolitical energy hedge"},
        {"ticker": "QTUM", "weight": 0.05, "theme": "Frontier Tech", "role": "Quantum computing — reduced; SMH absorbs weight"},
    ],

    # ── Capped-Anchor Portfolios (2026-03-22) ─────────────────────────────────────
    # Constraint: PPA ≤ 20%, NLR ≤ 15%, VDE ≤ 10%, AMLP ≤ 5% → frees 50% for rotation
    # Free-pool optimizer (Sharpe/Sortino/max-return on 9-ticker candidate universe) surfaced:
    #   XLF (financials) and SLV (silver) — both selected in 2/3 objectives
    #   EWY (South Korea semis) — selected in Sharpe + Sortino
    #   SMH — selected in all 3 at varying weights

    # v7_finance_silver: Sharpe optimizer — financials + silver + semiconductors fill the free 50%
    # XLF thesis: bank deregulation cycle, higher-for-longer margin expansion, payments consolidation
    # SLV thesis: industrial silver demand (solar cells, EV electronics, AI hardware) + monetary bid
    "v7_finance_silver": [
        {"ticker": "PPA",  "weight": 0.20, "theme": "Defense",       "role": "Aerospace & defense — capped at 20%"},
        {"ticker": "SMH",  "weight": 0.20, "theme": "Technology",    "role": "AI compute — semiconductors; optimizer preferred at 13-18% free-pool"},
        {"ticker": "NLR",  "weight": 0.15, "theme": "Nuclear Energy","role": "Nuclear renaissance — AI power demand"},
        {"ticker": "XLF",  "weight": 0.15, "theme": "Financials",    "role": "Deregulation + rate margins — banks, insurance, payments; #1 Sharpe optimizer pick"},
        {"ticker": "SLV",  "weight": 0.15, "theme": "Precious Metals","role": "Silver — industrial demand (solar, EV, AI hardware) + monetary hedge; selected all 3 objectives"},
        {"ticker": "VDE",  "weight": 0.10, "theme": "Energy",        "role": "Geopolitical energy hedge — WTI beta 0.44"},
        {"ticker": "AMLP", "weight": 0.05, "theme": "Energy Infra",  "role": "Midstream toll-road — AI gas demand, 7.6% yield"},
    ],
    # v7_korea_silver: Sortino optimizer — Korea semiconductors + silver + financials
    # EWY thesis: Samsung + SK Hynix dominate HBM memory for AI GPUs; KOSPI governance reforms;
    #             Korea is the dominant supplier of the memory layer of the AI stack
    "v7_korea_silver": [
        {"ticker": "SMH",  "weight": 0.20, "theme": "Technology",    "role": "AI compute — US semiconductor ETF (NVDA, ASML, AMAT)"},
        {"ticker": "PPA",  "weight": 0.20, "theme": "Defense",       "role": "Aerospace & defense — capped at 20%"},
        {"ticker": "EWY",  "weight": 0.15, "theme": "Korea Semis",   "role": "Samsung + SK Hynix — HBM memory for AI; Korea AI supply chain"},
        {"ticker": "NLR",  "weight": 0.15, "theme": "Nuclear Energy","role": "Nuclear renaissance — AI power demand"},
        {"ticker": "VDE",  "weight": 0.10, "theme": "Energy",        "role": "Geopolitical energy hedge"},
        {"ticker": "SLV",  "weight": 0.10, "theme": "Precious Metals","role": "Silver — industrial AI hardware demand + monetary metal"},
        {"ticker": "XLF",  "weight": 0.05, "theme": "Financials",    "role": "Financials — deregulation tailwind, reduced conviction weight"},
        {"ticker": "AMLP", "weight": 0.05, "theme": "Energy Infra",  "role": "Midstream toll-road — AI gas demand, 7.6% yield"},
    ],
    # v7_smh_silver: Max-return optimizer — SMH + SLV at high conviction, minimal everything else
    # Character: Two highest-return assets (AI chips + industrial silver) dominate
    # Accepts higher volatility in exchange for maximum upside
    "v7_smh_silver": [
        {"ticker": "SMH",  "weight": 0.30, "theme": "Technology",    "role": "AI compute — maximum semiconductor weight"},
        {"ticker": "PPA",  "weight": 0.20, "theme": "Defense",       "role": "Aerospace & defense — capped at 20%"},
        {"ticker": "NLR",  "weight": 0.15, "theme": "Nuclear Energy","role": "Nuclear renaissance — AI power demand"},
        {"ticker": "SLV",  "weight": 0.15, "theme": "Precious Metals","role": "Silver — max-return optimizer top pick alongside SMH"},
        {"ticker": "VDE",  "weight": 0.10, "theme": "Energy",        "role": "Geopolitical energy hedge"},
        {"ticker": "AMLP", "weight": 0.05, "theme": "Energy Infra",  "role": "Midstream toll-road"},
        {"ticker": "QTUM", "weight": 0.05, "theme": "Frontier Tech", "role": "Quantum computing"},
    ],

    # ── Creative Alternatives (2026-03-22) ───────────────────────────────────────
    # Three archetypes exploring reduced defense/energy with different sector rotations.
    # All sum to 100%. Backtested and analytics-compared against v5 as baseline.

    # v6_ai_power: AI infrastructure stack — max compute + power + grid; defense minimal
    # Character: Pure AI value chain — chips, nuclear power, midstream gas, smart grid
    # Reduces: PPA 30%→10%, VDE eliminated entirely
    # Adds: GRID (data center power/Vertiv/Eaton), AMLP scaled up to anchor energy infra
    "v6_ai_power": [
        {"ticker": "SMH",  "weight": 0.30, "theme": "Technology",    "role": "AI compute — semiconductors; best ETF for AI chip value chain"},
        {"ticker": "NLR",  "weight": 0.25, "theme": "Nuclear Energy","role": "AI power demand — nuclear utilities + full value chain"},
        {"ticker": "AMLP", "weight": 0.20, "theme": "Energy Infra",  "role": "Gas delivery to data centers — toll-road revenue, 7.6% yield"},
        {"ticker": "PPA",  "weight": 0.10, "theme": "Defense",       "role": "Reduced defense anchor — AI defense applications, cost-plus inflation pass-through"},
        {"ticker": "GRID", "weight": 0.10, "theme": "Power Infra",   "role": "Data center power & cooling — Vertiv, Eaton, Hubbell; direct AI power beneficiary"},
        {"ticker": "QTUM", "weight": 0.05, "theme": "Frontier Tech", "role": "Quantum computing — next-gen AI compute adjacency"},
    ],
    # v6_diversified: Genuine sector breadth — adds PAVE, keeps reduced defense + energy
    # Character: Broad conviction across 6 themes; no single sector >25%
    # Reduces: PPA 30%→15%, VDE 15%→5%
    # Adds: PAVE (construction/infrastructure) — CHIPS Act fabs, data center campuses
    "v6_diversified": [
        {"ticker": "SMH",  "weight": 0.25, "theme": "Technology",    "role": "AI compute — semiconductors"},
        {"ticker": "PPA",  "weight": 0.15, "theme": "Defense",       "role": "Reduced defense — geopolitical tailwind, inflation pass-through"},
        {"ticker": "NLR",  "weight": 0.15, "theme": "Nuclear Energy","role": "Nuclear renaissance — AI power demand, full value chain"},
        {"ticker": "AMLP", "weight": 0.15, "theme": "Energy Infra",  "role": "Midstream toll-road — AI gas demand, LNG export, inflation escalators"},
        {"ticker": "PAVE", "weight": 0.15, "theme": "Industrials",   "role": "US infrastructure construction — CHIPS Act fabs, data center campuses, reshoring"},
        {"ticker": "QTUM", "weight": 0.10, "theme": "Frontier Tech", "role": "Quantum computing — only purpose-built quantum ETF"},
        {"ticker": "VDE",  "weight": 0.05, "theme": "Energy",        "role": "Residual geopolitical energy hedge — oil commodity exposure"},
    ],
    # v6_tech_heavy: Max tech conviction — SMH+QTUM at 50%; defense and energy supporting roles
    # Character: Bet on technology dominating returns; everything else is ballast
    # Reduces: PPA 30%→15%, VDE+AMLP combined 20%→15%
    # Increases: SMH 25%→35%, QTUM 10%→15%
    "v6_tech_heavy": [
        {"ticker": "SMH",  "weight": 0.35, "theme": "Technology",    "role": "AI compute — maximum semiconductor conviction"},
        {"ticker": "NLR",  "weight": 0.20, "theme": "Nuclear Energy","role": "AI power + nuclear renaissance — increased weight as AI power thesis matures"},
        {"ticker": "QTUM", "weight": 0.15, "theme": "Frontier Tech", "role": "Quantum computing — scaled up as AI adjacency bet"},
        {"ticker": "PPA",  "weight": 0.15, "theme": "Defense",       "role": "Defense anchor — geopolitical tailwind, lower conviction weight"},
        {"ticker": "AMLP", "weight": 0.15, "theme": "Energy Infra",  "role": "Energy infrastructure — AI gas demand; replaces VDE entirely"},
        # No VDE — accepts loss of geopolitical commodity hedge to free weight for tech
    ],

    # v5: v4 + AMLP deployed to 5% flex slot — 2026-03-22
    # VDE stays (geopolitical hedge); AMLP adds midstream toll-road income + AI gas demand thesis
    # First full allocation (100%); PAVE/GRID removed from watch — AMLP wins the flex slot on data
    "v5": [
        {"ticker": "PPA",  "weight": 0.30, "theme": "Defense",           "role": "Aerospace & defense — Sharpe 0.91/Sortino 1.37, beats ITA on every metric"},
        {"ticker": "SMH",  "weight": 0.25, "theme": "Technology",        "role": "Semiconductor / AI compute — best semi ETF; 5Y +239% vs SOXX +154%"},
        {"ticker": "NLR",  "weight": 0.15, "theme": "Nuclear Energy",    "role": "Nuclear renaissance full value chain — Sharpe 0.72 vs URA 0.49; AI power demand tailwind"},
        {"ticker": "VDE",  "weight": 0.15, "theme": "Traditional Energy","role": "Oil & gas geopolitical hedge — WTI beta 0.44, corr 0.58; best risk-adj energy ETF"},
        {"ticker": "QTUM", "weight": 0.10, "theme": "Frontier Tech",     "role": "Quantum computing — only purpose-built quantum ETF; next-gen AI compute adjacency"},
        {"ticker": "AMLP", "weight": 0.05, "theme": "Energy Infra",      "role": "Midstream MLP — toll-road revenue; Sharpe 0.90; AI data center gas demand tailwind; 7.6% yield"},
    ],
    # v5_amlp: v4 with VDE → AMLP (direct swap) — 2026-03-22
    # Tests AMLP vs VDE in isolation; optimizer eliminated VDE in 3/4 risk-adjusted objectives
    # AMLP: Sharpe 0.90 vs VDE 0.79; lower vol (20.7% vs 26.7%); toll-road revenue model
    "v5_amlp": [
        {"ticker": "PPA",  "weight": 0.30, "theme": "Defense",           "role": "Aerospace & defense — Sharpe 0.91/Sortino 1.37"},
        {"ticker": "SMH",  "weight": 0.25, "theme": "Technology",        "role": "Semiconductor / AI compute — best semi ETF"},
        {"ticker": "NLR",  "weight": 0.15, "theme": "Nuclear Energy",    "role": "Nuclear renaissance full value chain"},
        {"ticker": "AMLP", "weight": 0.15, "theme": "Energy Infra",      "role": "Midstream MLP — toll-road revenue; Sharpe 0.90 vs VDE 0.79; AI gas demand tailwind"},
        {"ticker": "QTUM", "weight": 0.10, "theme": "Frontier Tech",     "role": "Quantum computing — only purpose-built quantum ETF"},
        # 5% flex (cash/bonds/BND/SGOV)
    ],
    # v5_gold: optimizer-informed — adds GLD, replaces VDE with AMLP — 2026-03-22
    # GLD selected at 35% in BOTH 3Y and 5Y Sharpe optimizer; selected in 4/4 risk-adjusted objectives
    # GLD: Sharpe 0.90, Beta VOO 0.13, Max DD -21% — highest-quality diversifier tested
    # Pure physical gold (bullion) — different thesis from RING (miners); currency hedge, flight-to-safety
    "v5_gold": [
        {"ticker": "GLD",  "weight": 0.20, "theme": "Precious Metals",   "role": "Physical gold — Sharpe 0.90, Beta 0.13; currency debasement + geopolitical hedge"},
        {"ticker": "PPA",  "weight": 0.25, "theme": "Defense",           "role": "Aerospace & defense — Sharpe 0.91/Sortino 1.37"},
        {"ticker": "SMH",  "weight": 0.20, "theme": "Technology",        "role": "Semiconductor / AI compute — best semi ETF"},
        {"ticker": "AMLP", "weight": 0.20, "theme": "Energy Infra",      "role": "Midstream MLP — toll-road revenue; AI gas demand tailwind"},
        {"ticker": "NLR",  "weight": 0.15, "theme": "Nuclear Energy",    "role": "Nuclear renaissance full value chain"},
        # no QTUM — optimizer consistently eliminates in risk-adjusted objectives
        # 5% flex (cash/bonds/BND/SGOV) or QTUM if speculative appetite
    ],
    # v4: final optimizer-informed allocation — 2026-03-21
    # VOO removed (optimizer eliminated across all 7 objectives); VOO 10% → PPA +5%, QTUM +5%
    # Every position benchmarked against all sector alternatives
    # On watch: AMLP (energy infra/AI power), PAVE (infra construction), GRID (smart grid)
    "v4": [
        {"ticker": "PPA",  "weight": 0.30, "theme": "Defense",           "role": "Aerospace & defense — Sharpe 0.91/Sortino 1.37, beats ITA on every metric"},
        {"ticker": "SMH",  "weight": 0.25, "theme": "Technology",        "role": "Semiconductor / AI compute — best semi ETF; 5Y +239% vs SOXX +154%"},
        {"ticker": "NLR",  "weight": 0.15, "theme": "Nuclear Energy",    "role": "Nuclear renaissance full value chain — Sharpe 0.72 vs URA 0.49; AI power demand tailwind"},
        {"ticker": "VDE",  "weight": 0.15, "theme": "Traditional Energy","role": "Oil & gas geopolitical hedge — WTI beta 0.44, corr 0.58; best risk-adj energy ETF"},
        {"ticker": "QTUM", "weight": 0.10, "theme": "Frontier Tech",     "role": "Quantum computing — only purpose-built quantum ETF; next-gen AI compute adjacency"},
        # 5% flex (cash/bonds/BND/SGOV) — reserved for AMLP, PAVE, or GRID when ready
    ],
    # ── v7: signal-averaged across Tier 1 + Tier 2 objectives — 2026-03-22 ─────────
    # Caps: PPA ≤ 25%, NLR ≤ 15%, VDE ≤ 15%, SLV ≤ 10%  |  min-drawdown objective added
    # Framework re-evaluation: ranked objectives by signal quality (Sharpe/Omega = Tier 1,
    #   Sortino = Tier 2, CVaR/min-drawdown for checks only, Sortino5Y/CVaR degenerate)
    # Derivation: average of 5 clean runs (3Y Sharpe/Sortino/Omega, 5Y Sharpe/Omega)
    # Key changes vs v6_vde:
    #   NLR 20% → 15%  (optimizer consistently at 15% cap; 5pp freed)
    #   QTUM 5% → 12%  (consistent 13-14% signal in 3Y; capped by QTUM's short history)
    #   VDE 10% → 13%  (3Y avg 13.5%, 5Y avg 12%+; thesis anchors geopolitical hedge)
    #   PPA 25% → 22%  (3Y always at cap; 5Y 13-24% avg; blend reads as ~22%)
    #   SMH 30% → 28%  (3Y avg ~21%, 5Y at cap 35%; blend with slight 3Y conservatism)
    "v7": [
        {"ticker": "SMH",  "weight": 0.28, "theme": "Technology",     "role": "AI compute — 10Y Sharpe 0.935; 5Y optimizer always at 35% cap; 3Y ~21%"},
        {"ticker": "PPA",  "weight": 0.22, "theme": "Defense",        "role": "Aerospace & defense — 10Y Sharpe 0.791; 3Y at 25% cap; 5Y avg 18%; blended 22%"},
        {"ticker": "NLR",  "weight": 0.15, "theme": "Nuclear Energy", "role": "Nuclear renaissance — 10Y Sharpe 0.553; at 15% cap in 7/10 runs; AI power demand"},
        {"ticker": "VDE",  "weight": 0.13, "theme": "Energy",         "role": "Geopolitical energy hedge — 10Y Sharpe 0.400; 3Y avg 13.5%, 5Y avg 12%; thesis anchor"},
        {"ticker": "SLV",  "weight": 0.10, "theme": "Precious Metals","role": "Silver — 10Y Sharpe 0.532; cap binds 10/10 runs; solar/EV/AI hardware demand"},
        {"ticker": "QTUM", "weight": 0.12, "theme": "Frontier Tech",  "role": "Quantum computing — avg 13.5% across 5 clean runs; 5Y Omega suppresses to 3.8%"},
    ],

    # ── v8: v7 + IAU — signal-averaged across 4 objectives (3Y/5Y Sharpe + 3Y/5Y Omega) — 2026-03-23
    # IAU cap binds 4/4 runs at 10% — same signal strength as SLV
    # Funded by: NLR 15%→11% (-4pp), QTUM 12%→7% (-5pp)
    # IAU thesis: real-rate compression hedge, central bank demand floor, DXY diversification
    # 5Y Sharpe IAU 0.91 (above SLV 0.53, VDE 0.40), Beta VOO 0.13 — uncorrelated compounder
    "v8": [
        {"ticker": "SMH",  "weight": 0.28, "theme": "Technology",     "role": "AI compute — 10Y Sharpe 0.935; 5Y optimizer always at 35% cap"},
        {"ticker": "PPA",  "weight": 0.21, "theme": "Defense",        "role": "Aerospace & defense — 10Y Sharpe 0.791; blended 3Y/5Y signal"},
        {"ticker": "VDE",  "weight": 0.13, "theme": "Energy",         "role": "Geopolitical energy hedge — 10Y Sharpe 0.400; thesis anchor"},
        {"ticker": "NLR",  "weight": 0.11, "theme": "Nuclear Energy", "role": "Nuclear renaissance — reduced from 15%; IAU crowds out 4pp"},
        {"ticker": "SLV",  "weight": 0.10, "theme": "Precious Metals","role": "Silver — industrial demand (solar/EV/AI hardware); cap binds 10/10 runs"},
        {"ticker": "IAU",  "weight": 0.10, "theme": "Precious Metals","role": "Physical gold — real-rate/DXY hedge; 5Y Sharpe 0.91, Beta 0.13; cap binds 4/4 runs"},
        {"ticker": "QTUM", "weight": 0.07, "theme": "Frontier Tech",  "role": "Quantum computing — reduced from 12%; speculative long-dated; 5Y Omega drops to 0%"},
    ],

    # v3: revert previous core, drop gold/real estate/rare earths, add defense + energy
    "v3": [
        {"ticker": "VOO",  "weight": 0.25, "theme": "Broad Market",      "role": "S&P 500 core anchor"},
        {"ticker": "VGT",  "weight": 0.15, "theme": "Technology",        "role": "Broad info tech — reverted from proposed"},
        {"ticker": "SMH",  "weight": 0.15, "theme": "Technology",        "role": "Semiconductor / AI conviction bet"},
        {"ticker": "URA",  "weight": 0.15, "theme": "Nuclear/Uranium",   "role": "Uranium — pure commodity + miner play, higher raw return than NLR"},
        {"ticker": "ITA",  "weight": 0.10, "theme": "Defense",           "role": "US aerospace & defense — geopolitical tailwind, best Sharpe/Sortino of any candidate"},
        {"ticker": "VDE",  "weight": 0.05, "theme": "Traditional Energy","role": "Oil & gas — geopolitical hedge, broader than XLE"},
        {"ticker": "QTUM", "weight": 0.05, "theme": "Frontier Tech",     "role": "Quantum computing — speculative, long-dated"},
        # 10% flex (cash/bonds/BND/SGOV) — unallocated, not included in analytics
    ],
}

# ── App Defaults ──────────────────────────────────────────────────────────────

DEFAULT_PORTFOLIO: str = "v8"
PORTFOLIO_DISPLAY_ORDER: list[str] = ["v8", "v7", "previous"]

# ── Data Fetcher Settings ─────────────────────────────────────────────────────

# Benchmark ticker used for beta/correlation across all analytics
BENCHMARK_TICKER: str = "VOO"

# Default lookback for price history (5 years + buffer for rolling windows)
from datetime import date as _date, timedelta as _td
LOOKBACK_5Y: _date = _date.today() - _td(days=365 * 5 + 30)
# Full history lookback — rolling 10.5Y ensures the 10Y perf button always has data
LOOKBACK_ALL: _date = _date.today() - _td(days=365 * 10 + 200)

# Risk-free rate for Sharpe/Sortino calculations (annualised, approximate T-bill yield)
RISK_FREE_RATE: float = 0.045

# ETF holdings cache TTL in days
HOLDINGS_CACHE_TTL_DAYS: int = 7

# yfinance retry settings
YFINANCE_MAX_RETRIES: int = 3
YFINANCE_BACKOFF_BASE: float = 2.0  # seconds; doubles each retry

# ── Optional API Keys (loaded from .env if present) ───────────────────────────
import os
from dotenv import load_dotenv

load_dotenv(ROOT_DIR / ".env")

ALPHA_VANTAGE_API_KEY: str | None = os.getenv("ALPHA_VANTAGE_API_KEY")
FRED_API_KEY: str | None = os.getenv("FRED_API_KEY")
DISCORD_WEBHOOK_URL: str | None = os.getenv("DISCORD_WEBHOOK_URL")

# ── Benchmark tickers ─────────────────────────────────────────────────────────
# BENCHMARK_TICKER : ETF benchmark (includes fees/tracking error) — default for most views
# BENCHMARK_SPX    : Pure S&P 500 index — used when --benchmark spx is passed
BENCHMARK_SPX: str = "^SPX"
