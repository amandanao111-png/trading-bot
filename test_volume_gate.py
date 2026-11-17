#!/usr/bin/env python3
"""
test_volume_gate.py — offline unit tests for the new volume gate.

No network: every test builds synthetic sessions and calls the pure math in
volume_gate.compute_gate(). Run with:

    venv/bin/python test_volume_gate.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import volume_gate as vg


def configured_min_ratio(default=1.0):
    """Read MIN_VOLUME_RATIO out of auto_trade.py so the tests judge the same
    threshold the live bot uses."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto_trade.py")
    try:
        with open(path) as fh:
            m = re.search(r"^MIN_VOLUME_RATIO\s*=\s*([0-9.]+)", fh.read(), re.M)
        return float(m.group(1)) if m else default
    except Exception:
        return default


MIN_RATIO = configured_min_ratio()

FAILURES = []


def check(name, got, expected, tol=None):
    ok = (abs(got - expected) <= tol) if tol is not None else (got == expected)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got} expected={expected}")
    if not ok:
        FAILURES.append(name)


def make_session(day, per_bar_volume, bars_per_hour=12, start_hour=9, end_hour=16,
                 open_burst=1.0):
    """Build a synthetic session: 5-min bars from start_hour to end_hour ET."""
    out = []
    for minute in range(start_hour * 60, end_hour * 60, vg.BAR_MINUTES):
        mult = open_burst if minute < 10 * 60 else 1.0
        out.append({
            "start_min": minute,
            "end_min": minute + vg.BAR_MINUTES,
            "v": per_bar_volume * mult,
        })
    return out


def old_gate_ratio(session, cutoff_min, window=100):
    """Emulate the legacy gate on the same session.

    The bot pulled limit=100 5-min bars ending at the decision clock and
    compared the last COMPLETED bar against the mean of the 20 before it.
    """
    bars = [b for b in session if b["end_min"] <= cutoff_min]
    if not bars:
        return None
    vols = [b["v"] for b in bars[-window:]]
    if len(vols) < 22:
        return None
    completed = vols[-22:-2]
    avg = sum(completed) / len(completed)
    if avg <= 0:
        return None
    return vols[-2] / avg


def main():
    print("=" * 74)
    print(f"volume_gate unit tests (offline, synthetic sessions)  MIN_VOLUME_RATIO={MIN_RATIO}")
    print("=" * 74)

    cutoff = 16 * 60  # 16:00 ET

    # 1. normal day -> ratio 1.0
    hist = [make_session(f"d{i}", 1000.0) for i in range(10)]
    today = make_session("today", 1000.0)
    r, recent, info = vg.compute_gate(today, hist, cutoff)
    check("normal day ratio", round(r, 4), 1.0, tol=0.001)
    check("normal day baseline", info["baseline"], "same_time_of_day")
    check("normal day sessions", info["sessions"], 10)

    # 2. dead day (half the normal volume) -> ratio 0.5 -> gate rejects
    today_dead = make_session("today", 500.0)
    r, _, _ = vg.compute_gate(today_dead, hist, cutoff)
    check("dead day ratio", round(r, 4), 0.5, tol=0.001)
    check(f"dead day fails gate ({MIN_RATIO})", r >= MIN_RATIO, False)

    # 2b. just below the configured threshold still rejects, just above passes
    r_below, _, _ = vg.compute_gate(make_session("today", 1000.0 * (MIN_RATIO - 0.05)), hist, cutoff)
    r_above, _, _ = vg.compute_gate(make_session("today", 1000.0 * (MIN_RATIO + 0.05)), hist, cutoff)
    check("just below threshold fails", r_below >= MIN_RATIO, False)
    check("just above threshold passes", r_above >= MIN_RATIO, True)

    # 3. busy day -> ratio 1.6 -> gate passes
    today_busy = make_session("today", 1600.0)
    r_busy, _, _ = vg.compute_gate(today_busy, hist, cutoff)
    check("busy day ratio", round(r_busy, 4), 1.6, tol=0.001)
    check(f"busy day passes gate ({MIN_RATIO})", r_busy >= MIN_RATIO, True)

    # 4. insufficient history -> neutral 1.0 (never 0), explicit reason
    r, _, info = vg.compute_gate(today, hist[:3], cutoff)
    check("insufficient history ratio", r, vg.NEUTRAL_RATIO)
    check("insufficient history reason", info["baseline"], "insufficient_history")

    # 5. no volume today (pre-open / feed lag) -> neutral, explicit reason
    r, _, info = vg.compute_gate([], hist, cutoff)
    check("no today volume ratio", r, vg.NEUTRAL_RATIO)
    check("no today volume reason", info["baseline"], "no_today_volume")

    # 6. the point of the fix: ONE dead 5-minute bar must not decide the
    #    verdict. The legacy gate compares that single bar against a trailing
    #    20-bar mean, so a single quiet bar collapses it; the new gate compares
    #    the session cumulative, which barely notices.
    hist_b = [make_session(f"d{i}", 1000.0) for i in range(10)]
    today_b = make_session("today", 1000.0)
    today_b[-2]["v"] = 40.0          # one dead bar just before the decision
    cutoff_late = 16 * 60
    r_new, _, _ = vg.compute_gate(today_b, hist_b, cutoff_late)
    r_old = old_gate_ratio(today_b, cutoff_late)
    print(f"      (one dead 5-min bar: old ratio={r_old:.3f}, new ratio={r_new:.3f})")
    check("new gate survives one dead bar", r_new > 0.9, True)
    check("old gate collapsed on one dead bar", r_old < 0.35, True)

    # 6b. a fat OPEN hour must not decide a 16:15 verdict either
    hist_b2 = [make_session(f"d{i}", 1000.0) for i in range(10)]
    today_b2 = make_session("today", 1000.0, open_burst=10.0)
    r_new2, _, _ = vg.compute_gate(today_b2, hist_b2, cutoff_late)
    check("fat open hour no longer collapses the late-session ratio", r_new2 > 0.9, True)

    # 6c. the MEDIAN baseline is robust to one volume-spike session
    hist_spike = [make_session(f"d{i}", 1000.0) for i in range(10)]
    hist_spike[5] = make_session("spike", 100_000.0)
    r_med, _, info = vg.compute_gate(make_session("today", 1000.0), hist_spike, cutoff)
    check("median baseline ignores a spike session", round(r_med, 3), 1.0, tol=0.001)
    check("spike visible in the reported mean", info["baseline_mean_volume"] > 9000, True)

    # 7. same clock on both sides: the 14:45 verdict uses 14:45 history
    cutoff_early = 14 * 60 + 45
    hist_c = [make_session(f"d{i}", 1000.0) for i in range(10)]
    today_c = make_session("today", 1000.0)
    r_early, _, info = vg.compute_gate(today_c, hist_c, cutoff_early)
    check("early cutoff ratio", round(r_early, 4), 1.0, tol=0.001)
    check("early cutoff label", info["cutoff_et"], "14:45")

    # 8. pre/post market volume is excluded from the comparison
    sess_pre = [{"start_min": 8 * 60, "end_min": 8 * 60 + 5, "v": 10_000_000.0}]
    sess_pre += make_session("d", 1000.0)
    hist_e = [sess_pre] * 10
    r, _, _ = vg.compute_gate(make_session("today", 1000.0), hist_e, cutoff)
    check("pre-market volume ignored", round(r, 4), 1.0, tol=0.001)

    # 9. recent-window metric tracks a live surge
    surge = make_session("today", 1000.0)
    for bar in surge:
        if bar["start_min"] >= 15 * 60 + 30:
            bar["v"] = 5000.0
    _, recent, _ = vg.compute_gate(surge, hist, cutoff)
    check("recent-window metric sees the surge", recent > 2.0, True)

    print("=" * 74)
    if FAILURES:
        print(f"FAILED {len(FAILURES)} test(s): {FAILURES}")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
