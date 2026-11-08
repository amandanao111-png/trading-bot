#!/usr/bin/env python3
"""
Trading Bot Learning Module
Self-learning and evolution system
"""

import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

JOURNAL_FILE = "/Users/amandaai/.openclaw/workspace/trading_bot/trade_journal.json"
STATS_FILE = "/Users/amandaai/.openclaw/workspace/trading_bot/learned_stats.json"


def load_journal():
    """Load trade journal"""
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return []


def save_journal(journal):
    """Save trade journal"""
    with open(JOURNAL_FILE, 'w') as f:
        json.dump(journal, f, indent=2)


def load_stats():
    """Load learned statistics"""
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_pnl": 0,
        "signal_stats": defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0}),
        "symbol_stats": defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0}),
        "optimizations": []
    }


def save_stats(stats):
    """Save learned statistics"""
    with open(STATS_FILE, 'w') as f:
        json.dump(stats, f, indent=2)


def record_trade(trade_type, symbol, qty, price, signal, confidence, reasons, pnl=0):
    """Record a trade for learning"""
    journal = load_journal()
    
    entry = {
        "timestamp": datetime.now().isoformat(),
        "type": trade_type,  # BUY or SELL
        "symbol": symbol,
        "qty": qty,
        "price": price,
        "signal": signal,
        "confidence": confidence,
        "reasons": reasons,
        "pnl": pnl,
        "closed": False
    }
    
    journal.append(entry)
    save_journal(journal)
    
    # Update stats
    stats = load_stats()
    stats["total_trades"] += 1
    
    if trade_type == "SELL":
        if pnl and pnl != 0:
            if pnl > 0:
                stats["winning_trades"] += 1
                win = True
            else:
                stats["losing_trades"] += 1
                win = False
            
            stats["total_pnl"] += pnl
            
            # Update symbol stats
            if symbol not in stats["symbol_stats"]:
                stats["symbol_stats"][symbol] = {"total": 0, "wins": 0, "losses": 0}
            stats["symbol_stats"][symbol]["total"] += 1
            if win:
                stats["symbol_stats"][symbol]["wins"] += 1
            else:
                stats["symbol_stats"][symbol]["losses"] += 1
            # === FIX: Track per-symbol P&L so auto-blacklist works ===
            stats["symbol_stats"][symbol]["pnl"] = stats["symbol_stats"][symbol].get("pnl", 0) + pnl

            # Update signal stats for each reason
            for reason in reasons:
                if reason not in stats["signal_stats"]:
                    stats["signal_stats"][reason] = {"total": 0, "wins": 0, "losses": 0}
                stats["signal_stats"][reason]["total"] += 1
                if win:
                    stats["signal_stats"][reason]["wins"] += 1
                else:
                    stats["signal_stats"][reason]["losses"] += 1
        
        # Mark journal entry as closed - find oldest open BUY for this symbol
        for j, je in enumerate(journal):
            if je.get("symbol") == symbol and je.get("type") == "BUY" and not je.get("closed", False):
                journal[j]["closed"] = True
                journal[j]["pnl"] = pnl if pnl else 0
                break  # Only close the oldest open position
        save_journal(journal)
    
    save_stats(stats)
    return stats


def analyze_performance():
    """Analyze trading performance and suggest optimizations"""
    stats = load_stats()
    journal = load_journal()
    
    if stats["total_trades"] < 5:
        return {
            "status": "learning",
            "message": "Not enough trades for analysis yet",
            "trades": stats["total_trades"]
        }
    
    # Calculate win rate
    total_closed = stats["winning_trades"] + stats["losing_trades"]
    win_rate = (stats["winning_trades"] / total_closed * 100) if total_closed > 0 else 0
    
    # Find best/worst signals
    best_signal = None
    worst_signal = None
    best_rate = 0
    worst_rate = 100
    
    for signal, data in stats["signal_stats"].items():
        if data["total"] >= 3:
            rate = data["wins"] / data["total"] * 100
            if rate > best_rate:
                best_rate = rate
                best_signal = signal
            if rate < worst_rate:
                worst_rate = rate
                worst_signal = signal
    
    # Find best/worst symbols
    best_symbol = None
    worst_symbol = None
    best_symbol_rate = 0
    worst_symbol_rate = 100
    
    for symbol, data in stats["symbol_stats"].items():
        if data["total"] >= 2:
            rate = data["wins"] / data["total"] * 100
            if rate > best_symbol_rate:
                best_symbol_rate = rate
                best_symbol = symbol
            if rate < worst_symbol_rate:
                worst_symbol_rate = rate
                worst_symbol = symbol
    
    suggestions = []
    
    # Suggest parameter adjustments
    if win_rate > 60:
        suggestions.append({
            "type": "AGGRESSIVE",
            "message": f"Win rate {win_rate:.1f}% is good! Consider increasing position size."
        })
    elif win_rate < 40:
        suggestions.append({
            "type": "CONSERVATIVE",
            "message": f"Win rate {win_rate:.1f}% is low. Consider being more selective."
        })
    
    if best_signal:
        suggestions.append({
            "type": "SIGNAL",
            "message": f"Best signal: {best_signal} ({best_rate:.1f}% win rate)"
        })
    
    if worst_signal:
        suggestions.append({
            "type": "AVOID",
            "message": f"Avoid: {worst_signal} ({worst_rate:.1f}% win rate)"
        })
    
    if best_symbol:
        suggestions.append({
            "type": "FOCUS",
            "message": f"Best performer: {best_symbol} ({best_symbol_rate:.1f}%)"
        })
    
    if worst_symbol:
        suggestions.append({
            "type": "SKIP",
            "message": f"Underperformer: {worst_symbol} ({worst_symbol_rate:.1f}%)"
        })
    
    return {
        "status": "analyzed",
        "win_rate": win_rate,
        "total_pnl": stats["total_pnl"],
        "total_trades": total_closed,
        "best_signal": best_signal,
        "worst_signal": worst_signal,
        "best_symbol": best_symbol,
        "worst_symbol": worst_symbol,
        "suggestions": suggestions
    }


def get_optimal_confidence():
    """Get optimized confidence threshold based on historical performance"""
    stats = load_stats()
    journal = load_journal()
    
    # Only consider closed trades
    closed_trades = [t for t in journal if t.get("closed", False)]
    
    if len(closed_trades) < 5:
        return 35  # Default
    
    # Find optimal confidence threshold
    for threshold in range(30, 60, 5):
        trades_at_threshold = [t for t in closed_trades if t.get("confidence", 0) >= threshold]
        if len(trades_at_threshold) >= 3:
            wins = sum(1 for t in trades_at_threshold if t.get("pnl", 0) > 0)
            win_rate = wins / len(trades_at_threshold)
            if win_rate >= 0.6:
                return threshold
    
    return 35  # Default


def get_weekly_report():
    """Generate weekly performance report"""
    stats = load_stats()
    journal = load_journal()
    
    # Get trades from last 7 days
    week_ago = datetime.now() - timedelta(days=7)
    recent_trades = []
    
    for entry in journal:
        try:
            trade_date = datetime.fromisoformat(entry["timestamp"])
            if trade_date >= week_ago and entry.get("closed", False):
                recent_trades.append(entry)
        except:
            pass
    
    if not recent_trades:
        return "No closed trades this week."
    
    wins = sum(1 for t in recent_trades if t.get("pnl", 0) > 0)
    losses = sum(1 for t in recent_trades if t.get("pnl", 0) < 0)
    total_pnl = sum(t.get("pnl", 0) for t in recent_trades)
    win_rate = wins / len(recent_trades) * 100 if recent_trades else 0
    
    report = f"""
📊 Weekly Trading Report
========================
Trades: {len(recent_trades)}
Wins: {wins} | Losses: {losses}
Win Rate: {win_rate:.1f}%
Total P&L: ${total_pnl:.2f}
"""
    
    return report


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: learning.py [record|analyze|optimal|report]")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "record":
        # record BUY AAPL 10 150.50 "BUY" 45 "RSI,MACD"
        trade_type = sys.argv[2]
        symbol = sys.argv[3]
        qty = int(sys.argv[4])
        price = float(sys.argv[5])
        signal = sys.argv[6]
        confidence = int(sys.argv[7])
        reasons = sys.argv[8].split(",")
        pnl = float(sys.argv[9]) if len(sys.argv) > 9 else 0
        
        stats = record_trade(trade_type, symbol, qty, price, signal, confidence, reasons, pnl)
        print(f"Recorded trade. Total: {stats['total_trades']}")
    
    elif command == "analyze":
        result = analyze_performance()
        print(json.dumps(result, indent=2))
    
    elif command == "optimal":
        threshold = get_optimal_confidence()
        print(f"Optimal confidence threshold: {threshold}")
    
    elif command == "report":
        print(get_weekly_report())
