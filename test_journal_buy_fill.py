#!/usr/bin/env python3
"""
test_journal_buy_fill.py — offline unit tests for the BUY journal row.

Defect (2026-11-20): BUY rows in trade_journal.json recorded the SIGNAL
price instead of the ACTUAL FILL price and omitted filled_avg_price /
fill_price / announced. The ABBV entry was journalled at 288.405 while
Alpaca actually filled 2 sh @ 288.14, so every journal-derived P&L drifted.

SELL rows already carry the fill price plus filled_avg_price / fill_price /
announced (auto_trade.py: reconcile_pending_exits). These tests prove the
BUY row now matches that shape.

No network and no live journal: every test builds a temp journal file and
calls the real functions from auto_trade.py.

Run with:
    venv/bin/python test_journal_buy_fill.py
"""
import ast
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
AUTO_TRADE = HERE / "auto_trade.py"


# --------------------------------------------------------------------------
# Source loader — exec ONLY the function definitions + literal constants
# --------------------------------------------------------------------------
_TREE = ast.parse(AUTO_TRADE.read_text(), filename=str(AUTO_TRADE))


def _is_literal(node):
    try:
        ast.literal_eval(node)
        return True
    except Exception:
        return False


def build_namespace():
    """Exec ONLY the function definitions + literal constants of auto_trade.py."""
    keep = []
    for node in _TREE.body:
        if isinstance(node, ast.FunctionDef):
            keep.append(node)
        elif isinstance(node, ast.Assign) and _is_literal(node.value):
            keep.append(node)
    ns = {"__name__": "auto_trade_pure"}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(AUTO_TRADE), "exec"), ns)
    return ns


# --------------------------------------------------------------------------
# Environment: temp journal + a stub record_trade() that mimics
# learning.record_trade() (appends exactly ONE row).
# --------------------------------------------------------------------------
class Env:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="buyfill-")
        self.journal_path = os.path.join(self.tmp, "trade_journal.json")
        self.recorded = []
        self.ns = build_namespace()

        ns = self.ns
        ns["os"] = os
        ns["json"] = json
        ns["datetime"] = datetime
        ns["TRADE_JOURNAL_FILE"] = self.journal_path
        ns["record_trade"] = self._record_trade

    def _record_trade(self, trade_type, symbol, qty, price, signal, confidence,
                      reasons, pnl=0):
        """Mimics learning.record_trade(): appends ONE row to the journal."""
        self.recorded.append({
            "type": trade_type, "symbol": symbol, "qty": qty, "price": price,
            "signal": signal, "confidence": confidence, "reasons": list(reasons),
            "pnl": pnl,
        })
        rows = self.journal()
        rows.append({
            "timestamp": datetime.now().isoformat(),
            "type": trade_type, "symbol": symbol, "qty": qty, "price": price,
            "signal": signal, "confidence": confidence, "reasons": list(reasons),
            "pnl": pnl, "closed": False,
        })
        with open(self.journal_path, "w") as fh:
            json.dump(rows, fh, indent=2)
        return {"total_trades": len(rows)}

    def journal(self):
        try:
            with open(self.journal_path) as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class JournalBuyFillTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.cleanup)
        self.ns = self.env.ns

    # -- (1) fill price differs from the signal price ---------------------
    def test_buy_row_uses_the_fill_price_not_the_signal_price(self):
        """The journalled price must be what Alpaca actually charged."""
        row = self.ns["build_buy_journal_row"](
            "ABBV", 2, 288.405, "BUY", 45, ["Trend: BULL", "Above VWAP"],
            fill_info={"filled_avg_price": 288.14},
            order_id="ord-abbv-1",
        )

        self.assertAlmostEqual(row["price"], 288.14, places=6,
                               msg="BUY row price must be the FILL price")
        self.assertNotAlmostEqual(row["price"], 288.405, places=3)
        self.assertAlmostEqual(row["filled_avg_price"], 288.14, places=6)
        self.assertAlmostEqual(row["fill_price"], 288.14, places=6)
        self.assertTrue(row["announced"])
        # The decision price is preserved, nothing is lost.
        self.assertAlmostEqual(row["signal_price"], 288.405, places=6)
        self.assertEqual(row["type"], "BUY")
        self.assertEqual(row["symbol"], "ABBV")
        self.assertEqual(row["qty"], 2)

    def test_buy_row_has_the_same_shape_as_a_sell_row(self):
        """SELL rows carry filled_avg_price / fill_price / announced."""
        row = self.ns["build_buy_journal_row"](
            "ABBV", 2, 288.405, "BUY", 45, [],
            fill_info={"filled_avg_price": 288.14},
        )
        for key in ("filled_avg_price", "fill_price", "announced"):
            self.assertIn(key, row, f"BUY row must carry {key} like SELL rows")

    def test_record_buy_journal_row_writes_the_fill_price_to_the_journal(self):
        """End-to-end (offline): the row on disk carries the fill price."""
        row = self.ns["record_buy_journal_row"](
            "ABBV", 2, 288.405, "BUY", 45, ["Trend: BULL"],
            fill_info={"filled_avg_price": 288.14},
            order_id="ord-abbv-1",
            announced=True,
        )
        self.assertIsNotNone(row)

        rows = self.env.journal()
        self.assertEqual(len(rows), 1, "exactly one journal row expected")
        written = rows[0]
        self.assertAlmostEqual(written["price"], 288.14, places=6)
        self.assertAlmostEqual(written["filled_avg_price"], 288.14, places=6)
        self.assertAlmostEqual(written["fill_price"], 288.14, places=6)
        self.assertAlmostEqual(written["signal_price"], 288.405, places=6)
        self.assertTrue(written["announced"])
        self.assertEqual(written["order_id"], "ord-abbv-1")
        # learning.record_trade() must receive the FILL price too, so the
        # stats/cost basis never see the signal price.
        self.assertEqual(len(self.env.recorded), 1)
        self.assertAlmostEqual(self.env.recorded[0]["price"], 288.14, places=6)

    def test_partial_fill_price_is_used_as_the_fill(self):
        """A partial fill still reports the average price it paid."""
        row = self.ns["build_buy_journal_row"](
            "ABBV", 1, 288.405, "BUY", 45, [],
            fill_info={"filled_avg_price": 288.05},
        )
        self.assertAlmostEqual(row["price"], 288.05, places=6)
        self.assertAlmostEqual(row["fill_price"], 288.05, places=6)

    # -- (2) no fill info -> fall back to the signal price ----------------
    def test_without_fill_info_the_row_falls_back_to_the_signal_price(self):
        row = self.ns["build_buy_journal_row"](
            "ABBV", 2, 288.405, "BUY", 45, [], fill_info=None,
        )
        self.assertIsNotNone(row["price"], "price must never be null")
        self.assertNotEqual(row["price"], 0, "price must never be 0")
        self.assertAlmostEqual(row["price"], 288.405, places=6)
        self.assertAlmostEqual(row["signal_price"], 288.405, places=6)
        # No fill happened, so no fill price may be invented.
        self.assertIsNone(row["filled_avg_price"])
        self.assertIsNone(row["fill_price"])

    def test_a_missing_or_zero_fill_price_falls_back_to_the_signal_price(self):
        for fill_info in ({"filled_avg_price": None}, {"filled_avg_price": 0},
                          {"filled_avg_price": "not-a-number"}, {}):
            with self.subTest(fill_info=fill_info):
                row = self.ns["build_buy_journal_row"](
                    "ABBV", 2, 288.405, "BUY", 45, [], fill_info=fill_info,
                )
                self.assertIsNotNone(row["price"])
                self.assertNotEqual(row["price"], 0)
                self.assertAlmostEqual(row["price"], 288.405, places=6)
                self.assertIsNone(row["filled_avg_price"])
                self.assertIsNone(row["fill_price"])

    def test_record_buy_journal_row_without_fill_info_does_not_write_null(self):
        row = self.ns["record_buy_journal_row"](
            "ABBV", 2, 288.405, "BUY", 45, [], fill_info=None,
        )
        self.assertIsNotNone(row)
        written = self.env.journal()[0]
        self.assertIsNotNone(written["price"])
        self.assertNotEqual(written["price"], 0)
        self.assertAlmostEqual(written["price"], 288.405, places=6)

    # -- (3) the live entry path uses the new writer ----------------------
    def test_the_entry_path_no_longer_journals_the_raw_signal_price(self):
        """The BUY journal row must go through record_buy_journal_row()."""
        src = AUTO_TRADE.read_text()
        self.assertNotIn(
            'record_trade("BUY", symbol, qty, analysis[\'price\']',
            src,
            "the entry path must not journal the signal price directly",
        )
        self.assertIn("record_buy_journal_row(", src)


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
