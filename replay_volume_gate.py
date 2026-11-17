#!/usr/bin/env python3
"""
replay_volume_gate.py — replay the OLD vs NEW volume gate over real history.

For each symbol and each decision clock (14:46 / 15:45 / 16:15 ET) it replays
the last N completed sessions and prints, side by side:

  * OLD gate ratio  — the legacy single-completed-bar vs 20-bar-mean metric
  * NEW gate ratio  — session-cumulative volume vs the same clock-time average
  * pass / reject   — against MIN_VOLUME_RATIO

Usage:
    venv/bin/python replay_volume_gate.py [--sessions 5] [--symbols SLV,AAPL,QQQ]
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import volume_gate as vg

CLOCKS = [("14:46", 14 * 60 + 46), ("15:45", 15 * 60 + 45), ("16:15", 16 * 60 + 15)]


def configured_min_ratio(default=1.0):
    """Read MIN_VOLUME_RATIO straight out of auto_trade.py.

    The replay must judge pass/reject with the LIVE threshold, otherwise the
    table silently drifts away from what the bot actually does.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto_trade.py")
    try:
        with open(path) as fh:
            m = re.search(r"^MIN_VOLUME_RATIO\s*=\s*([0-9.]+)", fh.read(), re.M)
        return float(m.group(1)) if m else default
    except Exception:
        return default


MIN_RATIO = configured_min_ratio()


def old_gate_ratio(session, cutoff_min, window=100):
    """Legacy gate: last COMPLETED bar vs the mean of the 20 before it.

    The bot fetched limit=100 5-min bars ending at the decision clock, so the
    window is the last 100 bars of the session up to that clock.
    """
    bars = [b for b in session if b["end_min"] <= cutoff_min]
    if len(bars) < 22:
        return None
    vols = [b["v"] for b in bars[-window:]]
    completed = vols[-22:-2]
    avg = sum(completed) / len(completed)
    if avg <= 0:
        return None
    return vols[-2] / avg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=5, help="completed sessions to replay")
    ap.add_argument("--symbols", default="SLV,AAPL,QQQ")
    ap.add_argument("--baseline", type=int, default=vg.BASELINE_SESSIONS)
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    now = datetime.now(vg.ET)

    print("=" * 100)
    print(f"VOLUME GATE REPLAY  (old gate vs new same-time-of-day gate)   now={now.isoformat()}")
    print(f"threshold: MIN_VOLUME_RATIO={MIN_RATIO} (read from auto_trade.py)   "
          f"new baseline: median of the same clock-time cumulative over up to {args.baseline} sessions")
    print("=" * 100)

    grand = {"old": [0, 0], "new": [0, 0]}

    for symbol in symbols:
        # ---- pull history -------------------------------------------------
        collected = {}
        for offset in vg.HISTORY_CHUNK_DAYS:
            start = vg._utc_iso(now - timedelta(days=offset))
            bars = vg.fetch_bars(symbol, start=start, limit=10000)
            if bars is None:
                print(f"  ⚠️ {symbol}: history fetch failed for chunk start={start}")
                continue
            for d, lst in vg.split_sessions(bars).items():
                collected.setdefault(d, lst)
            if len(collected) > args.baseline + args.sessions + 3:
                break

        days = sorted(collected)
        if not days:
            print(f"  ⚠️ {symbol}: no history available")
            continue
        # today (partial) is not a completed session -> drop it
        today_day = now.date().isoformat()
        completed_days = [d for d in days if d < today_day]
        replay_days = completed_days[-args.sessions:]

        print(f"\n--- {symbol} ---  history sessions available: {len(completed_days)} "
              f"({completed_days[0]}..{completed_days[-1]}), replaying: {', '.join(replay_days)}")
        print(f"{'date':<12}{'clock':<8}{'old ratio':>11}{'old':>7}{'new ratio':>12}{'new':>7}"
              f"{'today vol':>13}{'base vol':>13}{'sess':>6}")
        for day in replay_days:
            session = collected[day]
            prior = [collected[d] for d in completed_days if d < day][-args.baseline:]
            for label, cutoff in CLOCKS:
                old = old_gate_ratio(session, cutoff)
                new, _recent, info = vg.compute_gate(session, prior, cutoff)
                old_pass = (old is not None and old >= MIN_RATIO)
                new_pass = new >= MIN_RATIO
                grand["old"][0 if old_pass else 1] += 1
                grand["new"][0 if new_pass else 1] += 1
                old_txt = f"{old:.2f}" if old is not None else "n/a"
                print(f"{day:<12}{label:<8}{old_txt:>11}{('PASS' if old_pass else 'rej'):>7}"
                      f"{new:>12.2f}{('PASS' if new_pass else 'rej'):>7}"
                      f"{info['today_volume']:>13,.0f}{info['baseline_volume']:>13,.0f}"
                      f"{info['sessions']:>6}")

    print("\n" + "=" * 100)
    print(f"TOTAL pass/reject   OLD gate: {grand['old'][0]} pass / {grand['old'][1]} rejected   |   "
          f"NEW gate: {grand['new'][0]} pass / {grand['new'][1]} rejected")
    print("=" * 100)


if __name__ == "__main__":
    main()
