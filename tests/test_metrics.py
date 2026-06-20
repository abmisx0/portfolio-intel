"""
Unit tests for the pure, deterministic, money-affecting math.

Runs with no network and no Robinhood: `python3 -m unittest discover tests`
(also discoverable by pytest). Covers Black-Scholes delta, the geometric
return/Sharpe primitives, drawdown, and delta-adjusted weight composition.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from core.exposure import _bs_delta, _bs_greeks, _GREEK_SANITY, delta_adjusted_positions, CAP_RATIO
from core.analytics import (
    ann_return_from_returns, ann_vol_from_returns,
    sharpe_from_returns, max_drawdown_from_returns, TRADING_DAYS,
)


class TestBSDelta(unittest.TestCase):
    def test_atm_call_near_half(self):
        d = _bs_delta(S=100, K=100, T=0.25, r=0.04, sigma=0.20, is_call=True)
        self.assertTrue(0.50 < d < 0.65, d)

    def test_atm_put_negative(self):
        d = _bs_delta(S=100, K=100, T=0.25, r=0.04, sigma=0.20, is_call=False)
        self.assertTrue(-0.55 < d < -0.35, d)

    def test_deep_itm_call_near_one(self):
        self.assertGreater(_bs_delta(200, 100, 0.5, 0.04, 0.3, True), 0.95)

    def test_deep_otm_call_near_zero(self):
        self.assertLess(_bs_delta(50, 100, 0.5, 0.04, 0.3, True), 0.05)

    def test_call_put_parity_of_delta(self):
        # call_delta - put_delta == 1 (same strike/expiry/vol)
        c = _bs_delta(100, 105, 0.4, 0.04, 0.25, True)
        p = _bs_delta(100, 105, 0.4, 0.04, 0.25, False)
        self.assertAlmostEqual(c - p, 1.0, places=6)

    def test_expired_intrinsic_fallback(self):
        self.assertEqual(_bs_delta(120, 100, 0, 0.04, 0.2, True), 1.0)   # ITM call
        self.assertEqual(_bs_delta(80, 100, 0, 0.04, 0.2, True), 0.0)    # OTM call
        self.assertEqual(_bs_delta(80, 100, 0, 0.04, 0.2, False), -1.0)  # ITM put
        self.assertEqual(_bs_delta(120, 100, 0, 0.04, 0.2, False), 0.0)  # OTM put


class TestBSGreeks(unittest.TestCase):
    # Reference: real NVDA short-put position (S=205.19, K=200, 97d, r=3.6%, IV=42.7%)
    NVDA = dict(S=205.19, K=200.0, T=97 / 365.0, r=0.036, sigma=0.42744, is_call=False)

    def test_delta_matches_bs_delta(self):
        g = _bs_greeks(**self.NVDA)
        self.assertAlmostEqual(g["delta"], _bs_delta(**self.NVDA), places=10)

    def test_nvda_reference_values(self):
        # Locks the per-share Greeks to the hand-verified figures.
        g = _bs_greeks(**self.NVDA)
        self.assertAlmostEqual(g["delta"], -0.3936, places=3)
        self.assertAlmostEqual(g["gamma"], 0.00851, places=4)
        self.assertAlmostEqual(g["vega"],  0.4069, places=3)
        self.assertAlmostEqual(g["theta"], -0.0803, places=3)
        self.assertAlmostEqual(g["rho"],   -0.2528, places=3)

    def test_gamma_vega_sign_independent_of_call_put(self):
        # Gamma and vega are identical for a call and put at the same strike.
        call = _bs_greeks(100, 100, 0.3, 0.04, 0.25, True)
        put = _bs_greeks(100, 100, 0.3, 0.04, 0.25, False)
        self.assertAlmostEqual(call["gamma"], put["gamma"], places=10)
        self.assertAlmostEqual(call["vega"], put["vega"], places=10)

    def test_long_option_gamma_vega_positive(self):
        g = _bs_greeks(100, 100, 0.3, 0.04, 0.25, True)
        self.assertGreater(g["gamma"], 0)
        self.assertGreater(g["vega"], 0)
        self.assertLess(g["theta"], 0)   # long option bleeds time value

    def test_expired_option_no_optionality(self):
        g = _bs_greeks(120, 100, 0, 0.04, 0.2, True)
        self.assertEqual(g["delta"], 1.0)
        self.assertEqual(g["gamma"], 0.0)
        self.assertEqual(g["vega"], 0.0)
        self.assertEqual(g["theta"], 0.0)


class TestGreekSanity(unittest.TestCase):
    """The plausibility ceilings that reject corrupt broker Greeks."""

    def test_corrupt_broker_vega_rejected(self):
        # Real bug: RH returned a per-share vega of ~800 for a deep-ITM,
        # near-expiry XAR put. It must exceed the ceiling so BS fills in.
        self.assertGreater(abs(805.0), _GREEK_SANITY["vega"])

    def test_normal_greeks_within_ceilings(self):
        # Every Greek of the real NVDA position is comfortably inside its bound.
        g = _bs_greeks(S=205.19, K=200.0, T=97 / 365.0, r=0.036, sigma=0.42744, is_call=False)
        for name, val in g.items():
            self.assertLessEqual(abs(val), _GREEK_SANITY[name], name)

    def test_ceilings_cover_all_five_greeks(self):
        self.assertEqual(set(_GREEK_SANITY), {"delta", "gamma", "theta", "vega", "rho"})


class TestReturnMetrics(unittest.TestCase):
    def test_zero_returns(self):
        r = pd.Series([0.0] * TRADING_DAYS)
        self.assertAlmostEqual(ann_return_from_returns(r), 0.0, places=9)

    def test_geometric_full_year(self):
        # one trading year compounding to 2x → 100% annualized
        r = pd.Series([2 ** (1 / TRADING_DAYS) - 1] * TRADING_DAYS)
        self.assertAlmostEqual(ann_return_from_returns(r), 1.0, places=4)

    def test_geometric_below_arithmetic_for_volatile(self):
        # +10%/-10% alternating: arithmetic mean 0 → naive (1+mean)^252-1 = 0,
        # but true geometric return is sharply negative (volatility drag).
        r = pd.Series([0.10, -0.10] * (TRADING_DAYS // 2))
        geo = ann_return_from_returns(r)
        arithmetic = (1 + r.mean()) ** TRADING_DAYS - 1
        self.assertLess(geo, 0.0)
        self.assertAlmostEqual(arithmetic, 0.0, places=6)
        self.assertLess(geo, arithmetic)  # the bug we fixed: geo must be < arithmetic

    def test_vol_annualization(self):
        r = pd.Series(np.random.default_rng(0).normal(0, 0.01, 5000))
        # daily sigma ~0.01 → annualized ~0.01*sqrt(252) ≈ 0.1587
        self.assertAlmostEqual(ann_vol_from_returns(r), 0.01 * math.sqrt(TRADING_DAYS), places=2)

    def test_sharpe_sign(self):
        r = pd.Series([0.001] * TRADING_DAYS)  # steady positive, ~28% annual
        self.assertGreater(sharpe_from_returns(r, rfr=0.04), 0)

    def test_max_drawdown(self):
        # +100% then -50% → trough is -50% from peak
        r = pd.Series([1.0, -0.5])
        self.assertAlmostEqual(max_drawdown_from_returns(r), -0.5, places=6)

    def test_empty_series_nan(self):
        self.assertTrue(math.isnan(ann_return_from_returns(pd.Series(dtype=float))))


class TestDeltaAdjustedPositions(unittest.TestCase):
    def _exposure(self):
        return {
            "total_value": 1000.0,
            "positions": [
                {"ticker": "AAA", "equity_value": 100, "option_delta_dollars": 0,
                 "delta_value": 100, "has_options": False},
                {"ticker": "BBB", "equity_value": 0, "option_delta_dollars": 50,
                 "delta_value": 50, "has_options": True},   # synthetic
                {"ticker": "CCC", "equity_value": 100, "option_delta_dollars": -90,
                 "delta_value": 10, "has_options": True},   # capped (10 < 100*CAP_RATIO)
                {"ticker": "DDD", "equity_value": 50, "option_delta_dollars": -60,
                 "delta_value": -10, "has_options": True},  # net short → dropped
            ],
            "options": [], "summary": {},
        }

    def test_drops_non_positive_and_normalizes(self):
        out = delta_adjusted_positions(self._exposure())
        tickers = {p["ticker"] for p in out}
        self.assertEqual(tickers, {"AAA", "BBB", "CCC"})       # DDD dropped
        self.assertAlmostEqual(sum(p["weight"] for p in out), 1.0, places=9)

    def test_weights_proportional_to_delta_value(self):
        out = {p["ticker"]: p["weight"] for p in delta_adjusted_positions(self._exposure())}
        # AAA:100 BBB:50 CCC:10 → gross 160
        self.assertAlmostEqual(out["AAA"], 100 / 160, places=9)
        self.assertAlmostEqual(out["BBB"], 50 / 160, places=9)

    def test_role_labels(self):
        roles = {p["ticker"]: p["role"] for p in delta_adjusted_positions(self._exposure())}
        self.assertIn("equity", roles["AAA"].lower())
        self.assertIn("synthetic", roles["BBB"].lower())
        self.assertIn("capped", roles["CCC"].lower())

    def test_cap_ratio_threshold(self):
        self.assertEqual(CAP_RATIO, 0.5)


if __name__ == "__main__":
    unittest.main()
