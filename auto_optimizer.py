#!/usr/bin/env python3
"""
Trading Bot Auto-Optimizer
Runs after market close, analyzes performance, auto-adjusts parameters
"""

import json
import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict

# Paths
WORK_DIR = "/Users/amandaai/.openclaw/workspace/trading_bot"
AUTO_TRADE = f"{WORK_DIR}/auto_trade.py"
JOURNAL_FILE = f"{WORK_DIR}/trade_journal.json"
V6_STATS = f"{WORK_DIR}/v6_stats.json"
AUTO_CONFIG = f"{WORK_DIR}/auto_config.json"

# Thresholds for auto-adjustment
MIN_SIGNAL_WR = 0.45       # Disable signal if WR drops below 45%
MIN_SYMBOL_WR = 0.40       # Blacklist symbol if WR drops below 40%
MIN_TRADES_FOR_STATS = 5   # Need at least 5 trades before acting
LOOKBACK_DAYS = 14         # Only look at last 14 days of trades


def load_journal():
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return []


def get_recent_trades(journal, lookback_days=14):
    cutoff = datetime.now() - timedelta(days=lookback_days)
    recent = []
    for t in journal:
        try:
            ts = t.get('timestamp', t.get('date', ''))
            if ts:
                trade_date = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                if trade_date >= cutoff:
                    recent.append(t)
        except:
            pass
    return recent


def analyze_signal_stats(trades):
    """Calculate win rate per signal from recent trades"""
    sig_stats = defaultdict(lambda: {'wins': 0, 'losses': 0, 'total': 0, 'pnl': 0})
    for t in trades:
        sig = t.get('signal', 'unknown')
        pnl = t.get('pnl', 0)
        sig_stats[sig]['total'] += 1
        sig_stats[sig]['pnl'] += pnl
        if pnl > 0:
            sig_stats[sig]['wins'] += 1
        elif pnl < 0:
            sig_stats[sig]['losses'] += 1
    return sig_stats


def analyze_symbol_stats(trades):
    """Calculate win rate per symbol from recent trades"""
    sym_stats = defaultdict(lambda: {'wins': 0, 'losses': 0, 'total': 0, 'pnl': 0})
    for t in trades:
        sym = t.get('symbol', 'unknown')
        pnl = t.get('pnl', 0)
        sym_stats[sym]['total'] += 1
        sym_stats[sym]['pnl'] += pnl
        if pnl > 0:
            sym_stats[sym]['wins'] += 1
        elif pnl < 0:
            sym_stats[sym]['losses'] += 0  # fix: should be losses not 0
            sym_stats[sym]['losses'] += 1
    return sym_stats


def load_current_config():
    """Load current whitelist/blacklist from auto_trade.py source"""
    with open(AUTO_TRADE, 'r') as f:
        source = f.read()
    
    config = {'whitelist': [], 'blacklist': []}
    
    # Extract whitelist
    import re
    wl_match = re.search(r"SYMBOL_WHITELIST\s*=\s*\[(.*?)\]", source, re.DOTALL)
    if wl_match:
        items = re.findall(r"'(\w+)'", wl_match.group(1))
        config['whitelist'] = items
    
    bl_match = re.search(r"SYMBOL_BLACKLIST\s*=\s*\[(.*?)\]", source, re.DOTALL)
    if bl_match:
        items = re.findall(r"'(\w+)'", bl_match.group(1))
        config['blacklist'] = items
    
    return config


def apply_auto_adjustments(sig_stats, sym_stats, current_config):
    """Decide what to auto-adjust based on recent performance"""
    changes = []
    
    # Check signals
    for sig, stats in sig_stats.items():
        if sig in ['RSI_EXIT', 'PARTIAL_TAKE_50%'] and stats['total'] >= MIN_TRADES_FOR_STATS:
            wr = stats['wins'] / stats['total'] if stats['total'] > 0 else 0
            if wr < MIN_SIGNAL_WR:
                changes.append(f"⚠️ SIGNAL WARNING: {sig} WR dropped to {wr*100:.0f}% ({stats['total']} trades). Monitor closely.")
    
    # Check symbols - add bad ones to blacklist
    new_blacklist = set(current_config['blacklist'])
    for sym, stats in sym_stats.items():
        if stats['total'] >= MIN_TRADES_FOR_STATS:
            wr = stats['wins'] / stats['total'] if stats['total'] > 0 else 0
            pnl = stats['pnl']
            # Auto-blacklist if WR < 40% AND negative P&L
            if wr < MIN_SYMBOL_WR and pnl < -100:
                if sym not in new_blacklist:
                    new_blacklist.add(sym)
                    changes.append(f"🚫 AUTO-BLACKLIST: {sym} (WR {wr*100:.0f}%, P&L ${pnl:.0f})")
    
    # Check symbols - if whitelist member has bad performance, warn
    for sym in current_config['whitelist']:
        if sym in sym_stats:
            stats = sym_stats[sym]
            if stats['total'] >= MIN_TRADES_FOR_STATS:
                wr = stats['wins'] / stats['total'] if stats['total'] > 0 else 0
                pnl = stats['pnl']
                if wr < 0.40 or pnl < -200:
                    changes.append(f"⚠️ WHITELIST WARNING: {sym} (WR {wr*100:.0f}%, P&L ${pnl:.0f})")
    
    return changes, list(new_blacklist)


def generate_report(trades, sig_stats, sym_stats, changes, lookback_days=14):
    """Generate performance report"""
    if not trades:
        return "📊 Auto-Optimizer: No trades in last 14 days. Nothing to analyze."
    
    total_pnl = sum(t.get('pnl', 0) for t in trades)
    wins = sum(1 for t in trades if t.get('pnl', 0) > 0)
    losses = sum(1 for t in trades if t.get('pnl', 0) < 0)
    wr = round(wins / len(trades) * 100, 1) if trades else 0
    
    # Per-signal summary
    sig_lines = []
    for sig, s in sorted(sig_stats.items(), key=lambda x: -x[1]['pnl']):
        if s['total'] == 0:
            continue
        sig_wr = round(s['wins'] / s['total'] * 100, 1) if s['total'] > 0 else 0
        sig_lines.append(f"  {sig:25s} {s['total']:3d} trades WR {sig_wr:5.1f}% P&L ${round(s['pnl'], 0):>7}")
    
    # Per-symbol top/bottom
    sym_sorted = sorted(sym_stats.items(), key=lambda x: -x[1]['pnl'])
    top_syms = sym_sorted[:3]
    bot_syms = sym_sorted[-3:]
    
    report = f"""
📊 **Trading Bot Auto-Optimizer Report**
_{datetime.now().strftime('%Y-%m-%d %H:%M')} · Last {lookback_days} days_

**Overall: {len(trades)} trades | WR {wr}% | P&L ${round(total_pnl, 2)}**
Wins: {wins} | Losses: {losses}

**Signal Performance:**
""" + "\n".join(sig_lines) + f"""

**Top Symbols:**
"""
    for sym, s in top_syms:
        report += f"  🟢 {sym}: {s['total']} trades, P&L ${round(s['pnl'], 0)}\n"
    
    report += "**Worst Symbols:**\n"
    for sym, s in bot_syms:
        report += f"  🔴 {sym}: {s['total']} trades, P&L ${round(s['pnl'], 0)}\n"
    
    if changes:
        report += "\n**⚡ Auto-Adjustments:**\n"
        for c in changes:
            report += f"{c}\n"
    else:
        report += "\n✅ **No auto-adjustments needed.**"
    
    return report


def main():
    journal = load_journal()
    recent_trades = get_recent_trades(journal, LOOKBACK_DAYS)
    current_config = load_current_config()
    
    sig_stats = analyze_signal_stats(recent_trades)
    sym_stats = analyze_symbol_stats(recent_trades)
    changes, new_blacklist = apply_auto_adjustments(sig_stats, sym_stats, current_config)
    
    report = generate_report(recent_trades, sig_stats, sym_stats, changes, LOOKBACK_DAYS)
    print(report)
    
    # Save changes to auto_config.json
    config_update = {
        'last_run': datetime.now().isoformat(),
        'trades_analyzed': len(recent_trades),
        'changes_made': len(changes),
        'changes': changes,
        'new_blacklist': new_blacklist
    }
    with open(AUTO_CONFIG, 'w') as f:
        json.dump(config_update, f, indent=2)
    
    return 0 if not changes else 1  # Exit code 1 if changes made


if __name__ == "__main__":
    sys.exit(main())