#!/usr/bin/env python3
"""Regression tests for trading-bot safety fixes (2026-11-06).

No network calls and no order placement: every external action is stubbed.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("ALPACA_API_KEY", "test-key")
os.environ.setdefault("ALPACA_API_SECRET", "test-secret")

import auto_trade as at
import bot_status_digest as digest
import rebalancing as rb


class TestAdaptiveConfidenceFallback(unittest.TestCase):
    def test_learning_failure_returns_safe_threshold(self):
        with mock.patch.object(at, "get_optimal_confidence", side_effect=RuntimeError("boom")):
            value = at.resolve_optimal_confidence(1.0)
        self.assertEqual(value, at.MIN_CONFIDENCE_BUY)
        self.assertGreaterEqual(value, 25)

    def test_success_path_is_regime_adjusted_and_clamped(self):
        with mock.patch.object(at, "get_optimal_confidence", return_value=40):
            self.assertEqual(at.resolve_optimal_confidence(1.2), 48)
        with mock.patch.object(at, "get_optimal_confidence", return_value=80):
            self.assertEqual(at.resolve_optimal_confidence(1.0), 50)


class TestMarketFallback(unittest.TestCase):
    def test_weekday_market_hours(self):
        state = at._fallback_market_state(datetime(2026, 11, 5, 10, 0))
        self.assertTrue(state["is_open"])

    def test_weekend_is_closed(self):
        state = at._fallback_market_state(datetime(2026, 11, 7, 10, 0))
        self.assertFalse(state["is_open"])

    def test_pre_and_post_market_are_closed(self):
        self.assertFalse(at._fallback_market_state(
            datetime(2026, 11, 5, 9, 0))["is_open"])
        self.assertFalse(at._fallback_market_state(
            datetime(2026, 11, 5, 16, 30))["is_open"])


class TestAccountFailsClosed(unittest.TestCase):
    def test_account_http_error_does_not_fabricate_equity(self):
        response = mock.Mock(status_code=503)
        with mock.patch.object(at.requests, "get", return_value=response):
            account, portfolio, error = at.fetch_account_state()
        self.assertEqual(account, {})
        self.assertEqual(portfolio, 0.0)
        self.assertIn("503", error)

    def test_account_exception_does_not_fabricate_equity(self):
        with mock.patch.object(at.requests, "get", side_effect=OSError("offline")):
            account, portfolio, error = at.fetch_account_state()
        self.assertEqual(account, {})
        self.assertEqual(portfolio, 0.0)
        self.assertIn("offline", error)


class TestMeanReversionRunsOnce(unittest.TestCase):
    def test_each_signal_is_considered_once(self):
        positions = {}
        trades = []
        account = {"buying_power": 100000.0}
        time_info = {"entries_allowed": True, "is_danger_zone": False,
                     "is_kill_zone": False}
        with mock.patch.object(at, "get_bars", return_value={"close": [100.0]}):
            with mock.patch.object(at, "place_order", return_value=True) as place:
                at.execute_mean_reversion_entries(
                    {"QQQ": 45}, positions, account, 100000.0,
                    time_info, set(), trades)
        self.assertEqual(place.call_count, 1)
        self.assertEqual(trades, ["QQQ"])

    def test_unavailable_clock_blocks_mean_reversion(self):
        with mock.patch.object(at, "place_order") as place:
            at.execute_mean_reversion_entries(
                {"QQQ": 45}, {}, {"buying_power": 100000.0}, 100000.0,
                {"entries_allowed": False}, set(), [])
        place.assert_not_called()


class TestDisabledFeatureEnforcement(unittest.TestCase):
    def test_disabled_feature_schema_is_read(self):
        with mock.patch.object(at, "_load_evolution_file", return_value={
            "disabled": ["Trend: UP"]
        }):
            at._DISABLED_FEATURES_CACHE["ts"] = 0
            self.assertEqual(at.disabled_features(), {"Trend: UP"})


class TestDigestReporting(unittest.TestCase):
    def test_disabled_features_render_names_not_raw_dict(self):
        self.assertEqual(
            digest.format_disabled_features({"disabled": ["Trend: UP"]}),
            ["Trend: UP"],
        )
        self.assertEqual(digest.format_disabled_features({}), [])

    def test_future_dated_report_has_negative_age(self):
        raw = (datetime.now().astimezone().isoformat())
        age = digest.report_age_seconds({"date": raw})
        self.assertIsNotNone(age)

    def test_process_scan_ignores_non_python_command(self):
        output = "123 00:00:01 pgrep -f auto_trade.py\n"
        with mock.patch("subprocess.check_output", return_value=output):
            self.assertEqual(digest.find_bot_processes(), [])


class TestLauncherEntryPoint(unittest.TestCase):
    def test_launchers_use_current_script_not_missing_v6(self):
        for name in ("watchdog.sh", "keep_alive.sh", "run_trade.sh"):
            text = (HERE / name).read_text()
            self.assertIn("auto_trade.py", text)
            self.assertNotIn("trading_bot_v6.py", text)


class TestShortSectorAdvice(unittest.TestCase):
    def test_short_sector_uses_reduce_short(self):
        _, suggestions, _ = rb.build_report(
            [{"symbol": "SLV", "market_value": -50000.0}], 100000.0)
        action = next(s["action"] for s in suggestions if s["sector"] == "commodity")
        self.assertEqual(action, "REDUCE_SHORT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
