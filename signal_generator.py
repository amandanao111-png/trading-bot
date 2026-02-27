#!/usr/bin/env python3
"""
Trading Signal Generator
Analyzes market data and generates buy/sell signals
"""

import random
from datetime import datetime
from typing import Dict, List

MARKET_DATA = {
    "GBP/USD": {
        "current": 1.3512,
        "resistance": 1.3650,
        "support": 1.3350,
        "trend": "bearish",
        "volatility": "medium"
    },
    "GBP/JPY": {
        "current": 212.05,
        "resistance": 215.00,
        "support": 210.00,
        "trend": "neutral",
        "volatility": "high"
    },
    "EUR/USD": {
        "current": 1.0815,
        "resistance": 1.0950,
        "support": 1.0700,
        "trend": "bullish",
        "volatility": "low"
    },
    "EUR/GBP": {
        "current": 0.8005,
        "resistance": 0.8100,
        "support": 0.7900,
        "trend": "bullish",
        "volatility": "low"
    },
    "BTC/USD": {
        "current": 45250,
        "resistance": 48000,
        "support": 42000,
        "trend": "bullish",
        "volatility": "high"
    },
    "AAPL": {
        "current": 182.50,
        "resistance": 190.00,
        "support": 175.00,
        "trend": "bullish",
        "volatility": "medium"
    },
    "TSLA": {
        "current": 198.20,
        "resistance": 220.00,
        "support": 180.00,
        "trend": "neutral",
        "volatility": "high"
    }
}


class SignalGenerator:
    """Generate trading signals based on technical analysis"""
    
    def __init__(self):
        self.signals_history = []
    
    def analyze(self, symbol: str) -> Dict:
        """Analyze a symbol and generate signal"""
        data = MARKET_DATA.get(symbol, {})
        
        if not data:
            return {"error": f"No data for {symbol}"}
        
        current = data["current"]
        resistance = data["resistance"]
        support = data["support"]
        
        # Calculate position relative to range
        range_size = resistance - support
        position = (current - support) / range_size if range_size > 0 else 0.5
        
        # Generate signal
        if position > 0.75:
            signal = "STRONG_SELL"
            confidence = 0.75
        elif position > 0.55:
            signal = "SELL"
            confidence = 0.60
        elif position < 0.25:
            signal = "STRONG_BUY"
            confidence = 0.75
        elif position < 0.45:
            signal = "BUY"
            confidence = 0.60
        else:
            signal = "HOLD"
            confidence = 0.50
        
        # Risk assessment
        risk = "HIGH" if data["volatility"] == "high" else "MEDIUM" if data["volatility"] == "medium" else "LOW"
        
        result = {
            "symbol": symbol,
            "signal": signal,
            "confidence": confidence,
            "current_price": current,
            "resistance": resistance,
            "support": support,
            "trend": data["trend"],
            "volatility": data["volatility"],
            "risk": risk,
            "recommendation": self._get_recommendation(signal, risk),
            "timestamp": datetime.now().isoformat()
        }
        
        self.signals_history.append(result)
        return result
    
    def _get_recommendation(self, signal: str, risk: str) -> str:
        """Get trade recommendation based on signal and risk"""
        if signal in ["STRONG_BUY", "BUY"]:
            if risk == "LOW":
                return "Enter long position with tight stop loss"
            else:
                return "Enter with smaller size, wider stop"
        elif signal in ["STRONG_SELL", "SELL"]:
            if risk == "LOW":
                return "Enter short position with tight stop loss"
            else:
                return "Enter with smaller size, wider stop"
        else:
            return "Wait for clearer signal"
    
    def get_all_signals(self) -> List[Dict]:
        """Get signals for all tracked symbols"""
        return [self.analyze(symbol) for symbol in MARKET_DATA.keys()]
    
    def get_top_signals(self, limit: int = 3) -> List[Dict]:
        """Get top trading opportunities"""
        signals = self.get_all_signals()
        
        # Filter for actionable signals
        actionable = [s for s in signals if s["signal"] in ["STRONG_BUY", "BUY", "STRONG_SELL", "SELL"]]
        
        # Sort by confidence
        actionable.sort(key=lambda x: x["confidence"], reverse=True)
        
        return actionable[:limit]


def print_signals():
    """Print current trading signals"""
    sg = SignalGenerator()
    
    print("\n" + "="*60)
    print("📊 TRADING SIGNALS - " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("="*60)
    
    signals = sg.get_all_signals()
    
    for s in signals:
        emoji = {
            "STRONG_BUY": "🟢🟢",
            "BUY": "🟢",
            "HOLD": "⚪",
            "SELL": "🔴",
            "STRONG_SELL": "🔴🔴"
        }.get(s["signal"], "❓")
        
        print(f"\n{emoji} {s['symbol']}")
        print(f"   Signal: {s['signal']} ({s['confidence']*100:.0f}% confidence)")
        print(f"   Price: {s['current_price']}")
        print(f"   Range: {s['support']} - {s['resistance']}")
        print(f"   Risk: {s['risk']}")
        print(f"   → {s['recommendation']}")
    
    print("\n" + "="*60)
    print("🎯 TOP OPPORTUNITIES:")
    print("="*60)
    
    top = sg.get_top_signals(3)
    for i, s in enumerate(top, 1):
        print(f"{i}. {s['symbol']}: {s['signal']} ({s['confidence']*100:.0f}%)")


if __name__ == "__main__":
    print_signals()
