#!/usr/bin/env python3
"""
test_reverse_signal.py — offline unit tests for the REVERSE_SIGNAL exit rule
(added 2026-11-19).

No network: auto_trade.py is a live script (it exits at import time when the
Alpaca keys are missing and hits the API at module level), so it cannot be
imported directly.  Instead we parse its source and execute ONLY the pure
part — function definitions and simple constant assignments — into a fresh
namespace, then stub the two things check_exit() reaches out to
(get_bars(), Indicators).  The namespace is then driven with synthetic
positions and synthetic analyses.

Covered:
  (1)  long + SELL @ 60 confidence            => REVERSE_SIGNAL
  (2)  long + SELL @ 40 confidence            => not REVERSE_SIGNAL
  (3)  long + BUY                            => not REVERSE_SIGNAL
  (4)  SHORT + SELL                           => not REVERSE_SIGNAL (shorts unaffected)
  (5)  long already past the -2% hard stop    => HARD_STOP (precedence intact)
  (6)  rule disabled by config flag           => not REVERSE_SIGNAL
  (7)  RSI exit still outranks REVERSE_SIGNAL  => RSI_EXIT
  (8)  no analysis for this cycle             => not REVERSE_SIGNAL
  (9)  take profit still outranks REVERSE_SIGNAL => TAKE_PROFIT
  (10) integration guards: every function the exit path calls really exists,
       and the dead `send_telegram(...)` call from the 2026-11-12 post-mortem
       has not come back.

Run with:
    python3 test_reverse_signal.py
"""
import ast
import os
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent
AUTO_TRADE = HERE / "auto_trade.py"

FAILURES = []


def load_pure(path):
    """Execute only the pure definitions of auto_trade.py.

    Function defs and constant assignments are compiled into a fresh
    namespace; every other top-level statement (the API calls, the prints,
    the sys.exit on a missing key) is never executed.  Same trick as
    test_pnl_reconciled.py.
    """
    src = path.read_text()
    tree = ast.parse(src, filename=str(path))
    keep = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            keep.append(node)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            keep.append(node)
    ns = {"__name__": "auto_trade_pure"}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), "exec"), ns)
    return ns


class _Indicators:
    """Stub for the Indicators class (numpy/pandas-free).

    RSI / MACD values are set by the tests so the RSI_EXIT and MACD_EXIT
    branches can be exercised deterministically.
    """
    rsi_value = 50.0
    macd_crossover = None

    @staticmethod
    def RSI(prices, period=10):
        return _Indicators.rsi_value

    @staticmethod
    def MACD(prices, fast=12, slow=26, signal=9):
        return {"macd": 0, "signal": 0, "histogram": 0,
                "crossover": _Indicators.macd_crossover}


class _Bars:
    """Stub get_bars(): returns a close series or None (bars unavailable)."""
    close = None

    @staticmethod
    def get_bars(symbol, timeframe="5Min", limit=100):
        if _Bars.close is None:
            return None
        return {"close": list(_Bars.close)}


PURE = load_pure(AUTO_TRADE)
PURE["Indicators"] = _Indicators
PURE["get_bars"] = _Bars.get_bars

check_exit = PURE["check_exit"]


def source_config(name, default=None):
    """Read a config constant straight out of auto_trade.py so the tests judge
    the same threshold the live bot uses."""
    text = AUTO_TRADE.read_text()
    m = re.search(rf"^{re.escape(name)}\s*=\s*(.+)$", text, re.M)
    if not m:
        return default
    try:
        return ast.literal_eval(m.group(1).strip())
    except Exception:
        return default


def long_position(price=100.0, entry=100.0, qty=10):
    return {"avg_price": entry, "qty": qty, "side": "long",
            "current_price": price}


def short_position(price=100.0, entry=100.0, qty=-10):
    return {"avg_price": entry, "qty": qty, "side": "short",
            "current_price": price}


def analysis(signal="SELL", confidence=60, price=100.0):
    return {"signal": signal, "confidence": confidence, "price": price,
            "reasons": []}


def setUpModule():
    _Bars.close = None
    _Indicators.rsi_value = 50.0
    _Indicators.macd_crossover = None


class TestReverseSignal(unittest.TestCase):
    """(1)-(5) the rule itself and its precedence."""

    def test_long_sell_60_confidence_triggers_reverse_signal(self):
        reason, detail = check_exit("ABBV", long_position(),
                                    analysis("SELL", 60))
        self.assertEqual(reason, "REVERSE_SIGNAL")
        self.assertIn("SELL", detail)

    def test_long_sell_40_confidence_does_not_trigger(self):
        reason, _ = check_exit("ABBV", long_position(), analysis("SELL", 40))
        self.assertNotEqual(reason, "REVERSE_SIGNAL")
        self.assertIsNone(reason)

    def test_long_buy_signal_does_not_trigger(self):
        reason, _ = check_exit("ABBV", long_position(), analysis("BUY", 90))
        self.assertNotEqual(reason, "REVERSE_SIGNAL")

    def test_short_sell_signal_is_not_affected(self):
        # A short that stays in SELL territory is working as intended.
        reason, _ = check_exit("SLV", short_position(), analysis("SELL", 90))
        self.assertNotEqual(reason, "REVERSE_SIGNAL")

    def test_hard_stop_takes_precedence_over_reverse_signal(self):
        # -3% is past the -2% HARD_STOP: the hard stop must win.
        reason, detail = check_exit("ABBV", long_position(price=97.0),
                                    analysis("SELL", 90))
        self.assertEqual(reason, "HARD_STOP")
        self.assertIn("hard cut", detail)

    def test_rule_can_be_disabled_by_config_flag(self):
        original = PURE["REVERSE_SIGNAL_EXIT_ENABLED"]
        PURE["REVERSE_SIGNAL_EXIT_ENABLED"] = False
        try:
            reason, _ = check_exit("ABBV", long_position(), analysis("SELL", 90))
        finally:
            PURE["REVERSE_SIGNAL_EXIT_ENABLED"] = original
        self.assertNotEqual(reason, "REVERSE_SIGNAL")

    def test_rsi_exit_outranks_reverse_signal(self):
        # Flat close series -> P&L 0 -> the RSI exit is the rule that fires.
        _Bars.close = [100.0 + (i * 0.01) for i in range(29)] + [100.0]
        _Indicators.rsi_value = 92.0
        try:
            reason, _ = check_exit("ABBV", long_position(), analysis("SELL", 90))
        finally:
            _Bars.close = None
            _Indicators.rsi_value = 50.0
        self.assertEqual(reason, "RSI_EXIT")

    def test_take_profit_outranks_reverse_signal(self):
        # +9% is past the 8% TAKE_PROFIT.  partial_taken is set so the
        # (pre-existing) +3% PARTIAL_TAKE_50% rule does not answer first.
        pos = long_position(price=109.0)
        pos["partial_taken"] = True
        reason, _ = check_exit("ABBV", pos, analysis("SELL", 90))
        self.assertEqual(reason, "TAKE_PROFIT")

    def test_missing_analysis_does_not_trigger(self):
        # analyze_symbol() failed this cycle -> no signal -> no exit.
        reason, _ = check_exit("ABBV", long_position(), None)
        self.assertNotEqual(reason, "REVERSE_SIGNAL")


class TestConfig(unittest.TestCase):
    """The config flags must exist in auto_trade.py with sane values."""

    def test_flags_present(self):
        self.assertIs(source_config("REVERSE_SIGNAL_EXIT_ENABLED"), True)
        self.assertEqual(source_config("REVERSE_SIGNAL_MIN_CONFIDENCE"), 50)

    def test_loaded_module_uses_the_same_flags(self):
        self.assertEqual(PURE["REVERSE_SIGNAL_EXIT_ENABLED"], True)
        self.assertEqual(PURE["REVERSE_SIGNAL_MIN_CONFIDENCE"], 50)


class TestIntegrationGuards(unittest.TestCase):
    """(10) The exit path must call functions that actually exist.

    The 2026-11-12 post-mortem found `send_telegram(...)` being called in
    auto_trade.py when no such function existed anywhere in the repo: every
    announcement raised NameError and a bare `except: pass` swallowed it.
    These guards keep that class of bug from coming back.
    """

    def setUp(self):
        self.tree = ast.parse(AUTO_TRADE.read_text(), filename=str(AUTO_TRADE))

    def _defined_functions(self):
        return {n.name for n in ast.walk(self.tree)
                if isinstance(n, ast.FunctionDef)}

    def test_exit_path_functions_exist(self):
        defined = self._defined_functions()
        for name in ("check_exit", "place_order", "place_order_chunked",
                     "announce_trade", "set_sector_cooldown",
                     "analyze_symbol", "get_bars"):
            self.assertIn(name, defined,
                          f"{name}() is called by the exit path but is not defined")

    def test_local_imports_resolve_to_real_definitions(self):
        """Every `from <local module> import <name>` must really exist.

        record_trade() comes from learning.py, announce_trade() uses
        telegram_send.send_message() from the workspace root.  Both were
        verified by definition, not by call site.
        """
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            mod = node.module
            for candidate in (HERE / f"{mod}.py", HERE.parent / f"{mod}.py"):
                if candidate.exists():
                    break
            else:
                continue  # third-party module — not our concern here
            mod_tree = ast.parse(candidate.read_text(), filename=str(candidate))
            names = set()
            for n in ast.walk(mod_tree):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(n.name)
                elif isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            names.add(t.id)
                elif isinstance(n, ast.ImportFrom):
                    for a in n.names:
                        names.add(a.asname or a.name)
            for alias in node.names:
                self.assertIn(alias.name, names,
                              f"{mod}.{alias.name} is imported by auto_trade.py "
                              f"but is not defined in {candidate.name}")

    def test_no_dead_send_telegram_call(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotEqual(
                    node.func.id, "send_telegram",
                    "send_telegram() is called but was never defined "
                    "(2026-11-12 post-mortem) — use announce_trade()")

    def test_check_exit_accepts_analysis(self):
        fn = [n for n in ast.walk(self.tree)
              if isinstance(n, ast.FunctionDef) and n.name == "check_exit"][0]
        self.assertIn("analysis", [a.arg for a in fn.args.args])

    def test_main_loop_passes_analysis_to_check_exit(self):
        calls = [n for n in ast.walk(self.tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "check_exit"]
        self.assertTrue(calls, "no check_exit() call site found")
        for call in calls:
            self.assertEqual(len(call.args), 3,
                             "check_exit() must receive the current cycle's "
                             "analysis so REVERSE_SIGNAL costs no extra API call")

    def test_reverse_signal_branch_lives_in_check_exit(self):
        fn = [n for n in ast.walk(self.tree)
              if isinstance(n, ast.FunctionDef) and n.name == "check_exit"][0]
        src = ast.get_source_segment(AUTO_TRADE.read_text(), fn) or ""
        self.assertIn("REVERSE_SIGNAL", src)


if __name__ == "__main__":
    print("=" * 60)
    print("test_reverse_signal.py — REVERSE_SIGNAL exit rule")
    print("=" * 60)
    result = unittest.main(verbosity=2, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
