#!/usr/bin/env python3
"""
Trading bot performance metrics — run on existing trade journal
to validate whether the strategy has actual edge.

Usage:
    source venv/bin/activate
    python3 metrics.py

Validates against BOTH trade_journal.json (live) and trade_journal_v6.json
(stale backup) so we can compare regimes.
"""

import json
import math
import os
from collections import defaultdict

WORKSPACE = "/Users/amandaai/.openclaw/workspace/trading_bot"


def load_journal(name="trade_journal.json"):
    path = os.path.join(WORKSPACE, name)
    if not os.path.exists(path):
        return [], path
    with open(path) as f:
        return json.load(f), path


def normalize_trades(journal):
    """Normalize v6 schema (side/entry_price/exit_price) and fresh schema
    (type/price/signal/closed) into a unified list of (pnl, reason, confidence).
    """
    out = []
    for t in journal:
        pnl = t.get("pnl")
        if pnl is None:
            continue
        # Schema detection
        if "side" in t:  # v6 schema
            if not t.get("closed"):
                continue
            reason = t.get("exit_reason", "unknown")
            conf = t.get("confidence", 0)
        else:  # fresh schema
            if not t.get("closed"):
                continue
            reason = t.get("signal", "unknown")
            conf = t.get("confidence", 0)
        out.append({"pnl": float(pnl), "reason": reason, "confidence": conf})
    return out


def compute_metrics(trades, label):
    if not trades:
        print(f"\n=== {label}: NO CLOSED TRADES ===")
        return {}

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    n = len(pnls)
    n_w = len(wins)
    n_l = len(losses)
    wr = n_w / n * 100 if n else 0
    avg_win = sum(wins) / n_w if n_w else 0
    avg_loss = sum(losses) / n_l if n_l else 0
    expectancy = (sum(wins) - sum(losses)) / n if n else 0
    profit_factor = sum(wins) / sum(losses) if sum(losses) > 0 else float("inf")
    total_pnl = sum(pnls)
    max_dd = min(pnls)  # conservative: worst single trade as proxy
    # Proper max drawdown
    cum = 0
    peak = 0
    max_dd_proper = 0
    for p in sorted(pnls):  # approximation by sort
        cum += p
        peak = max(peak, cum)
        dd = peak - cum
        max_dd_proper = max(max_dd_proper, dd)

    # Sharpe-like (per-trade, not annualized — multiply by sqrt(252/trades_per_year) for annual)
    if n > 1:
        mean = sum(pnls) / n
        var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
        std = math.sqrt(var) if var > 0 else 0
        sharpe_per_trade = mean / std if std > 0 else 0
    else:
        sharpe_per_trade = 0
        std = 0

    # By reason
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t["reason"]].append(t["pnl"])
    reason_stats = {}
    for r, ps in by_reason.items():
        rw = [p for p in ps if p > 0]
        rl = [-p for p in ps if p < 0]
        reason_stats[r] = {
            "count": len(ps),
            "wins": len(rw),
            "losses": len(rl),
            "wr": len(rw) / len(ps) * 100 if ps else 0,
            "pnl": sum(ps),
        }

    # By confidence bucket
    by_conf = defaultdict(list)
    for t in trades:
        bkt = (int(t["confidence"]) // 10) * 10
        by_conf[bkt].append(t["pnl"])
    conf_stats = {}
    for b, ps in sorted(by_conf.items()):
        rw = [p for p in ps if p > 0]
        rl = [-p for p in ps if p < 0]
        conf_stats[b] = {
            "count": len(ps),
            "wr": len(rw) / len(ps) * 100 if ps else 0,
            "pnl": sum(ps),
            "expectancy": (sum(rw) - sum(rl)) / len(ps) if ps else 0,
        }

    print(f"\n=== {label} ===")
    print(f"  Closed trades:        {n}")
    print(f"  Wins / Losses:        {n_w} / {n_l}")
    print(f"  Win rate:             {wr:.1f}%")
    print(f"  Avg win:              ${avg_win:>8.2f}")
    print(f"  Avg loss:             ${avg_loss:>8.2f}")
    print(f"  Total P&L:            ${total_pnl:>10.2f}")
    print(f"  Expectancy/trade:     ${expectancy:>8.2f}")
    print(f"  Profit factor:        {profit_factor:.2f}")
    print(f"  Per-trade std:        ${std:>8.2f}")
    print(f"  Sharpe (per-trade):   {sharpe_per_trade:.3f}")
    print(f"  Max single loss:      ${min(pnls):>8.2f}")
    print(f"  Max DD (approx):      ${max_dd_proper:>8.2f}")

    print(f"\n  By exit reason:")
    for r, s in sorted(reason_stats.items(), key=lambda x: -x[1]["pnl"]):
        print(f"    {r:25s}: n={s['count']:>3}  {s['wins']}W/{s['losses']}L  WR={s['wr']:>5.1f}%  P&L=${s['pnl']:>+8.2f}")

    print(f"\n  By confidence bucket:")
    for b, s in sorted(conf_stats.items()):
        marker = " ← profitable?" if s["expectancy"] > 0 else ""
        print(f"    conf {b:>3}-{b+9:>3}:  n={s['count']:>3}  WR={s['wr']:>5.1f}%  exp/trade=${s['expectancy']:>+7.2f}  P&L=${s['pnl']:>+8.2f}{marker}")

    return {
        "n": n, "wr": wr, "expectancy": expectancy, "profit_factor": profit_factor,
        "sharpe": sharpe_per_trade, "total_pnl": total_pnl,
    }


def main():
    print(f"Analyzing trade journals in {WORKSPACE}")
    print("=" * 70)

    # Fresh journal (live)
    j_fresh, path_fresh = load_journal("trade_journal.json")
    t_fresh = normalize_trades(j_fresh)
    m_fresh = compute_metrics(t_fresh, f"FRESH JOURNAL (live) — {path_fresh}")

    # v6 journal (stale backup)
    j_v6, path_v6 = load_journal("trade_journal_v6.json")
    t_v6 = normalize_trades(j_v6)
    m_v6 = compute_metrics(t_v6, f"V6 JOURNAL (stale) — {path_v6}")

    # Verdict
    print(f"\n{'='*70}")
    print("VERDICT")
    print(f"{'='*70}")

    for label, m in [("Fresh", m_fresh), ("V6 (stale)", m_v6)]:
        if not m:
            print(f"  {label}: no data")
            continue
        wr = m["wr"]
        exp = m["expectancy"]
        pf = m["profit_factor"]
        sharpe = m["sharpe"]
        has_edge = (
            exp > 0 and pf > 1.0 and sharpe > 0.05 and wr > 50
        )
        verdict = "✅ HAS EDGE" if has_edge else "❌ NO EDGE"
        print(f"  {label:12s}: WR={wr:>5.1f}%  exp=${exp:>+7.2f}/trade  PF={pf:.2f}  Sharpe={sharpe:.3f}  → {verdict}")

    print(f"\nKey thresholds for real money:")
    print(f"  Expectancy > $0/trade (mandatory)")
    print(f"  Profit factor > 1.5 (recommended)")
    print(f"  Sharpe > 0.10/trade (≈ Sharpe > 1.5 annualized at 1 trade/day)")
    print(f"  Win rate > 50% AND avg_win > avg_loss")


if __name__ == "__main__":
    main()
