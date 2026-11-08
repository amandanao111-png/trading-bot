#!/usr/bin/env python3
"""
Winners Pattern Mining — Trading Bot Edge Analysis
Analyzes closed trades in trade_journal.json to find what distinguishes winners
from losers. Output: actionable filter candidates for auto_trade.py.

Run:  python3 winners_patterns.py
"""
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

JOURNAL = Path("/Users/amandaai/.openclaw/workspace/trading_bot/trade_journal.json")
REPORT = Path("/Users/amandaai/.openclaw/workspace/trading_bot/mining/report.md")


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def conf_bucket(c):
    if c is None:
        return "unk"
    if c < 30:
        return "<30"
    if c < 50:
        return "30-50"
    if c < 70:
        return "50-70"
    return "70+"


def stats(group):
    if not group:
        return None
    wins = [t for t in group if t["pnl"] > 0]
    losses = [t for t in group if t["pnl"] < 0]
    n = len(wins) + len(losses)
    if n == 0:
        return None
    pnl = sum(t["pnl"] for t in group)
    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "wr": len(wins) / n * 100,
        "pnl": pnl,
        "avg": pnl / n,
        "avg_win": sum(t["pnl"] for t in wins) / len(wins) if wins else 0,
        "avg_loss": sum(t["pnl"] for t in losses) / len(losses) if losses else 0,
    }


def fmt_row(name, s, extra=""):
    if not s:
        return f"{name:<22} (no data)"
    return (
        f"{name:<22} {s['n']:>4}  {s['wr']:>5.1f}%  "
        f"${s['pnl']:>+10,.0f}  ${s['avg']:>+8,.0f}  "
        f"win=${s['avg_win']:>+7,.0f}  loss=${s['avg_loss']:>+8,.0f} {extra}"
    )


def group_by(closed, key_fn, min_n=3):
    g = defaultdict(list)
    for t in closed:
        k = key_fn(t)
        if k is not None:
            g[k].append(t)
    rows = []
    for k, v in g.items():
        s = stats(v)
        if s and s["n"] >= min_n:
            rows.append((k, s))
    return rows


def main():
    trades = json.loads(JOURNAL.read_text())
    closed = [t for t in trades if t.get("pnl") is not None]
    print(f"Loaded {len(trades)} trades · {len(closed)} with P&L\n")

    lines = []
    lines.append("# Winners Pattern Mining — Trading Bot Edge Analysis")
    lines.append(f"\n_Generated: {datetime.now().isoformat(timespec='seconds')}_")
    lines.append(f"\nSource: `trade_journal.json` · {len(closed)} closed trades")

    # === OVERALL ===
    overall = stats(closed)
    lines.append("\n## Overall")
    lines.append("")
    lines.append(f"- **Win rate:** {overall['wins']}/{overall['n']} = **{overall['wr']:.1f}%**")
    lines.append(f"- **Realized P&L:** ${overall['pnl']:,.2f}")
    lines.append(f"- **Avg trade:** ${overall['avg']:,.2f}")
    lines.append(f"- **Avg win:** ${overall['avg_win']:,.2f} · Avg loss: ${overall['avg_loss']:,.2f}")
    if overall["avg_loss"] != 0:
        pf = abs(overall["avg_win"] / overall["avg_loss"]) if overall["avg_loss"] else 0
        lines.append(f"- **Profit factor (avg win / |avg loss|):** {pf:.2f}")

    # === BY SIGNAL ===
    lines.append("\n## By Signal")
    lines.append("")
    lines.append("| Signal | N | WR | P&L | Avg | Avg Win | Avg Loss |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for k, s in sorted(group_by(closed, lambda t: t.get("signal")),
                       key=lambda x: -x[1]["wr"]):
        lines.append(
            f"| `{k or 'None'}` | {s['n']} | {s['wr']:.1f}% | "
            f"${s['pnl']:+,.0f} | ${s['avg']:+,.0f} | "
            f"${s['avg_win']:+,.0f} | ${s['avg_loss']:+,.0f} |"
        )

    # === BY SYMBOL ===
    lines.append("\n## By Symbol")
    lines.append("")
    lines.append("| Symbol | N | WR | P&L | Avg | Avg Win | Avg Loss |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for k, s in sorted(group_by(closed, lambda t: t.get("symbol")),
                       key=lambda x: -x[1]["pnl"]):
        lines.append(
            f"| `{k or '?'}` | {s['n']} | {s['wr']:.1f}% | "
            f"${s['pnl']:+,.0f} | ${s['avg']:+,.0f} | "
            f"${s['avg_win']:+,.0f} | ${s['avg_loss']:+,.0f} |"
        )

    # === BY SIDE ===
    lines.append("\n## By Side (type)")
    lines.append("")
    lines.append("| Side | N | WR | P&L | Avg |")
    lines.append("|---|---:|---:|---:|---:|")
    for k, s in sorted(group_by(closed, lambda t: t.get("type")),
                       key=lambda x: -x[1]["pnl"]):
        lines.append(
            f"| `{k or '?'}` | {s['n']} | {s['wr']:.1f}% | "
            f"${s['pnl']:+,.0f} | ${s['avg']:+,.0f} |"
        )

    # === BY HOUR OF DAY ===
    lines.append("\n## By Hour of Day (timestamp local)")
    lines.append("")
    lines.append("| Hour | N | WR | P&L | Avg |")
    lines.append("|---:|---:|---:|---:|---:|")
    rows = group_by(closed, lambda t: parse_ts(t.get("timestamp")).hour
                    if parse_ts(t.get("timestamp")) else None)
    for k, s in sorted(rows, key=lambda x: int(x[0]) if x[0] is not None else -1):
        lines.append(
            f"| {k} | {s['n']} | {s['wr']:.1f}% | "
            f"${s['pnl']:+,.0f} | ${s['avg']:+,.0f} |"
        )

    # === BY DAY OF WEEK ===
    lines.append("\n## By Day of Week")
    lines.append("")
    lines.append("| Day | N | WR | P&L | Avg |")
    lines.append("|---|---:|---:|---:|---:|")
    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    rows = group_by(closed, lambda t: parse_ts(t.get("timestamp")).strftime("%A")
                    if parse_ts(t.get("timestamp")) else None)
    by_day = {k: s for k, s in rows}
    for d in dow_order:
        if d in by_day:
            s = by_day[d]
            lines.append(
                f"| {d} | {s['n']} | {s['wr']:.1f}% | "
                f"${s['pnl']:+,.0f} | ${s['avg']:+,.0f} |"
            )

    # === BY CONFIDENCE BUCKET ===
    lines.append("\n## By Confidence Bucket")
    lines.append("")
    lines.append("| Bucket | N | WR | P&L | Avg |")
    lines.append("|---|---:|---:|---:|---:|")
    bucket_order = ["<30", "30-50", "50-70", "70+", "unk"]
    rows = group_by(closed, lambda t: conf_bucket(t.get("confidence")))
    by_b = {k: s for k, s in rows}
    for b in bucket_order:
        if b in by_b:
            s = by_b[b]
            lines.append(
                f"| {b} | {s['n']} | {s['wr']:.1f}% | "
                f"${s['pnl']:+,.0f} | ${s['avg']:+,.0f} |"
            )

    # === CROSS-TABS ===
    lines.append("\n## Signal × Symbol Combinations (≥5 trades)")
    lines.append("")
    lines.append("| Signal | Symbol | N | WR | P&L |")
    lines.append("|---|---|---:|---:|---:|")
    combos = defaultdict(list)
    for t in closed:
        k = (t.get("signal"), t.get("symbol"))
        combos[k].append(t)
    combo_rows = []
    for (sig, sym), v in combos.items():
        s = stats(v)
        if s and s["n"] >= 5:
            combo_rows.append((sig or "None", sym or "?", s))
    # Top 10 by WR
    lines.append("\n### Top 10 by Win Rate")
    lines.append("")
    lines.append("| Signal | Symbol | N | WR | P&L |")
    lines.append("|---|---|---:|---:|---:|")
    for sig, sym, s in sorted(combo_rows, key=lambda x: -x[2]["wr"])[:10]:
        lines.append(
            f"| `{sig}` | `{sym}` | {s['n']} | {s['wr']:.1f}% | ${s['pnl']:+,.0f} |"
        )
    # Bottom 10 by WR
    lines.append("\n### Bottom 10 by Win Rate")
    lines.append("")
    lines.append("| Signal | Symbol | N | WR | P&L |")
    lines.append("|---|---|---:|---:|---:|")
    for sig, sym, s in sorted(combo_rows, key=lambda x: x[2]["wr"])[:10]:
        lines.append(
            f"| `{sig}` | `{sym}` | {s['n']} | {s['wr']:.1f}% | ${s['pnl']:+,.0f} |"
        )

    # === ACTIONABLE PATTERNS ===
    lines.append("\n## Actionable Filter Candidates")
    lines.append("")
    candidates = []
    # High WR signal-symbol combos (≥60% WR, ≥5 trades, profitable)
    for sig, sym, s in combo_rows:
        if s["wr"] >= 60 and s["n"] >= 5 and s["pnl"] > 0:
            candidates.append(
                f"- ✅ **Concentrate** `{sig}` on `{sym}` "
                f"({s['n']} trades, {s['wr']:.1f}% WR, ${s['pnl']:+,.0f})"
            )
    # Low WR combos (avoid)
    lines.append("### ✅ High-WR Combos (concentrate here)")
    for c in candidates:
        lines.append(c)
    if not candidates:
        lines.append("- _(none with WR≥60% and ≥5 trades — win rate is broadly mediocre)_")

    lines.append("\n### ❌ Low-WR Combos (avoid these)")
    lines.append("")
    avoid = [c for c in combo_rows if c[2]["wr"] < 40 and c[2]["n"] >= 5]
    for sig, sym, s in sorted(avoid, key=lambda x: x[2]["wr"])[:10]:
        lines.append(
            f"- ❌ **Avoid** `{sig}` on `{sym}` "
            f"({s['n']} trades, {s['wr']:.1f}% WR, ${s['pnl']:+,.0f})"
        )

    # Hour-of-day blacklist suggestion
    bad_hours = [k for k, s in rows if isinstance(k, int) and s["pnl"] < 0 and s["n"] >= 5]
    # Actually we need raw hour rows
    hour_rows = group_by(closed, lambda t: parse_ts(t.get("timestamp")).hour
                         if parse_ts(t.get("timestamp")) else None)
    bad_hours = [k for k, s in hour_rows if s["pnl"] < 0 and s["n"] >= 5]
    if bad_hours:
        lines.append("")
        lines.append(f"### ⏰ Hour-of-day blacklist candidates (negative P&L, ≥5 trades)")
        lines.append(f"- Hours to block: {sorted(bad_hours)}")

    # Day-of-week blacklist
    dow_rows = group_by(closed, lambda t: parse_ts(t.get("timestamp")).strftime("%A")
                        if parse_ts(t.get("timestamp")) else None)
    bad_days = [k for k, s in dow_rows if s["pnl"] < 0 and s["n"] >= 5]
    if bad_days:
        lines.append(f"### 📅 Day-of-week blacklist candidates (negative P&L, ≥5 trades)")
        lines.append(f"- Days to block: {bad_days}")

    # Confidence threshold
    conf_rows = group_by(closed, lambda t: conf_bucket(t.get("confidence")))
    high_conf = [s for k, s in conf_rows if k in ("70+", "50-70")]
    low_conf = [s for k, s in conf_rows if k in ("<30", "30-50")]
    if high_conf and low_conf:
        hc_wr = sum(s["wins"] for s in high_conf) / sum(s["n"] for s in high_conf) * 100
        lc_wr = sum(s["wins"] for s in low_conf) / sum(s["n"] for s in low_conf) * 100
        lines.append("")
        lines.append(f"### 🎯 Confidence threshold insight")
        lines.append(f"- High-confidence (50+): {hc_wr:.1f}% WR")
        lines.append(f"- Low-confidence (<50): {lc_wr:.1f}% WR")

    REPORT.write_text("\n".join(lines))
    print(f"\n✅ Report written to: {REPORT}")
    print(f"   ({len(lines)} lines, {REPORT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()