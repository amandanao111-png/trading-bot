#!/usr/bin/env python3
"""
test_pnl_reconciled.py — offline unit tests for the reconciliation rules in
pnl_reconciled.py (2026-11-17 fix).

No network: pnl_reconciled.py is a top-level script, so importing it would
run the live Alpaca API calls. Instead we parse its source and execute ONLY
its function definitions and simple constants — the pure part — and drive the
rules directly with synthetic ledgers.

Covered:
  (i)   flat account (open qty 0 everywhere) => PASS
  (ii)  non-flat account whose net fill shares equal the open position => PASS
  (iii) non-flat account with a mismatch => FAIL (the test must stay loud)
  plus: implied_start is correct for both flat and non-flat accounts.

Run with:
    python3 test_pnl_reconciled.py
"""
import ast
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent


def load_pure(path):
    """Execute only the pure definitions of a top-level script.

    Keeps the unit tests network-free without touching the live script's
    control flow: function defs and constant assignments are compiled into a
    fresh namespace; every other top-level statement (the Alpaca calls, the
    printing, the JSON snapshot) is never executed.
    """
    src = path.read_text()
    tree = ast.parse(src, filename=str(path))
    keep = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            keep.append(node)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            keep.append(node)
    ns = {'__name__': 'pnl_reconciled_pure'}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), 'exec'), ns)
    return ns


PURE = load_pure(HERE / 'pnl_reconciled.py')
balance_test = PURE['balance_test']
open_position_map = PURE['open_position_map']
implied_start_from_books = PURE['implied_start_from_books']


def position(symbol, qty, market_value, side=None):
    row = {'symbol': symbol, 'qty': str(qty), 'market_value': str(market_value)}
    if side:
        row['side'] = side
    return row


class TestFlatAccount(unittest.TestCase):
    """(i) A flat account must still reconcile every symbol to zero."""

    def test_flat_account_passes(self):
        net, mismatches = balance_test(
            {'AAPL': 100.0, 'MSFT': 50.0},
            {'AAPL': 100.0, 'MSFT': 50.0},
            {}, {})
        self.assertEqual(mismatches, [])
        self.assertEqual(net['AAPL'], 0.0)
        self.assertEqual(net['MSFT'], 0.0)

    def test_flat_account_with_short_round_trip_passes(self):
        # sold 100 short, bought back 100 -> flat
        net, mismatches = balance_test({'SLV': 100.0}, {}, {'SLV': 100.0}, {})
        self.assertEqual(mismatches, [])
        self.assertEqual(net['SLV'], 0.0)

    def test_flat_account_with_leftover_shares_fails(self):
        net, mismatches = balance_test({'AAPL': 100.0}, {'AAPL': 60.0}, {}, {})
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]['symbol'], 'AAPL')
        self.assertAlmostEqual(mismatches[0]['net_shares'], 40.0)
        self.assertAlmostEqual(mismatches[0]['open_qty'], 0.0)


class TestOpenPositions(unittest.TestCase):
    """(ii)/(iii) Non-flat accounts: net fills must explain the open position."""

    def test_open_long_matching_ledger_passes(self):
        open_pos = open_position_map([
            position('SLV', 62, 2661.04, 'long')])
        net, mismatches = balance_test({'SLV': 62.0}, {}, {}, open_pos)
        self.assertEqual(mismatches, [])
        self.assertEqual(net['SLV'], 62.0)

    def test_open_short_matching_ledger_passes(self):
        # sold 100 short and still short 100 -> ledger nets to -100
        open_pos = open_position_map([
            position('GLD', -100, -11850.00, 'short')])
        net, mismatches = balance_test({}, {}, {'GLD': 100.0}, open_pos)
        self.assertEqual(mismatches, [])
        self.assertEqual(net['GLD'], -100.0)

    def test_partially_covered_short_matches_remaining_position(self):
        # shorted 100, bought back 40 -> still short 60
        open_pos = open_position_map([
            position('GLD', -60, -7110.00, 'short')])
        net, mismatches = balance_test({'GLD': 40.0}, {}, {'GLD': 100.0}, open_pos)
        self.assertEqual(mismatches, [])
        self.assertEqual(net['GLD'], -60.0)

    def test_open_position_not_explained_by_ledger_fails(self):
        open_pos = open_position_map([
            position('SLV', 62, 2661.04, 'long')])
        net, mismatches = balance_test({'SLV': 40.0}, {}, {}, open_pos)
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]['symbol'], 'SLV')
        self.assertAlmostEqual(mismatches[0]['diff'], -22.0)

    def test_open_position_with_no_fills_fails(self):
        open_pos = open_position_map([
            position('SLV', 62, 2661.04, 'long')])
        net, mismatches = balance_test({}, {}, {}, open_pos)
        self.assertEqual(len(mismatches), 1)
        self.assertAlmostEqual(mismatches[0]['net_shares'], 0.0)
        self.assertAlmostEqual(mismatches[0]['open_qty'], 62.0)

    def test_one_mismatch_does_not_hide_a_matching_symbol(self):
        open_pos = open_position_map([
            position('SLV', 62, 2661.04, 'long'),
            position('AAPL', 10, 2050.00, 'long')])
        net, mismatches = balance_test({'SLV': 62.0, 'AAPL': 4.0}, {}, {}, open_pos)
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]['symbol'], 'AAPL')
        self.assertEqual(net['SLV'], 62.0)

    def test_sub_share_noise_is_tolerated(self):
        open_pos = open_position_map([
            position('SLV', 62, 2661.04, 'long')])
        net, mismatches = balance_test({'SLV': 62.4}, {}, {}, open_pos)
        self.assertEqual(mismatches, [])


class TestPositionMap(unittest.TestCase):
    def test_long_and_short_signs(self):
        m = open_position_map([
            position('SLV', 62, 2661.04, 'long'),
            position('GLD', -100, -11850.00, 'short')])
        self.assertEqual(m['SLV']['qty'], 62.0)
        self.assertEqual(m['GLD']['qty'], -100.0)
        self.assertEqual(m['SLV']['market_value'], 2661.04)
        self.assertEqual(m['GLD']['market_value'], -11850.00)

    def test_sign_derived_from_qty_when_side_missing(self):
        m = open_position_map([position('GLD', -100, -11850.00)])
        self.assertEqual(m['GLD']['qty'], -100.0)
        self.assertEqual(m['GLD']['side'], 'short')

    def test_empty_positions(self):
        self.assertEqual(open_position_map([]), {})
        self.assertEqual(open_position_map(None), {})


class TestImpliedStart(unittest.TestCase):
    """implied_start must be ~$100,000 for flat AND non-flat accounts."""

    def test_flat_account_implied_start(self):
        start = implied_start_from_books(139322.87, 39322.93, 0.0)
        self.assertAlmostEqual(start, 99999.94, places=2)

    def test_non_flat_account_implied_start_ignores_open_position(self):
        # equity 139,320.12 = cash 136,659.08 + open position value 2,661.04
        start = implied_start_from_books(139320.12, 36659.14, 2661.04)
        self.assertAlmostEqual(start, 99999.94, places=2)
        # the old (wrong) formula double-counted the open position
        old = 139320.12 - 36659.14
        self.assertAlmostEqual(old, 102660.98, places=2)
        self.assertGreater(old, start)

    def test_non_flat_account_matches_cash_identity(self):
        equity, cash, net_cash_pnl = 139320.12, 136659.08, 36659.14
        open_mv = equity - cash
        start = implied_start_from_books(equity, net_cash_pnl, open_mv)
        self.assertAlmostEqual(start, cash - net_cash_pnl, places=6)


if __name__ == '__main__':
    print('=' * 74)
    print('pnl_reconciled unit tests (offline, no Alpaca API access)')
    print('=' * 74)
    sys.stdout.flush()
    outcome = unittest.main(verbosity=2, exit=False).result
    sys.exit(0 if outcome.wasSuccessful() else 1)
