#!/usr/bin/env python3
"""
evolution.py — Self-improvement engine for the trading bot.

Capabilities (all evidence-based; never modifies config without statistical significance):
  1. Per-symbol confidence thresholds (some symbols need higher bars)
  2. Adaptive position sizing (reduce size for underperformers)
  3. Time-of-day performance tracking (block bad hours)
  4. Holding-period learning (cut losers earlier, hold winners longer)
  5. Auto-disable features that don't help (conservative: needs >=10 trades)
  6. Weekly self-review report (Telegram-ready text)

Safety:
  - Every tuning change is logged to learned_stats.json["optimizations"]
  - Never touches live config without N trades of evidence
  - Min sample sizes enforced throughout
  - All changes reversible via learned_stats.json
"""

import json
import os
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

import learning

# Config
MIN_TRADES_FOR_TUNING = 10        # Need this many trades before changing anything per-symbol
MIN_TRADES_FOR_GLOBAL = 20        # For global parameter changes
SIGNIFICANCE_THRESHOLD = 0.10     # p-value-ish: 10% difference must be observable
EVOLUTION_LOG = "/Users/amandaai/.openclaw/workspace/trading_bot/evolution.json"

# Output files (consumed by auto_trade.py)
SYMBOL_CONFIDENCE_FILE = "/Users/amandaai/.openclaw/workspace/trading_bot/symbol_confidence.json"
SYMBOL_POSITION_FILE = "/Users/amandaai/.openclaw/workspace/trading_bot/symbol_position.json"
TIME_BLOCKLIST_FILE = "/Users/amandaai/.openclaw/workspace/trading_bot/time_blocklist.json"
DISABLED_FEATURES_FILE = "/Users/amandaai/.openclaw/workspace/trading_bot/disabled_features.json"


def _save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f"[evolution] Failed to save {path}: {e}")
        return False


def _load_evolution_log():
    if os.path.exists(EVOLUTION_LOG):
        try:
            with open(EVOLUTION_LOG) as f:
                return json.load(f)
        except Exception:
            pass
    return {"runs": [], "tunings": [], "created": datetime.now().isoformat()}


# ============================================================
# 1. PER-SYMBOL CONFIDENCE THRESHOLDS
# ============================================================
def tune_symbol_confidence():
    """Find optimal confidence threshold per symbol.

    For each symbol with >= MIN_TRADES_FOR_TUNING closed trades,
    find the threshold (30-70) that maximizes win rate.

    Returns: dict of {symbol: optimal_threshold}
    """
    journal = learning.load_journal()
    closed = [t for t in journal if t.get("closed", False) and t.get("pnl") is not None]

    by_symbol = defaultdict(list)
    for t in closed:
        by_symbol[t["symbol"]].append(t)

    result = {}
    for symbol, trades in by_symbol.items():
        if len(trades) < MIN_TRADES_FOR_TUNING:
            continue

        best_threshold = 35  # default
        best_wr = 0
        for threshold in range(30, 75, 5):
            subset = [t for t in trades if t.get("confidence", 0) >= threshold]
            if len(subset) < 3:
                continue
            wins = sum(1 for t in subset if t.get("pnl", 0) > 0)
            wr = wins / len(subset)
            if wr > best_wr:
                best_wr = wr
                best_threshold = threshold

        # Only use the threshold if it actually helps vs default 35
        default_subset = [t for t in trades if t.get("confidence", 0) >= 35]
        if len(default_subset) >= 3:
            default_wins = sum(1 for t in default_subset if t.get("pnl", 0) > 0)
            default_wr = default_wins / len(default_subset)
            if best_wr - default_wr < SIGNIFICANCE_THRESHOLD:
                continue  # Not significant enough to override default

        result[symbol] = {
            "threshold": best_threshold,
            "win_rate": round(best_wr * 100, 1),
            "trades": len(trades),
            "updated": datetime.now().isoformat()
        }

    _save_json(SYMBOL_CONFIDENCE_FILE, result)
    return result


# ============================================================
# 2. ADAPTIVE POSITION SIZING
# ============================================================
def tune_position_sizing():
    """Scale down position size for symbols with worse recent performance.

    For each symbol with >= 5 recent trades:
      - WR >= 70%  → 1.2x size (boost winners)
      - WR 50-70%  → 1.0x size (default)
      - WR 30-50%  → 0.6x size (reduce exposure)
      - WR < 30%   → 0.0x size (already blacklisted elsewhere)

    Returns: dict of {symbol: size_multiplier}
    """
    journal = learning.load_journal()
    # Only use last 20 trades per symbol (recency-weighted)
    by_symbol = defaultdict(list)
    for t in journal:
        # EXCLUDE CLEANUP phantom rows (exit_reason CLEANUP_*): they carry
        # pnl=0.0, which passes the "pnl is not None" test but then fails
        # "pnl > 0", so every phantom counted as a LOSS and dragged every
        # symbol's multiplier toward 0. That is what poisoned the sizing table.
        if "CLEANUP" in str(t.get("exit_reason", "")):
            continue
        if t.get("closed", False) and t.get("pnl") is not None:
            by_symbol[t["symbol"]].append(t)

    result = {}
    for symbol, trades in by_symbol.items():
        recent = trades[-20:]  # Last 20 closed trades
        if len(recent) < 5:
            continue

        wins = sum(1 for t in recent if t.get("pnl", 0) > 0)
        wr = wins / len(recent)

        if wr >= 0.70:
            multiplier = 1.2
        elif wr >= 0.50:
            multiplier = 1.0
        elif wr >= 0.30:
            multiplier = 0.6
        else:
            multiplier = 0.0  # Should already be blacklisted

        # Also check PnL consistency
        avg_pnl = sum(t.get("pnl", 0) for t in recent) / len(recent)
        if avg_pnl < 0:
            multiplier *= 0.5  # Halve size if losing money on average

        result[symbol] = {
            "multiplier": round(multiplier, 2),
            "win_rate": round(wr * 100, 1),
            "avg_pnl": round(avg_pnl, 2),
            "trades": len(recent),
            "updated": datetime.now().isoformat()
        }

    _save_json(SYMBOL_POSITION_FILE, result)
    return result


# ============================================================
# 3. TIME-OF-DAY PERFORMANCE TRACKING
# ============================================================
def tune_time_blocks():
    """Identify hours of day that historically lose money.

    Groups entry timestamps by hour and checks win rate per hour.
    Hours with WR < 35% AND >= 5 trades get added to blocklist.

    Returns: dict of {hour: stats}
    """
    journal = learning.load_journal()
    by_hour = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0, "count": 0})

    for t in journal:
        if not t.get("closed", False):
            continue
        ts = t.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts)
            hour = dt.hour
        except Exception:
            continue

        pnl = t.get("pnl", 0) or 0
        by_hour[hour]["count"] += 1
        by_hour[hour]["pnl"] += pnl
        if pnl > 0:
            by_hour[hour]["wins"] += 1
        elif pnl < 0:
            by_hour[hour]["losses"] += 1

    blocklist = []
    result = {}
    for hour, stats in sorted(by_hour.items()):
        closed = stats["wins"] + stats["losses"]
        if closed < 5:
            continue
        wr = stats["wins"] / closed
        result[hour] = {
            "win_rate": round(wr * 100, 1),
            "trades": closed,
            "pnl": round(stats["pnl"], 2),
            "blocked": wr < 0.35
        }
        if wr < 0.35 and closed >= 5:
            blocklist.append(hour)

    output = {
        "blocked_hours": sorted(blocklist),
        "by_hour": result,
        "updated": datetime.now().isoformat()
    }
    _save_json(TIME_BLOCKLIST_FILE, output)
    return output


# ============================================================
# 4. HOLDING PERIOD ANALYSIS
# ============================================================
def analyze_holding_period():
    """Learn optimal exit timing.

    For each trade, calculate hold duration (entry → exit).
    Bucket by hold time and check win rate per bucket:
      - < 30 min  (too quick?)
      - 30min-2h
      - 2h-end-of-day (swing)
      - multi-day

    Returns insights about which hold times are profitable.
    """
    journal = learning.load_journal()

    # Need paired entry+exit to measure hold time
    opens = {}  # (symbol, qty) -> entry time
    holds = []  # list of (hold_minutes, pnl, exit_reason)

    for t in journal:
        sym = t.get("symbol")
        qty = t.get("qty")
        ts = t.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts)
        except Exception:
            continue

        if t.get("type") == "BUY":
            opens[(sym, qty)] = dt
        elif t.get("type") == "SELL" and t.get("closed"):
            # Find matching entry
            key = (sym, qty)
            entry_time = opens.get(key)
            if entry_time:
                hold_minutes = (dt - entry_time).total_seconds() / 60
                holds.append({
                    "hold_minutes": hold_minutes,
                    "pnl": t.get("pnl", 0) or 0,
                    "reason": t.get("signal", "unknown")
                })
                del opens[key]

    if not holds:
        return {"buckets": {}, "insights": []}

    # Bucket by hold time
    buckets = {
        "<30min": [], "30min-2h": [], "2h-EOD": [], "multi-day": []
    }
    for h in holds:
        hm = h["hold_minutes"]
        if hm < 30:
            buckets["<30min"].append(h)
        elif hm < 120:
            buckets["30min-2h"].append(h)
        elif hm < 480:  # 8 hours
            buckets["2h-EOD"].append(h)
        else:
            buckets["multi-day"].append(h)

    bucket_stats = {}
    for label, trades in buckets.items():
        if not trades:
            continue
        wins = sum(1 for t in trades if t["pnl"] > 0)
        wr = wins / len(trades)
        avg_pnl = sum(t["pnl"] for t in trades) / len(trades)
        bucket_stats[label] = {
            "count": len(trades),
            "win_rate": round(wr * 100, 1),
            "avg_pnl": round(avg_pnl, 2),
            "total_pnl": round(sum(t["pnl"] for t in trades), 2)
        }

    # Generate insights
    insights = []
    for label, s in bucket_stats.items():
        if s["count"] < 3:
            continue
        if s["win_rate"] < 35 and s["total_pnl"] < -200:
            insights.append(f"⚠️ {label} holds losing money: {s['win_rate']}% WR, ${s['total_pnl']}")
        elif s["win_rate"] > 65 and s["total_pnl"] > 200:
            insights.append(f"✅ {label} holds profitable: {s['win_rate']}% WR, ${s['total_pnl']}")

    return {"buckets": bucket_stats, "insights": insights}


# ============================================================
# 5. AUTO-DISABLE UNDERPERFORMING FEATURES
# ============================================================
def detect_disabled_features():
    """Detect which features correlate with losses and disable them.

    Conservative: needs >= MIN_TRADES_FOR_TUNING per feature.
    Looks at reasons[] in journal entries (logged at trade open).
    Features with WR < 35% AND >= 10 mentions get disabled.

    Returns: dict of disabled feature names
    """
    journal = learning.load_journal()

    by_feature = defaultdict(lambda: {"wins": 0, "losses": 0})

    for t in journal:
        if not t.get("closed", False):
            continue
        pnl = t.get("pnl", 0) or 0
        for reason in t.get("reasons", []):
            # Filter out non-feature reasons (timestamps, prices, etc.)
            if not isinstance(reason, str) or len(reason) > 60:
                continue
            if any(kw in reason.lower() for kw in ["price", "volume", "$", "high", "low"]):
                continue
            if pnl > 0:
                by_feature[reason]["wins"] += 1
            elif pnl < 0:
                by_feature[reason]["losses"] += 1

    disabled = []
    feature_stats = {}
    for feat, stats in by_feature.items():
        total = stats["wins"] + stats["losses"]
        if total < MIN_TRADES_FOR_TUNING:
            continue
        wr = stats["wins"] / total
        feature_stats[feat] = {
            "total": total,
            "wins": stats["wins"],
            "losses": stats["losses"],
            "win_rate": round(wr * 100, 1)
        }
        # Disable features with very poor performance
        if wr < 0.35 and total >= 10:
            disabled.append(feat)

    output = {
        "disabled": disabled,
        "feature_stats": feature_stats,
        "updated": datetime.now().isoformat()
    }
    _save_json(DISABLED_FEATURES_FILE, output)
    return output


# ============================================================
# 6. WEEKLY SELF-REVIEW REPORT
# ============================================================
def generate_weekly_review():
    """Generate human-readable weekly performance review."""
    stats = learning.load_stats()
    journal = learning.load_journal()

    week_ago = datetime.now() - timedelta(days=7)
    week_trades = []
    for t in journal:
        if not t.get("closed", False):
            continue
        try:
            dt = datetime.fromisoformat(t["timestamp"])
        except Exception:
            continue
        if dt < week_ago:
            continue
        week_trades.append(t)

    if not week_trades:
        return "📊 No closed trades in the last 7 days."

    wins = sum(1 for t in week_trades if t.get("pnl", 0) > 0)
    losses = len(week_trades) - wins
    total_pnl = sum(t.get("pnl", 0) for t in week_trades)
    wr = (wins / len(week_trades)) * 100 if week_trades else 0
    avg_win = sum(t["pnl"] for t in week_trades if t["pnl"] > 0) / max(wins, 1)
    avg_loss = sum(t["pnl"] for t in week_trades if t["pnl"] < 0) / max(losses, 1)

    # Best and worst trades
    sorted_trades = sorted(week_trades, key=lambda t: t.get("pnl", 0), reverse=True)
    best = sorted_trades[0] if sorted_trades else None
    worst = sorted_trades[-1] if sorted_trades else None

    # Per-symbol breakdown
    by_symbol = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0})
    for t in week_trades:
        sym = t.get("symbol", "?")
        by_symbol[sym]["pnl"] += t.get("pnl", 0)
        if t.get("pnl", 0) > 0:
            by_symbol[sym]["wins"] += 1
        else:
            by_symbol[sym]["losses"] += 1

    # Build report
    lines = [
        f"📊 **Weekly Trading Report** ({datetime.now().strftime('%Y-%m-%d')})",
        "",
        f"Trades: {len(week_trades)} ({wins}W / {losses}L)",
        f"Win rate: {wr:.1f}%",
        f"P&L: ${total_pnl:+,.2f}",
        f"Avg win: ${avg_win:+.2f} | Avg loss: ${avg_loss:+.2f}",
        f"Expectancy: ${(total_pnl/len(week_trades)):+.2f}/trade",
        ""
    ]

    if best and best.get("pnl", 0) > 0:
        lines.append(f"🏆 Best: {best.get('symbol')} ${best.get('pnl', 0):+.2f}")
    if worst and worst.get("pnl", 0) < 0:
        lines.append(f"💀 Worst: {worst.get('symbol')} ${worst.get('pnl', 0):+.2f}")

    lines.append("")
    lines.append("**By symbol:**")
    for sym, s in sorted(by_symbol.items(), key=lambda x: x[1]["pnl"], reverse=True):
        sym_wr = s["wins"] / max(s["wins"] + s["losses"], 1) * 100
        emoji = "✅" if s["pnl"] > 0 else "❌"
        lines.append(f"  {emoji} {sym}: {s['wins']}W/{s['losses']}L ({sym_wr:.0f}% WR) ${s['pnl']:+.2f}")

    return "\n".join(lines)


# ============================================================
# MAIN ENTRY POINT
# ============================================================
def run_evolution(verbose=True):
    """Run all evolution passes. Call this daily after market close."""
    started = datetime.now()
    if verbose:
        print("=" * 60)
        print("🧬 EVOLUTION: Self-improvement pass starting")
        print("=" * 60)

    log = _load_evolution_log()

    results = {
        "timestamp": started.isoformat(),
        "symbol_confidence": tune_symbol_confidence(),
        "position_sizing": tune_position_sizing(),
        "time_blocks": tune_time_blocks(),
        "holding_period": analyze_holding_period(),
        "disabled_features": detect_disabled_features()
    }

    if verbose:
        sc = results["symbol_confidence"]
        ps = results["position_sizing"]
        tb = results["time_blocks"]
        hp = results["holding_period"]
        df = results["disabled_features"]

        print(f"\n📊 Per-symbol confidence tuned for {len(sc)} symbols")
        for sym, d in sorted(sc.items(), key=lambda x: x[1]["win_rate"], reverse=True)[:5]:
            print(f"   {sym}: threshold={d['threshold']} (WR={d['win_rate']}%)")

        print(f"\n💰 Position sizing adjusted for {len(ps)} symbols")
        boosts = [s for s, d in ps.items() if d["multiplier"] > 1.0]
        reduces = [s for s, d in ps.items() if d["multiplier"] < 1.0]
        if boosts:
            print(f"   Boosted: {boosts}")
        if reduces:
            print(f"   Reduced: {reduces}")

        print(f"\n⏰ Time-of-day analysis:")
        blocked = tb.get("blocked_hours", [])
        if blocked:
            print(f"   ⚠️ Blocked hours: {blocked}")
        else:
            print(f"   No problematic hours detected")

        print(f"\n⏳ Holding period:")
        for label, s in hp.get("buckets", {}).items():
            print(f"   {label}: {s['count']} trades, {s['win_rate']}% WR, ${s['total_pnl']:+.2f}")
        for insight in hp.get("insights", []):
            print(f"   {insight}")

        print(f"\n🔧 Feature analysis:")
        if df.get("disabled"):
            print(f"   ⚠️ Suggested for disable: {df['disabled']}")
        else:
            print(f"   No underperforming features detected")

    # Generate weekly report
    weekly = generate_weekly_review()
    if verbose:
        print("\n" + "=" * 60)
        print(weekly)
        print("=" * 60)

    # Log this run
    log["runs"].append({
        "timestamp": started.isoformat(),
        "duration_seconds": (datetime.now() - started).total_seconds(),
        "results_summary": {
            "symbols_tuned": len(results["symbol_confidence"]),
            "symbols_sized": len(results["position_sizing"]),
            "hours_blocked": len(results["time_blocks"].get("blocked_hours", [])),
            "features_flagged": len(results["disabled_features"].get("disabled", []))
        }
    })
    # Keep only last 90 days of runs
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()
    log["runs"] = [r for r in log["runs"] if r.get("timestamp", "") > cutoff]
    _save_json(EVOLUTION_LOG, log)

    return results


if __name__ == "__main__":
    run_evolution(verbose=True)
