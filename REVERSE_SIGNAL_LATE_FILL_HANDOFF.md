# REVERSE_SIGNAL LATE-FILL HANDOFF

Task: fix the bug where an exit order that fills LATE (after the cycle that
placed it) leaves no Telegram announcement and no trade_journal.json SELL row.
Scope: `trading_bot/auto_trade.py` + one new test file + this handoff.

## DONE
- [x] Handoff file created FIRST (2026-11-19 ~15:25 London), before any code change.
- [x] Reproduced/confirmed the incident from trades.log lines 16147-16160 and
      from Alpaca `GET /v2/orders`:
        - 2026-11-19T14:31:40.262272Z  AAPL  sell 5  market  **filled**  filled_qty=5  filled_avg_price=352.858
        - Alpaca order id: **77f266bd-1ad7-4188-ae5e-f2db118ec487**
      (the ABBV HARD_STOP sell that followed is order id
       d675ca01-0b6b-4cec-bb42-f1daf9ee7b6c, filled 2 @ 281.64 - that one DID
       announce + journal because it filled inside its own cycle.)
- [x] Root cause confirmed in source:
      - `place_order()` (auto_trade.py:1542) polls GET /v2/orders/{id}; when the
        status is a working one (`new`/`accepted`/`pending_new`/`partially_filled`)
        it deliberately leaves the order live and returns **False**.
      - `place_order_chunked()` (auto_trade.py:1495) sees False and prints
        `⚠️ Chunk of 5 failed — 0/5 covered` -> **misleading** (C).
      - The caller in `do_trading_cycle()` (auto_trade.py:1931) only runs the
        exit bookkeeping (record_trade -> journal row, announce_trade ->
        Telegram, sector cooldown, `del positions[symbol]`) **inside**
        `if place_order_chunked(...):` -> a left-live order skips ALL of it.
      - Next cycle the position is read from Alpaca, where it is already gone,
        so nothing looks wrong -> the exit vanished silently.
- [x] **A. Pending-exit bookkeeping** - new runtime file
      `trading_bot/pending_exit_orders.json` (gitignored) next to
      `position_state.json`. Written by `record_pending_exit()` whenever an
      exit order is left live. Each record holds: order_id, symbol, side,
      requested_qty, filled_qty, filled_avg_price, exit_reason, exit_detail,
      entry_price, confidence, is_short, is_partial, status, created_at.
- [x] **B. Reconcile on the next cycle** - `reconcile_pending_exits()` is
      called at the TOP of `do_trading_cycle()` (after `load_position_state`,
      BEFORE any `check_exit()`), so it runs on the first cycle after a cycle
      that left orders live:
        - filled / partially filled (filled_qty > 0)
            -> writes the trade_journal.json SELL row (symbol, qty actually
               filled, filled avg price, exit reason, pnl), sends the Telegram
               announcement through the EXISTING `announce_trade()` helper
               (which calls workspace `telegram_send.send_message(chat_id,
               text)` - no new channel invented), updates the position/peak
               state, sets the sector cooldown for HARD_STOP parity, and
               prints `✅ Reconciled late fill: <SYM> <n> sh @ <price> (<REASON>)`.
        - canceled / expired / rejected -> logged, pending entry dropped, NO
               announcement.
        - still working -> stays pending, NO announcement, NO journal row, NO
               spam.
- [x] **Idempotency** - two independent guards:
        (1) the journal row is keyed on `order_id` (`journal_has_order()`),
        (2) the announcement is guarded by a durable `announced` stamp ON the
            journal row (`mark_journal_announced()`) plus the pending record.
        So reconciling the same order 3x writes exactly 1 journal row and sends
        exactly 1 Telegram message - even if the pending file is rolled back
        to a stale copy.
- [x] **C. Misleading message fixed** - `place_order()` now reports what
      actually happened via `LAST_ORDER_INFO` (`outcome` = filled / working /
      dead / error). `place_order_chunked()` prints
      `ℹ️ Order still working (status=new) - left live, will reconcile next cycle (not a failure)`
      instead of `⚠️ Chunk of N failed — 0/N covered` for a working order.
- [x] **D. REVERSE_SIGNAL** - REVERSE_SIGNAL has no separate exit copy: it is
      a reason returned by `check_exit()` and it flows through the same
      `place_order_chunked()` call, so it now records a pending exit with
      `exit_reason="REVERSE_SIGNAL"` and is announced with that reason.
      Verified end-to-end in the test suite (AAPL scenario, real order id).
- [x] Extra safety: a symbol that already has a LIVE exit order is skipped
      for new exit evaluation in the same cycle (`pending_exit_symbols()`),
      so two live orders can never over-close a position.
- [x] **Tests** - new `trading_bot/test_late_fill_reconcile.py`, 11 tests,
      ALL PASS. Fully offline: the Alpaca orders API is mocked, no live order
      is ever placed (a test even asserts the reconcile path never DELETEs an
      order). Cases: filled / partially_filled / cancelled+expired+rejected /
      still-working / idempotency (3x) / idempotency with a stale pending
      file / end-to-end place->left-live->reconcile with reason
      REVERSE_SIGNAL / the misleading "Chunk of N failed" line is NOT printed
      / reconcile runs BEFORE check_exit / a live exit order blocks a second
      exit order.
- [x] Full suite green:
      test_late_fill_reconcile.py 11 OK | test_reverse_signal.py 17 OK |
      test_volume_gate.py ALL TESTS PASSED | test_pnl_reconciled.py 16 OK |
      test_rebalancing.py 10 OK | test_safety.py 19 OK
- [x] py_compile clean on auto_trade.py.

## LEFT
- [x] Commit + push to origin/main.
- [x] Restart bot (old PID 40552) and prove the new code is live.

## COMMIT HASH
**aa5da5e** — `trading bot: reconcile late exit fills (no more silent exits)`
(pushed to origin/main: `9e45ca8..aa5da5e`)

A follow-up commit records this handoff's own hash + the restart proof, same
pattern as the REVERSE_SIGNAL handoff (see `git log -2`).

## NEW PID + START TIME
- Old PID **40552** (started Thu Nov 19 10:31:19 2026) — stopped via
  `launchctl kickstart -k gui/501/ai.nova.trading-bot`.
- New PID **49422**, started **Thu Nov 19 15:28:18 2026**.
- `auto_trade.py` mtime: **2026-11-19 15:27:12**
- `ps -o lstart= -p 49422` -> **Thu Nov 19 15:28:18 2026**
  -> the process start is LATER than the code mtime -> the NEW code is live ✔
- The bot's first cycle after the restart ran clean at 10:28 ET: the BAC
  REVERSE_SIGNAL exit (27 sh @ 48.70037) filled in-cycle and was announced +
  journaled normally. `trades.err.log` was NOT touched by the new process
  (mtime still 2026-10-24 14:42:04 - a 26-day-old error from a previous code
  version, not from this restart).

## PROOF LINE
`trades.log` line **16249** (fresh startup banner on line 16247):

```
🚀 Starting V9 Trading Loop
   Checking every 30 minutes during market hours
🔎 Pending-exit reconcile enabled - late exit fills are reconciled on the next cycle
```

The per-cycle reconcile line (printed only when there IS a pending exit order
to reconcile) is:
`🔎 Reconciling N pending exit order(s)...`
and a completed late fill prints:
`   ✅ Reconciled late fill: <SYM> <n> sh @ <price> (<REASON>)`

## ALPACA ORDER ID USED AS THE RECONCILIATION TEST CASE
**77f266bd-1ad7-4188-ae5e-f2db118ec487** — the real 2026-11-19 AAPL
REVERSE_SIGNAL sell (5 sh, market, submitted 2026-11-19T14:31:40.262272Z,
status filled, filled_qty 5, filled_avg_price 352.858).

It is used verbatim as the order id in `test_late_fill_reconcile.py`
(`REAL_ORDER_ID`) so the test stays traceable to the real incident. The
reconcile code is driven by order id, so a synthetic id behaves identically;
the test mocks GET /v2/orders/{id} and NEVER places a live order (one test
even asserts the reconcile path never issues a DELETE against an order).

## HOW TO VERIFY THE FIX LIVE
If an exit order is ever left live again, the next cycle prints:
```
🔎 Reconciling 1 pending exit order(s)...
   ✅ Reconciled late fill: AAPL 5 sh @ 352.858 (REVERSE_SIGNAL)
   📚 Learning recorded / 📣 Telegram announce sent
```
and `trade_journal.json` gets a SELL row carrying `"order_id"` and
`"late_fill": true`. Reconciling the same order again prints
`Journal row already exists ... - not writing again` and
`Telegram announce already sent ... - not repeating`.


## SCOPE LIMITS
- Only `trading_bot/auto_trade.py`, `trading_bot/test_late_fill_reconcile.py`
  and this handoff file.
- No live/test order is placed by the tests (fully mocked orders API).
- No cron, gateway, heartbeat or other-project changes.
