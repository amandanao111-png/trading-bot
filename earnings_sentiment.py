#!/usr/bin/env python3
"""
Earnings Sentiment Analysis
Analyzes upcoming earnings and provides sentiment indicators
"""

import requests
import json
from datetime import datetime, timedelta

# Using Yahoo Finance for earnings dates
# In production, would use a proper API

# Sample earnings calendar (would be fetched from API)
EARNINGS_CALENDAR = {
    "AAPL": {"date": "2026-04-28", "estimate": 2.15, "previous": 2.40},
    "MSFT": {"date": "2026-04-30", "estimate": 3.20, "previous": 3.45},
    "GOOGL": {"date": "2026-04-29", "estimate": 1.85, "previous": 2.10},
    "AMZN": {"date": "2026-05-01", "estimate": 1.50, "previous": 1.75},
    "NVDA": {"date": "2026-05-21", "estimate": 4.20, "previous": 4.80},
    "META": {"date": "2026-04-30", "estimate": 4.50, "previous": 4.90},
    "TSLA": {"date": "2026-04-23", "estimate": 0.95, "previous": 1.10},
    "AMD": {"date": "2026-04-29", "estimate": 1.25, "previous": 1.40},
    "JPM": {"date": "2026-04-25", "estimate": 4.10, "previous": 4.35},
    "BAC": {"date": "2026-04-25", "estimate": 0.85, "previous": 0.95}
}

def get_sentiment_indicators(symbol):
    """Get sentiment indicators for a stock"""
    earning = EARNINGS_CALENDAR.get(symbol, {})
    
    if not earning:
        return None
    
    estimate = earning.get("estimate", 0)
    previous = earning.get("previous", 0)
    
    # Calculate sentiment
    surprise_potential = ((previous - estimate) / previous * 100) if previous else 0
    
    # Sentiment score (-100 to +100)
    if surprise_potential > 20:
        sentiment = "STRONG_BUY"
        score = 80
    elif surprise_potential > 10:
        sentiment = "BUY"
        score = 40
    elif surprise_potential > 0:
        sentiment = "SLIGHT_BUY"
        score = 20
    elif surprise_potential > -10:
        sentiment = "HOLD"
        score = 0
    elif surprise_potential > -20:
        sentiment = "SELL"
        score = -40
    else:
        sentiment = "STRONG_SELL"
        score = -80
    
    return {
        "symbol": symbol,
        "earnings_date": earning.get("date"),
        "eps_estimate": estimate,
        "eps_previous": previous,
        "surprise_potential": surprise_potential,
        "sentiment": sentiment,
        "score": score
    }

def analyze_all():
    """Analyze all earnings"""
    print("\n" + "="*70)
    print("📈 EARNINGS SENTIMENT ANALYSIS")
    print("="*70)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    # Sort by earnings date
    sorted_earnings = sorted(
        [(s, e) for s, e in EARNINGS_CALENDAR.items()],
        key=lambda x: x[1]["date"]
    )
    
    print("\n📅 UPCOMING EARNINGS")
    print("-" * 70)
    print(f"{'Symbol':<8} {'Date':<12} {'Est EPS':<10} {'Prev EPS':<10} {'Surprise':<12} {'Sentiment'}")
    print("-" * 70)
    
    sentiments = []
    for symbol, earning in sorted_earnings:
        info = get_sentiment_indicators(symbol)
        if info:
            sentiments.append(info)
            emoji = "🟢" if info["sentiment"] in ["STRONG_BUY", "BUY"] else ("🔴" if info["sentiment"] in ["STRONG_SELL", "SELL"] else "🟡")
            print(f"{symbol:<8} {earning['date']:<12} ${earning['estimate']:<9.2f} ${earning['previous']:<9.2f} {info['surprise_potential']:>+9.1f}% {emoji} {info['sentiment']}")
    
    # Portfolio impact
    print("\n🎯 PORTFOLIO POSITIONS IMPACT")
    print("-" * 70)
    
    # Load positions
    try:
        with open("/Users/amandaai/.openclaw/workspace/trading_bot/trade_journal.json") as f:
            journal = json.load(f)
        
        # Get unique symbols from open positions
        open_symbols = set()
        for entry in journal:
            if entry.get("type") == "BUY" and not entry.get("closed", False):
                open_symbols.add(entry["symbol"])
        
        portfolio_earnings = [s for s in open_symbols if s in EARNINGS_CALENDAR]
        
        if portfolio_earnings:
            for symbol in portfolio_earnings:
                info = get_sentiment_indicators(symbol)
                if info:
                    action = "⚠️ WATCH" if info["score"] < 0 else "✅ OK"
                    print(f"  {symbol}: {info['earnings_date']} | Sentiment: {info['sentiment']} | {action}")
        else:
            print("  No upcoming earnings for current positions")
    except Exception as e:
        print(f"  Could not load positions: {e}")
    
    # Summary
    buy_signals = [s for s in sentiments if s["score"] > 20]
    sell_signals = [s for s in sentiments if s["score"] < -20]
    
    print("\n📊 SUMMARY")
    print("-" * 70)
    print(f"  Total analyzed: {len(sentiments)}")
    print(f"  Strong buy signals: {len([s for s in sentiments if s['sentiment'] == 'STRONG_BUY'])}")
    print(f"  Buy signals: {len([s for s in sentiments if s['sentiment'] == 'BUY'])}")
    print(f"  Sell signals: {len([s for s in sentiments if s['sentiment'] == 'STRONG_SELL'])}")
    
    # Save report
    report = {
        "date": datetime.now().isoformat(),
        "earnings": sentiments
    }
    
    with open("/Users/amandaai/.openclaw/workspace/trading_bot/earnings_sentiment.json", "w") as f:
        json.dump(report, f, indent=2)
    
    return sentiments

if __name__ == "__main__":
    analyze_all()
