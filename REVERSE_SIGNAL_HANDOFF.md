# REVERSE_SIGNAL EXIT — HANDOFF (updated live)

Task: add a SIGNAL-REVERSAL EXIT to trading_bot/auto_trade.py so a HELD LONG is closed
when the analyzer flips to SELL (confidence >= 50). Work + tests + commit + push + RESTART bot.

## DONE
- [x] Handoff file created (2026-11-19 ~10:24 London) before any coding.
- [x] Read auto_trade.py: check_exit() (line 1374), exit constants (~296-307), main loop
      (line 1808 `for symbol in ALL_SYMBOLS`, analyze_symbol at 1812, check_exit call 1877).
- [x] Verified DEFINITIONS (not call sites): announce_trade() def line 74 (uses
      telegram_send.send_message, both exist); place_order() 1506; place_order_chunked() 1459;
      record_trade() imported from learning.py:58 (exists); set_sector_cooldown() exists.
      No `send_telegram(` call remains anywhere (grepped whole file).
- [x] Implemented REVERSE_SIGNAL:
      - config flags near exit constants: REVERSE_SIGNAL_EXIT_ENABLED = True,
        REVERSE_SIGNAL_MIN_CONFIDENCE = 50 (auto_trade.py ~line 301-317).
      - check_exit(symbol, position, analysis=None) — new optional param.
      - Rule evaluated LAST (lowest priority, after MACD_EXIT) so HARD_STOP /
        PARTIAL_TAKE_50% / TAKE_PROFIT / TRAILING_STOP / RSI_EXIT / MACD_EXIT
        all keep precedence. Long-only (shorts unaffected). No new API call:
        uses the current cycle's `analysis` passed in from the main loop.
      - Main loop now calls check_exit(symbol, positions[symbol], analysis).
      - Exits flow through the existing place_order_chunked()/sell path, so the
        existing announce_trade() Telegram message (symbol, qty, price, reason) fires.
- [x] Added trading_bot/test_reverse_signal.py — 17 tests, all PASS:
      long+SELL60 => REVERSE_SIGNAL; long+SELL40 => not; long+BUY => not;
      SHORT+SELL => not; long past -2% => HARD_STOP; flag-off disables;
      RSI_EXIT and TAKE_PROFIT outrank REVERSE_SIGNAL; no analysis => no exit;
      integration guards (no dead send_telegram call, all exit-path functions exist).
- [x] Full suite green:
      test_reverse_signal.py 17 OK | test_volume_gate.py ALL TESTS PASSED |
      test_pnl_reconciled.py 16 OK | test_rebalancing.py 10 OK | test_safety.py 19 OK
- [x] py_compile clean on auto_trade.py.

## LEFT
- [x] Commit + push origin/main  (see COMMIT below)
- [x] RESTART bot: old PID 65612 (Nov 17 09:50:48) killed; new PID started via
      launchctl kickstart -k on ai.nova.trading-bot; verified new PID start time is
      LATER than auto_trade.py mtime and trades.log shows a fresh "Market CLOSED" line.
- [x] Append dated entry to /Users/amandaai/.openclaw/workspace/memory/2026-11-19.md.

## COMMIT
b2e91b5 — trading bot: exit held longs when the analyzer flips to SELL (REVERSE_SIGNAL)
         (pushed to origin/main: a486f90..b2e91b5)
Also: a follow-up commit corrects this handoff's COMMIT hash (it was written
with a placeholder before the commit existed) — see `git log -2` for it.

## RESTART PROOF (done)
- Old PID 65612 (started Tue Nov 17 09:50:48) killed.
- `launchctl kickstart -k gui/501/ai.nova.trading-bot` -> **new PID 40552,
  started Thu Nov 19 10:31:19 2026**.
- auto_trade.py mtime = 2026-11-19 10:27:28  ->  new process start is LATER ✔
- trades.log: line 16090 `🚀 Starting V9 Trading Loop`, line 16093
  `⏰ 05:31 ET - Market CLOSED`  ✔ (market closed, so this is the proof line)

## MEMORY NOTE
- /Users/amandaai/.openclaw/workspace/memory/2026-11-19.md appended (8 lines, each
  with the project marker) and committed in the WORKSPACE ROOT repo as fab8ba3
  (that repo was otherwise left untouched).

## SCOPE LIMITS (respected — not touched)
- market-open-monitor cron schedule (already fixed separately, 15:10 London)
- pnl_authoritative.py / pnl_truth_final.py / pnl_cash.py scratch scripts
- fill-reconciler, heartbeat config, any other cron job
- any exit rule other than adding REVERSE_SIGNAL
- workspace root repo: only the memory note (2026-11-19.md) committed there
