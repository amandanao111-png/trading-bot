# Volume gate fix — handoff notes (2026-11-17)

Status: code complete, verification re-run pending. Bot still on OLD code (PID 54242, Nov-12 start)
until the launchd restart below.

## DONE (files on disk in /Users/amandaai/.openclaw/workspace/trading_bot)

- `volume_gate.py` (NEW) — same-time-of-day volume baseline:
  - `ratio` = today's regular-session (09:30-16:00 ET) cumulative volume up to the decision
    clock, divided by the MEDIAN of the SAME clock-time cumulative volume over the last
    20 completed sessions.
  - `recent_ratio` = last 30 completed minutes vs the same 30 minutes on those sessions
    (used for the "High volume" score bonus only).
  - Fallbacks are always NEUTRAL 1.0 (never 0) and labelled in `info["baseline"]`:
    `same_time_of_day` | `insufficient_history` | `zero_baseline` | `no_today_volume` |
    `pre_open` | `unavailable` | `error`.
  - History cached per symbol per day; today's bars cached 120 s.
  - Uses the Alpaca data API with retry/backoff; never raises out of `evaluate()`.
- `test_volume_gate.py` (NEW) — offline unit tests (synthetic sessions, no network).
- `replay_volume_gate.py` (NEW) — OLD-vs-NEW replay over real history.
- `auto_trade.py` (EDITED) — integration:
  - imports `volume_gate.evaluate` (guarded; degrades to neutral 1.0).
  - `analyze_symbol()` now uses the new ratio for `volume_ratio`; keeps `volume_ok`
    travelling with the result and enforced at the ENTRY DECISION (gate-at-the-decision
    design preserved, no early `return None`).
  - `Indicators.VolumeConfirmation` left in place (legacy, unused by the gate).
  - `MIN_VOLUME_RATIO` 1.0 -> 0.90 (rationale in the code comment).
  - scan line format unchanged; added a volume-baseline line; per-symbol line now shows
    the volume ratio; skip print names the baseline used.

## WHY median, not mean (replay evidence)
Mean of 20 sessions is dragged up 40-60% by a few spike sessions (QQQ 2026-11-05 ran 94M
shares by 14:46 vs a ~25M normal day). The median tracks the typical day; threshold 0.90
then means "at least a typical day" while still rejecting dead tape (dead sessions 0.62-0.86).

## LEFT / TODO (in order)
1. Re-run `venv/bin/python test_volume_gate.py` and
   `venv/bin/python replay_volume_gate.py --sessions 5 --symbols SLV,AAPL,QQQ`;
   capture final numbers (threshold 0.90, median baseline).
2. `macro_events.json` — add (data-only, same schema, existing entries intact):
   Tue 2026-11-17: Oct retail sales 08:30 ET; industrial production 09:15 ET; Nov NAHB;
   HD + DIS earnings before the open; NVDA earnings after the close.
   Wed 2026-11-18: FOMC minutes 14:00 ET; Anthropic IPO pricing.
3. Commit + push (stage ONLY: auto_trade.py, volume_gate.py, test_volume_gate.py,
   replay_volume_gate.py, macro_events.json, VOLUME_GATE_HANDOFF.md).
4. Restart launchd job `ai.nova.trading-bot`; verify new PID start time > mtime of
   auto_trade.py (`ps -o lstart`); trades.log tail: 0 error/traceback/exception, and the
   new cycle prints "Scanned N/M symbols | volume gate: X pass, Y rejected".
5. Append entry to /Users/amandaai/.openclaw/workspace/memory/2026-11-17.md
   (each bullet needs `<!-- project: path:/Users/amandaai/.openclaw/workspace -->`).

## Commit hash
`804365e` — "volume gate: judge volume at the same time of day, not against a mixed 20-bar mean"
Pushed to origin/main (`0b41e15..804365e  main -> main`).
`469ca8b` — "docs: volume-gate handoff - commit hash, restart verification, replay numbers".

## macro_events.json — final state (data-only, event_guard.py untouched)
The parent session and this child appended the same Nov 17/18 events concurrently, so the file
ended up with 21 events and 9 duplicate pairs (every new event twice). It now carries **ONE entry
per real-world event (14 total)**: the original 4 entries are untouched, and the new entries are
Tue 2026-11-17: Retail sales (Oct) 08:30 | Industrial production + import prices 09:15 |
NAHB housing market index (Nov) 10:00 | Home Depot earnings (Q3) 06:00 (pre-open) |
Disney earnings (Q4) 06:00 (pre-open) | NVIDIA earnings (Q3) 16:20 (after the close).
Wed 2026-11-18: NVIDIA earnings reaction 09:30 | FOMC minutes 14:00 | Anthropic IPO pricing 16:30.
Thu 2026-11-19: Anthropic IPO debut (ANT) 09:30.
The two non-duplicate additions from the parent's set were kept — "NVIDIA earnings reaction
(Nov 17 print)" (blocks the first 45 min of the Nov 18 open, where the after-close NVDA print is
priced) and a new "Anthropic IPO debut (ANT)" on Nov 19 09:30 (the Wed pricing print lands after
the close, so the Thu debut is the window that actually carries the gap). Verified: valid JSON,
every entry has exactly the original 8 keys, no duplicate names, `event_guard.py` untouched, and
the guard blocks at 09:35 ET (Nov 17 pre-open window), 16:15 ET (NVDA pre-release),
09:35 ET Nov 18 (NVDA reaction) and 13:30 ET Nov 18 (FOMC minutes pre-release).

## Dedupe commit
`<DEDUPE_COMMIT>` — macro_events: collapse duplicate event pairs (21 -> 14 events).

## Restart verification
- `launchctl kickstart -k gui/$(id -u)/ai.nova.trading-bot` at 09:50 London.
- New PID **65612**, `ps -o lstart` = `Tue Nov 17 09:50:48 2026`; `auto_trade.py` mtime = `2026-11-17 09:44:07`.
  **PID start time is LATER than the code mtime** → the running process is on the new code.
- trades.log tail after the restart: `🚀 Starting V9 Trading Loop` / `⏰ 04:50 ET - Market CLOSED` /
  `💤 Sleeping 30 minutes until next check...` — no error/traceback/exception lines.
- Live integration check (`analyze_symbol("SLV")` at 04:50 ET, pre-open) returns `None` because
  `get_bars()` has fewer than 30 completed 5-min bars before the open — pre-existing behaviour,
  untouched by this patch (the gate is only reached after the 30-bar check). The volume-gate scan
  line (`Scanned N/M symbols | volume gate: X pass, Y rejected`) will first appear in trades.log
  on the first cycle after the 09:30 ET / 14:30 London open.

## Verification completed (parent re-ran it after the child run was killed)
- `venv/bin/python test_volume_gate.py` → **ALL TESTS PASSED** (23/23, MIN_VOLUME_RATIO=0.9).
- `venv/bin/python replay_volume_gate.py --sessions 5 --symbols SLV,AAPL,QQQ` →
  TOTAL old gate 24 pass / 21 rejected, new gate 24 pass / 21 rejected, with the decisions landing
  on the right symbols: on 2026-11-16 SLV goes 14:46 ET **0.53 rej → 1.66 PASS** and
  16:15 ET **0.33 rej → 1.52 PASS** (the BUY the old gate blocked), while dead sessions
  (SLV 0.62-0.86, AAPL 0.70-0.86) still reject.
