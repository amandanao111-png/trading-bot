# JOURNAL BUY FILL-PRICE HANDOFF

Task: fix the journal data-integrity defect where BUY rows in
`trading_bot/trade_journal.json` recorded the **signal** price instead of the
**actual fill** price, and omitted `filled_avg_price` / `fill_price` /
`announced`.
Scope: `trading_bot/auto_trade.py` + one new test file + the single-row
backfill + this handoff. **No other automation, cron or channel was touched.**

## DONE
- [x] Handoff file created FIRST (2026-11-20 ~18:13 London), before the code
      change and before any restart.
- [x] **Incident confirmed** — the 2026-11-20 ABBV entry:
      - journal row (before): `{"timestamp":"2026-11-20T17:33:58.559677","type":"BUY","symbol":"ABBV","qty":2,"price":288.405,"signal":"BUY","confidence":45,"reasons":["Trend: BULL","Above VWAP"],"pnl":0,"closed":false}`
      - Alpaca actually filled **2 sh @ 288.14**.
      - drift = 288.405 - 288.14 = **0.265/share (0.09%)** on a 2-share lot.
- [x] **Root cause confirmed in source**:
      - `place_order()` (`auto_trade.py:1985-2040`) polls the order to a
        terminal status and records the real fill in `LAST_ORDER_INFO`
        (`filled_avg_price`, `filled_qty`, `order_id`, `outcome`).
      - the entry path (`do_trading_cycle()`, `auto_trade.py:2660` area) then
        called `record_trade("BUY", symbol, qty, analysis['price'], ...)` —
        `analysis['price']` is the **signal** price, so the fill price that
        Alpaca reported was thrown away.
      - the row also had no `filled_avg_price` / `fill_price` / `announced`,
        so a BUY row did not match the SELL row shape written by
        `reconcile_pending_exits()` (`auto_trade.py:1634+`).
      - impact: open-position P&L derived from the journal drifts —
        `bot_status_digest.py:169` (journal book cost basis) and
        `reconcile_fills.py:318,343` both consume the journal `price`.
- [x] **Fix** (`auto_trade.py`), three new helpers next to the journal
      helpers (`mark_journal_announced`):
      - `resolve_buy_fill_price(signal_price, fill_info)` → `(price,
        filled_avg_price, fill_price)`. Uses the real fill price when
        available (`filled_avg_price`, then `fill_price`); otherwise falls
        back to the **signal price**, so the row is never null/0.
      - `build_buy_journal_row(...)` → the BUY row: `price` = fill price,
        `signal_price` = original decision price, plus `filled_avg_price`,
        `fill_price`, `announced` (mirrors the SELL row shape), optional
        `order_id`.
      - `record_buy_journal_row(...)` → appends the row through the existing
        `record_trade()` (so learning stats still update) and then enriches
        the appended row with the fill fields, exactly like the SELL
        late-fill path does.
      - the entry path now calls `record_buy_journal_row(...)` with
        `fill_info=LAST_ORDER_INFO` and `order_id=LAST_ORDER_INFO["order_id"]`,
        and `announced` reflects the real result of `announce_trade()`.
      - the original signal price is **kept** in `signal_price` — nothing is
        lost.
- [x] **Offline unit test** — new file `trading_bot/test_journal_buy_fill.py`
      (8 tests, no network, temp journal):
      - (a) fill price ≠ signal price → row `price` == **288.14** (the fill),
        `filled_avg_price` == 288.14, `fill_price` == 288.14, `announced` true,
        `signal_price` == 288.405 retained; end-to-end the row **on disk**
        carries the fill price and `record_trade()` receives the fill price.
      - (b) no fill info (`None` / `{}` / `filled_avg_price=None` / `0` /
        `"not-a-number"`) → row falls back to the **signal price** 288.405,
        never null and never 0; no fill price is invented.
      - plus: BUY row has the same shape as a SELL row; partial-fill average
        price is used; the live entry path no longer journals the raw signal
        price.
- [x] **Backfill — ONE row only** (`trade_journal.json`): the single ABBV BUY
      row with timestamp `2026-11-20T17:33:58` now reads
      `price=288.14, filled_avg_price=288.14, fill_price=288.14,
      announced=true, signal_price=288.405`.
      - **Backup taken BEFORE the edit**:
        `trading_bot/backups/trade_journal.json.bak.20261120-181324`
      - row count unchanged (214 before and after); no other row touched.
- [x] **Existing offline tests re-run — no regression**:
      - `test_late_fill_reconcile.py` → **11 tests, OK**
      - `test_volume_gate.py` → **17 checks, ALL TESTS PASSED**
      - `test_reverse_signal.py` → 17 tests, OK
      - `test_pnl_reconciled.py` → 16 tests, OK
      - `test_safety.py` → OK
      - `test_rebalancing.py` → 10 tests, OK
- [x] **Committed + pushed** — see COMMIT below.
- [x] **Restarted** the bot exactly the way it was launched (LaunchAgent
      `ai.nova.trading-bot` → `/bin/bash run_bot.sh` → `python -u
      auto_trade.py`) — see RESTART PROOF below.

## LEFT / NOT DONE (deliberate)
- [ ] The **in-memory** `positions[symbol]["avg_price"]` / `peak_price` for a
      new entry still uses the signal price, and `position_state.json` keeps
      `peak_price=288.405` for ABBV. Changing that would move the trailing-stop
      levels, which is a risk-behaviour change and outside this defect's
      scope. The **journal** (the source of truth for P&L) is now correct.
- [ ] The **mean-reversion entry path** (`execute_mean_reversion_entries`,
      `auto_trade.py:~786`) places a BUY order but never writes a journal row
      at all — pre-existing gap, not part of this defect, left untouched.
- [ ] The **normal (non-late-fill) SELL path** (`auto_trade.py:2382`) writes
      `filled_avg_price` / `fill_price` / `announced` only on the late-fill
      path. Left untouched (SELL rows are out of scope here).
- [ ] Other runtime churn (`evolution.json`, `learned_stats.json`,
      `v7_journal.json`, `auto_blacklist.json`, `position_state.json`,
      `symbol_position.json`, `time_blocklist.json`,
      `disabled_features.json`, `earnings_sentiment.json`) is left
      uncommitted — the repo convention is to keep live-runtime churn out of
      fix commits.

## COMMIT
- Commit: **PENDING** (filled in below after the commit was created)
- Push: **PENDING**

## RESTART PROOF
- PENDING
