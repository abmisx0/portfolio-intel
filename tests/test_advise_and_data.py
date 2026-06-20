"""
Unit tests for the 2026-06 audit fixes: trim-signal semantics, valuation unit
normalisation, lookback-scaled history gate, and Finnhub weight conversion.

Runs with no network and no Robinhood: `python3 -m unittest discover tests`.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli.commands.advise import _trim_signal, _REQUIRED_HISTORY, MIN_HISTORY_DAYS
from core.valuation import _pct_to_fraction, _invert_ratio
from config import THESIS_ANCHORS


class TestTrimSignal(unittest.TestCase):
    def _r(self, ticker="XYZ", ds=0.0, ps=1.0, w=0.05):
        return {"ticker": ticker, "delta_sharpe": ds, "pos_sharpe": ps, "weight": w}

    def test_exit_when_removal_strongly_improves(self):
        self.assertEqual(_trim_signal(self._r(ds=0.06)), "EXIT")

    def test_trim_when_removal_mildly_improves(self):
        self.assertEqual(_trim_signal(self._r(ds=0.03)), "TRIM")

    def test_weak_standalone_is_not_trim(self):
        # Poor standalone Sharpe but removal HURTS the portfolio → WEAK, not TRIM
        self.assertEqual(_trim_signal(self._r(ds=-0.03, ps=0.2)), "WEAK")

    def test_hold_when_no_signal(self):
        self.assertEqual(_trim_signal(self._r(ds=-0.01, ps=1.5)), "HOLD")

    def test_oversize_position_is_reduce(self):
        self.assertEqual(_trim_signal(self._r(ds=-0.1, w=0.30)), "REDUCE")

    def test_anchor_never_exit_trim_weak(self):
        anchor = next(iter(THESIS_ANCHORS))
        for ds, ps in [(0.06, 1.0), (0.03, 1.0), (-0.03, 0.1)]:
            self.assertEqual(_trim_signal(self._r(ticker=anchor, ds=ds, ps=ps)), "ANCHOR")


class TestHistoryGate(unittest.TestCase):
    def test_required_history_scales_with_lookback(self):
        self.assertLess(_REQUIRED_HISTORY["1Y"], _REQUIRED_HISTORY["3Y"])
        self.assertLess(_REQUIRED_HISTORY["3Y"], _REQUIRED_HISTORY["10Y"])
        # A ~1-year-old ticker must NOT pass the 3Y gate (the CRCL truncation bug)
        self.assertGreater(_REQUIRED_HISTORY["3Y"], 300)
        # Tolerance: a ticker with full 3Y of trading days does pass
        self.assertLessEqual(_REQUIRED_HISTORY["3Y"], 252 * 3)
        self.assertGreaterEqual(max(MIN_HISTORY_DAYS, _REQUIRED_HISTORY["1Y"]), 226)


class TestValuationUnits(unittest.TestCase):
    def test_percent_to_fraction_always_divides(self):
        # yfinance stock dividendYield: 6.71 means 6.71%, 0.60 means 0.60%
        self.assertAlmostEqual(_pct_to_fraction(6.71), 0.0671)
        self.assertAlmostEqual(_pct_to_fraction(0.60), 0.0060)
        self.assertIsNone(_pct_to_fraction(None))

    def test_fund_ratio_inversion(self):
        # Yahoo fund "Price/Earnings" arrives as earnings yield: 0.0248 → ~40.3x
        self.assertAlmostEqual(_invert_ratio(0.0248), 40.32, places=1)
        self.assertIsNone(_invert_ratio(None))
        self.assertIsNone(_invert_ratio(0))
        self.assertIsNone(_invert_ratio(40.0))  # already a multiple — refuse to invert


class TestFinnhubWeightConversion(unittest.TestCase):
    def test_pct_converted_to_fraction(self):
        from unittest import mock
        import core.holdings as h

        fake = [{"symbol": "NVDA", "name": "NVIDIA", "pct": 18.09},
                {"symbol": "TSM", "name": "Taiwan Semi", "pct": 10.59},
                {"symbol": "", "name": "junk", "pct": 1.0}]
        with mock.patch.object(h, "_finnhub_unavailable", False), \
             mock.patch("core.finnhub.get_etf_holdings", return_value=fake), \
             mock.patch("config.FINNHUB_API_KEY", "test-key"):
            out = h._finnhub_full_holdings("SMH")
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(out[0]["weight"], 0.1809)


if __name__ == "__main__":
    unittest.main()
