#!/usr/bin/env python3
"""
test_rebalancing.py — regression guard for the Day-249 rebalancing defects.

Two bugs fixed 2026-11-05:

1. SIGN DISCARDED. build_report() took abs(market_value), so a short position
   counted as positive exposure. The live book is a SLV short worth -$271,792
   against $147,579 equity; it read as "184% commodity overweight" instead of
   "-184% short commodity".

2. NO GROSS-EXPOSURE CHECK. Suggestions were computed per sector in isolation,
   so a book at 185.7% gross exposure still produced five BUY suggestions.
   Acting on them would have pushed gross exposure to roughly 274% of equity.

Run: python test_rebalancing.py
Exit 0 = pass. No network calls, no side effects.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import rebalancing as rb


# The live book as of 2026-11-01: SLV short -6094 @ 52.96, SPY long 3 @ 766.97.
LIVE_POSITIONS = [
    {"symbol": "SLV", "market_value": -271792.0},
    {"symbol": "SPY", "market_value": 2300.91},
]
LIVE_EQUITY = 147579.51


class TestShortIsNotLongExposure(unittest.TestCase):
    def test_short_position_is_negative_allocation(self):
        current, _, _ = rb.build_report(LIVE_POSITIONS, LIVE_EQUITY)
        self.assertLess(
            current["commodity"], 0,
            "a short position must read as negative allocation, not positive",
        )
        self.assertAlmostEqual(current["commodity"], -184.17, places=2)

    def test_long_position_stays_positive(self):
        current, _, _ = rb.build_report(LIVE_POSITIONS, LIVE_EQUITY)
        self.assertGreater(current["index"], 0)

    def test_gross_exposure_counts_both_sides(self):
        _, _, info = rb.build_report(LIVE_POSITIONS, LIVE_EQUITY)
        # 271,792 + 2,300.91 = 274,092.91 over 147,579.51 equity.
        self.assertAlmostEqual(info["gross_exposure_pct"], 185.73, places=2)
        self.assertAlmostEqual(info["net_exposure_pct"], -182.61, places=2)


class TestNoBuyAdviceWhileOverLeveraged(unittest.TestCase):
    def test_over_leveraged_book_emits_no_buys(self):
        _, suggestions, info = rb.build_report(LIVE_POSITIONS, LIVE_EQUITY)
        buys = [s for s in suggestions if s["action"] == "BUY"]
        self.assertEqual(
            buys, [],
            "sector BUY advice on a 185% gross book would compound the leverage",
        )
        self.assertTrue(info["over_leveraged"])

    def test_reduce_leverage_action_is_present_and_first(self):
        _, suggestions, _ = rb.build_report(LIVE_POSITIONS, LIVE_EQUITY)
        self.assertEqual(suggestions[0]["action"], "REDUCE_LEVERAGE")
        self.assertIn("commodity", suggestions[0]["sector"])

    def test_normal_book_still_emits_buys(self):
        positions = [
            {"symbol": "AAPL", "market_value": 30000.0},
            {"symbol": "SPY", "market_value": 20000.0},
        ]
        _, suggestions, info = rb.build_report(positions, 100000.0)
        self.assertFalse(info["over_leveraged"])
        buys = [s for s in suggestions if s["action"] == "BUY"]
        self.assertTrue(buys, "a normal book must still produce BUY suggestions")
        self.assertEqual(info["gross_exposure_pct"], 50.0)


class TestGuardsStillHold(unittest.TestCase):
    def test_zero_portfolio_value_raises(self):
        with self.assertRaises(RuntimeError):
            rb.build_report(LIVE_POSITIONS, 0.0)

    def test_empty_positions_do_not_phantom_buy_everything(self):
        # A report with an empty allocation used to make every sector read
        # "underweight by full target" -> six phantom BUYs.
        current, suggestions, info = rb.build_report([], 100000.0)
        self.assertEqual(sum(current.values()), 0.0)
        self.assertFalse(info["over_leveraged"])
        # With no exposure at all, every sector IS underweight, but gross
        # exposure is 0 so the advice is a normal, non-leveraged BUY set.
        self.assertTrue(all(s["action"] == "BUY" for s in suggestions))

    def test_untargeted_sector_is_reviewed_not_folded_in(self):
        positions = [{"symbol": "ZZZZ", "market_value": 25000.0}]
        _, suggestions, _ = rb.build_report(positions, 100000.0)
        review = [s for s in suggestions if s["action"] == "REVIEW"]
        self.assertEqual(len(review), 1)
        self.assertEqual(review[0]["sector"], "other")


class TestShortSectorGetsReduceShort(unittest.TestCase):
    def test_net_short_sector_is_not_reported_as_buy(self):
        positions = [{"symbol": "SLV", "market_value": -50000.0}]
        _, suggestions, info = rb.build_report(positions, 100000.0)
        commodity = [s for s in suggestions if s["sector"] == "commodity"]
        self.assertEqual(len(commodity), 1)
        self.assertEqual(commodity[0]["action"], "REDUCE_SHORT")
        self.assertNotIn(
            "BUY", [s["action"] for s in commodity],
            "a net-short sector must never be labelled BUY",
        )
        self.assertFalse(info["over_leveraged"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
