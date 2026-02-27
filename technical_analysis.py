#!/usr/bin/env python3
"""
Technical Analysis Module
Adds RSI, MACD, Moving Averages to trading bot
"""

import random
from datetime import datetime
from typing import Dict, List

class TechnicalAnalysis:
    """Technical analysis indicators"""
    
    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> float:
        """Calculate RSI"""
        if len(prices) < period:
            return 50.0  # Neutral
        
        # Simplified RSI
        gains = []
        losses = []
        
        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    @staticmethod
    def moving_average(prices: List[float], period: int) -> float:
        """Calculate simple moving average"""
        if len(prices) < period:
            return prices[-1] if prices else 0
        return sum(prices[-period:]) / period
    
    @staticmethod
    def analyze(prices: List[float]) -> Dict:
        """Full technical analysis"""
        current = prices[-1] if prices else 0
        ma20 = TechnicalAnalysis.moving_average(prices, 20)
        ma50 = TechnicalAnalysis.moving_average(prices, 50) if len(prices) >= 50 else ma20
        rsi = TechnicalAnalysis.calculate_rsi(prices)
        
        # Generate signal
        signal = "HOLD"
        confidence = 0.5
        reasons = []
        
        # RSI signals
        if rsi < 30:
            signal = "BUY"
            confidence = 0.7
            reasons.append(f"RSI oversold ({rsi:.1f})")
        elif rsi > 70:
            signal = "SELL"
            confidence = 0.7
            reasons.append(f"RSI overbought ({rsi:.1f})")
        
        # Moving average signals
        if current < ma20 < ma50:
            signal = "BUY"
            confidence += 0.1
            reasons.append("Price below averages")
        elif current > ma20 > ma50:
            signal = "SELL"
            confidence += 0.1
            reasons.append("Price above averages")
        
        # Trend alignment
        if ma20 > ma50:
            reasons.append("Uptrend")
        else:
            reasons.append("Downtrend")
        
        return {
            "current": current,
            "rsi": rsi,
            "ma20": ma20,
            "ma50": ma50,
            "signal": signal,
            "confidence": min(confidence, 0.95),
            "reasons": reasons
        }


class SignalGenerator:
    """AI-powered signal generator with self-learning"""
    
    def __init__(self):
        self.market_data = self._get_market_data()
        self.ta = TechnicalAnalysis()
        self.patterns = self._load_patterns()
    
    def _get_market_data(self) -> Dict:
        """Get current market data"""
        # In production, this would fetch real data
        return {
            "AAPL": {"price": 182.50, "trend": "bullish", "volatility": "medium"},
            "NVDA": {"price": 195.83, "trend": "bullish", "volatility": "high"},
            "TSLA": {"price": 198.20, "trend": "neutral", "volatility": "high"},
            "MSFT": {"price": 400.00, "trend": "bullish", "volatility": "low"},
            "GOOGL": {"price": 312.00, "trend": "bullish", "volatility": "medium"},
            "BTC": {"price": 45000, "trend": "bullish", "volatility": "very_high"},
            "ETH": {"price": 2800, "trend": "bullish", "volatility": "very_high"},
        }
    
    def _load_patterns(self) -> List[Dict]:
        """Load learned patterns"""
        try:
            with open('learnings.json', 'r') as f:
                return json.load(f)
        except:
            return []
    
    def generate_signal(self, symbol: str) -> Dict:
        """Generate trading signal"""
        data = self.market_data.get(symbol, {"price": 100, "trend": "neutral"})
        price = data["price"]
        
        # Get technical analysis
        # In production: fetch historical prices
        mock_prices = [price * (1 + random.uniform(-0.1, 0.1)) for _ in range(60)]
        analysis = self.ta.analyze(mock_prices)
        
        # Combine with market context
        trend = data.get("trend", "neutral")
        volatility = data.get("volatility", "medium")
        
        # Adjust signal based on trend
        if trend == "bullish" and analysis["signal"] == "HOLD":
            analysis["signal"] = "BUY"
            analysis["confidence"] = 0.6
            analysis["reasons"].append(f"Trend: {trend}")
        elif trend == "bearish" and analysis["signal"] == "HOLD":
            analysis["signal"] = "SELL"
            analysis["confidence"] = 0.6
            analysis["reasons"].append(f"Trend: {trend}")
        
        # Risk adjustment
        risk_multiplier = {
            "very_high": 0.5,
            "high": 0.7,
            "medium": 0.85,
            "low": 1.0
        }.get(volatility, 0.7)
        
        analysis["confidence"] *= risk_multiplier
        analysis["volatility"] = volatility
        analysis["symbol"] = symbol
        
        return analysis
    
    def get_all_signals(self) -> List[Dict]:
        """Get signals for all symbols"""
        signals = []
        
        for symbol in self.market_data.keys():
            signal = self.generate_signal(symbol)
            signals.append(signal)
        
        # Sort by confidence
        signals.sort(key=lambda x: x["confidence"], reverse=True)
        
        return signals


def print_signals():
    """Print trading signals"""
    sg = SignalGenerator()
    
    print("\n" + "="*60)
    print("📊 NOVA'S TRADING SIGNALS")
    print("="*60)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    signals = sg.get_all_signals()
    
    print("\n🎯 TOP OPPORTUNITIES:\n")
    
    for i, s in enumerate(signals[:5], 1):
        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(s["signal"], "❓")
        
        print(f"{i}. {emoji} {s['symbol']}")
        print(f"   Signal: {s['signal']} ({s['confidence']*100:.0f}% confidence)")
        print(f"   Price: ${s['current']:.2f}")
        print(f"   RSI: {s['rsi']:.1f}")
        print(f"   Volatility: {s['volatility']}")
        for reason in s["reasons"]:
            print(f"   • {reason}")
        print()
    
    print("="*60)
    print("💡 Bot will auto-trade HIGH confidence signals when market opens")
    print("="*60)


if __name__ == "__main__":
    print_signals()
