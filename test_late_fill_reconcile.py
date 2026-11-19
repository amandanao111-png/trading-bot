#!/usr/bin/env python3
"""
test_late_fill_reconcile.py — offline unit tests for the LATE-FILL RECONCILE path.

Bug being fixed (2026-11-19): an exit order that is left LIVE at the exchange
fills after the cycle that placed it. place_order() returns False for a working
order, so the caller never ran the exit bookkeeping -> NO trade_journal.json
SELL row and NO Telegram announcement. The exit simply vanished.

Live example: the AAPL REVERSE_SIGNAL sell (5 sh, Alpaca order
77f266bd-1ad7-4188-ae5e-f2db118ec487, submitted 2026-11-19T14:31:40Z) sat
"new" at the end of its cycle and filled at 352.858 a minute later.

NO NETWORK: every test drives the real reconcile code with a mocked Alpaca
orders API. No live order is ever placed.

auto_trade.py is a live script (it exits at import time when the Alpaca keys
are missing and hits the API at module level), so it cannot be imported
directly. Instead we parse its source and execute ONLY the function
definitions and literal constant assignments into a fresh namespace, then
stub the collaborators (requests / announce_trade / record_trade / ...).
Same trick as test_reverse_signal.py and test_pnl_reconciled.py.

Covered:
  (1) filled order          -> exactly one journal row + one announcement
  (2) partially filled      -> exactly one journal row + one announcement
  (3) cancelled / expired   -> no journal row, no announcement, entry dropped
  (4) still working         -> entry kept pending, no journal row, no announce
  (5) idempotency           -> reconciling twice writes/announces ONCE
  (6) end-to-end            -> place_order leaves the order live -> pending
                                record written with reason REVERSE_SIGNAL ->
                                next cycle reconciles it (journal + announce)
  (7) the misleading "Chunk of N failed - 0/N covered" line is NOT printed
                                for a still-working order
  (8) a symbol with a live exit order is not given a second exit order

Run with:
    python3 test_late_fill_reconcile.py
"""
import ast
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
AUTO_TRADE = HERE / "auto_trade.py"

# The real Alpaca order id from the 2026-11-19 incident. Used as the
# reconciliation test case (the reconcile code is driven by order id, so a
# synthetic id works identically - this one keeps the test traceable to the
# real incident).
REAL_ORDER_ID = "77f266bd-1ad7-4188-ae5e-f2db118ec487"


# --------------------------------------------------------------------------
# Source loader
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
# Fakes
# --------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeRequests:
    """Mocked Alpaca API. Routes POST /v2/orders, GET /v2/orders/{id},
    GET /v2/account and DELETE /v2/orders/{id}."""

    def __init__(self):
        self.orders = {}          # order id -> order dict
        self.post_order = None    # what the next POST returns
        self.post_status = 200
        self.deleted = []
        self.get_order_status = 200

    def post(self, url, headers=None, json=None, timeout=None):
        if self.post_status not in (200, 201):
            return FakeResponse(self.post_status, {"message": "rejected"})
        order = dict(self.post_order or {})
        order.setdefault("id", "ord-new")
        order.setdefault("status", "new")
        order.setdefault("filled_qty", 0)
        self.orders[order["id"]] = order
        return FakeResponse(200, order)

    def get(self, url, headers=None, timeout=None):
        if url.endswith("/v2/account"):
            return FakeResponse(200, {"buying_power": 1000000})
        for oid, order in self.orders.items():
            if url.endswith("/v2/orders/" + oid):
                return FakeResponse(self.get_order_status, order)
        return FakeResponse(404, {"message": "not found"})

    def delete(self, url, headers=None, timeout=None):
        self.deleted.append(url)
        return FakeResponse(200, {})


class FakeTime:
    """Deterministic clock: every time() call jumps forward so the 60s
    place_order() poll deadline is reached after ONE poll instead of 60s of
    real sleeping."""

    def __init__(self, step=30.0):
        self.t = 1000.0
        self.step = step

    def time(self):
        self.t += self.step
        return self.t

    def sleep(self, seconds):
        return None


class Env:
    """One isolated reconcile environment (temp journal + temp pending file)."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="latefill-")
        self.journal_path = os.path.join(self.tmp, "trade_journal.json")
        self.pending_path = os.path.join(self.tmp, "pending_exit_orders.json")
        self.requests = FakeRequests()
        self.announcements = []
        self.recorded = []
        self.cooldowns = []
        self.ns = build_namespace()

        ns = self.ns
        ns["os"] = os
        ns["json"] = json
        ns["datetime"] = datetime
        ns["timedelta"] = timedelta
        ns["requests"] = self.requests
        ns["time"] = FakeTime()
        ns["BASE_URL"] = "https://paper-api.alpaca.markets"
        ns["HEADERS"] = {"APCA-API-KEY-ID": "x", "APCA-API-SECRET-KEY": "y"}
        ns["TRADE_JOURNAL_FILE"] = self.journal_path
        ns["PENDING_EXIT_FILE"] = self.pending_path
        ns["announce_trade"] = self._announce
        ns["record_trade"] = self._record_trade
        ns["set_sector_cooldown"] = self._cooldown

    # -- stubs -------------------------------------------------------------
    def _announce(self, text):
        self.announcements.append(text)
        return True

    def _record_trade(self, trade_type, symbol, qty, price, signal, confidence,
                      reasons, pnl=0):
        """Mimics learning.record_trade(): appends ONE SELL row to the journal."""
        self.recorded.append({
            "type": trade_type, "symbol": symbol, "qty": qty, "price": price,
            "signal": signal, "confidence": confidence, "reasons": reasons, "pnl": pnl,
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

    def _cooldown(self, symbol):
        self.cooldowns.append(symbol)

    # -- helpers -----------------------------------------------------------
    def journal(self):
        try:
            with open(self.journal_path) as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def pending(self):
        return self.ns["load_pending_exits"]()

    def write_pending(self, orders):
        with open(self.pending_path, "w") as fh:
            json.dump(orders, fh, indent=2)

    def seed_pending(self, order_id=REAL_ORDER_ID, symbol="AAPL", side="sell",
                     qty=5, reason="REVERSE_SIGNAL", entry_price=353.054,
                     confidence=50, is_short=False, is_partial=False):
        self.write_pending({order_id: {
            "order_id": order_id, "symbol": symbol, "side": side,
            "requested_qty": qty, "filled_qty": 0, "filled_avg_price": None,
            "exit_reason": reason, "exit_detail": "SELL signal @ 50% confidence",
            "entry_price": entry_price, "confidence": confidence,
            "is_short": is_short, "is_partial": is_partial,
            "status": "pending", "created_at": datetime.now().isoformat(),
        }})

    def seed_order(self, order_id=REAL_ORDER_ID, status="filled", filled_qty=5,
                   avg_price=352.858):
        self.requests.orders[order_id] = {
            "id": order_id, "symbol": "AAPL", "side": "sell", "qty": "5",
            "type": "market", "status": status, "filled_qty": filled_qty,
            "filled_avg_price": avg_price,
        }

    def run_reconcile(self, positions=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = self.ns["reconcile_pending_exits"](positions if positions is not None else {})
        self.last_output = buf.getvalue()
        return result

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
class LateFillReconcileTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.cleanup)

    # -- (1) filled --------------------------------------------------------
    def test_filled_order_writes_one_journal_row_and_one_announcement(self):
        self.env.seed_pending()
        self.env.seed_order(status="filled", filled_qty=5, avg_price=352.858)

        result = self.env.run_reconcile()

        rows = self.env.journal()
        self.assertEqual(len(rows), 1, "exactly one journal row expected")
        row = rows[0]
        self.assertEqual(row["type"], "SELL")
        self.assertEqual(row["symbol"], "AAPL")
        self.assertEqual(row["qty"], 5)
        self.assertAlmostEqual(row["price"], 352.858, places=3)
        self.assertEqual(row["signal"], "REVERSE_SIGNAL")
        self.assertEqual(row["order_id"], REAL_ORDER_ID)
        self.assertTrue(row["late_fill"])
        self.assertAlmostEqual(row["pnl"], (352.858 - 353.054) * 5, places=6)

        self.assertEqual(len(self.env.announcements), 1, "exactly one announcement")
        text = self.env.announcements[0]
        self.assertIn("AAPL", text)
        self.assertIn("REVERSE_SIGNAL", text)
        self.assertIn("352.86", text)

        self.assertIn("Reconciled late fill", self.env.last_output)
        self.assertEqual(result["filled"], [REAL_ORDER_ID])

    # -- (2) partially filled ---------------------------------------------
    def test_partially_filled_order_is_reconciled(self):
        self.env.seed_pending(qty=5)
        self.env.seed_order(status="partially_filled", filled_qty=2, avg_price=352.5)

        self.env.run_reconcile()

        rows = self.env.journal()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["qty"], 2)
        self.assertAlmostEqual(rows[0]["price"], 352.5, places=3)
        self.assertEqual(len(self.env.announcements), 1)
        self.assertIn("Reconciled late fill", self.env.last_output)

    # -- (3) cancelled / expired ------------------------------------------
    def test_cancelled_order_is_dropped_without_announcement(self):
        for dead_status in ("canceled", "expired", "rejected"):
            with self.subTest(status=dead_status):
                env = Env()
                self.addCleanup(env.cleanup)
                env.seed_pending(order_id=f"ord-{dead_status}")
                env.seed_order(order_id=f"ord-{dead_status}", status=dead_status,
                               filled_qty=0, avg_price=None)

                result = env.run_reconcile()

                self.assertEqual(env.journal(), [], "no journal row for a dead order")
                self.assertEqual(env.announcements, [], "no announcement for a dead order")
                self.assertEqual(result["dead"], [f"ord-{dead_status}"])
                rec = env.pending().get(f"ord-{dead_status}")
                self.assertIsNotNone(rec)
                self.assertNotEqual(rec["status"], "pending",
                                    "a dead order must not stay pending")

    # -- (4) still working -------------------------------------------------
    def test_still_working_order_stays_pending_without_announcement(self):
        self.env.seed_pending()
        self.env.seed_order(status="new", filled_qty=0, avg_price=None)

        result = self.env.run_reconcile()

        self.assertEqual(self.env.journal(), [], "no journal row while still working")
        self.assertEqual(self.env.announcements, [], "no announcement while still working")
        self.assertEqual(result["filled"], [])
        self.assertEqual(result["still_working"], [REAL_ORDER_ID])
        rec = self.env.pending().get(REAL_ORDER_ID)
        self.assertIsNotNone(rec, "a working order must stay pending")
        self.assertEqual(rec["status"], "pending")
        self.assertNotIn("Reconciled late fill", self.env.last_output)

    # -- (5) idempotency ---------------------------------------------------
    def test_reconcile_is_idempotent(self):
        self.env.seed_pending()
        self.env.seed_order(status="filled", filled_qty=5, avg_price=352.858)

        self.env.run_reconcile()
        self.env.run_reconcile()
        self.env.run_reconcile()

        self.assertEqual(len(self.env.journal()), 1,
                         "reconciling 3x must still leave exactly one journal row")
        self.assertEqual(len(self.env.announcements), 1,
                         "reconciling 3x must still send exactly one announcement")
        self.assertEqual(len(self.env.recorded), 1,
                         "record_trade must be called exactly once")

    def test_idempotent_even_when_pending_entry_is_reintroduced(self):
        """A stale copy of the pending file must not produce a 2nd row."""
        self.env.seed_pending()
        self.env.seed_order(status="filled", filled_qty=5, avg_price=352.858)
        self.env.run_reconcile()

        # Re-seed the entry exactly as it was before the first reconcile.
        self.env.seed_pending()
        self.env.run_reconcile()

        self.assertEqual(len(self.env.journal()), 1)
        self.assertEqual(len(self.env.announcements), 1)

    # -- (6) end to end: place -> left live -> reconcile ------------------
    def test_reverse_signal_exit_left_live_is_reconciled_next_cycle(self):
        """The real 2026-11-19 AAPL scenario, end to end."""
        ns = self.env.ns
        self.env.requests.post_order = {
            "id": REAL_ORDER_ID, "symbol": "AAPL", "side": "sell", "qty": "5",
            "status": "new", "filled_qty": 0, "filled_avg_price": None,
        }

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            filled = ns["place_order_chunked"](
                "AAPL", 5, "sell", 352.9, reduce_only=True,
                exit_reason="REVERSE_SIGNAL",
                exit_detail="SELL signal @ 50% confidence",
                entry_price=353.054, confidence=50,
                is_short=False, is_partial=False,
            )
        out = buf.getvalue()

        self.assertFalse(filled, "a working order is not a fill")
        self.assertNotIn("Chunk of 5 failed", out,
                         "the misleading 'Chunk of N failed' line must not be printed "
                         "for an order that is still working")
        self.assertIn("still working", out.lower())
        self.assertIn("not a failure", out.lower())

        rec = self.env.pending().get(REAL_ORDER_ID)
        self.assertIsNotNone(rec, "the live exit order must be persisted")
        self.assertEqual(rec["symbol"], "AAPL")
        self.assertEqual(rec["side"], "sell")
        self.assertEqual(rec["requested_qty"], 5)
        self.assertEqual(rec["exit_reason"], "REVERSE_SIGNAL")
        self.assertEqual(rec["status"], "pending")
        self.assertAlmostEqual(rec["entry_price"], 353.054, places=3)

        # The order fills a minute later, at the exchange.
        self.env.requests.orders[REAL_ORDER_ID].update({
            "status": "filled", "filled_qty": 5, "filled_avg_price": 352.858,
        })

        result = self.env.run_reconcile()

        self.assertEqual(result["filled"], [REAL_ORDER_ID])
        rows = self.env.journal()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["signal"], "REVERSE_SIGNAL")
        self.assertEqual(rows[0]["qty"], 5)
        self.assertEqual(len(self.env.announcements), 1)
        self.assertIn("REVERSE_SIGNAL", self.env.announcements[0])
        self.assertIn("Reconciled late fill", self.env.last_output)

    # -- (7) / (8) source-level guards ------------------------------------
    def test_main_loop_passes_exit_reason_to_place_order_chunked(self):
        calls = [n for n in ast.walk(_TREE)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "place_order_chunked"]
        self.assertTrue(calls, "no place_order_chunked() call site found")
        for call in calls:
            kw = {k.arg: k.value for k in call.keywords}
            self.assertIn("exit_reason", kw,
                          "place_order_chunked() must receive the exit reason so the "
                          "pending record can be reconciled and announced with it")

    def test_reconcile_is_called_before_new_exits_are_evaluated(self):
        src = AUTO_TRADE.read_text()
        fn = [n for n in ast.walk(_TREE)
              if isinstance(n, ast.FunctionDef) and n.name == "do_trading_cycle"][0]
        body = ast.get_source_segment(src, fn) or ""
        self.assertIn("reconcile_pending_exits(", body,
                      "do_trading_cycle() must reconcile pending exits")
        self.assertLess(body.index("reconcile_pending_exits("),
                        body.index("check_exit("),
                        "the reconcile must run BEFORE new exits are evaluated")

    def test_live_exit_symbol_is_not_given_a_second_exit_order(self):
        ns = self.env.ns
        self.env.seed_pending(symbol="AAPL")
        self.assertIn("AAPL", ns["pending_exit_symbols"](),
                      "a symbol with a live exit order must be reported as pending")
        # The done/dead records must not block a future exit.
        self.env.write_pending({REAL_ORDER_ID: {
            "order_id": REAL_ORDER_ID, "symbol": "AAPL", "side": "sell",
            "requested_qty": 5, "exit_reason": "REVERSE_SIGNAL",
            "status": "done", "created_at": datetime.now().isoformat(),
        }})
        self.assertNotIn("AAPL", ns["pending_exit_symbols"](),
                         "a resolved order must not block future exits")

    def test_no_live_order_is_placed_by_the_tests(self):
        """Guard: the reconcile path must only READ orders."""
        self.env.seed_pending()
        self.env.seed_order(status="filled", filled_qty=5, avg_price=352.858)
        self.env.run_reconcile()
        self.assertEqual(self.env.requests.deleted, [],
                         "the reconcile path must never cancel an order")


if __name__ == "__main__":
    print("=" * 68)
    print("test_late_fill_reconcile.py — late-fill reconcile (offline, mocked API)")
    print("=" * 68)
    result = unittest.main(verbosity=2, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
