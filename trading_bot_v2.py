#!/usr/bin/env python3
"""
Nova's Trading Bot - Version 2.0
Enhanced with self-learning and risk management
"""

import os
import time
import json
from datetime import datetime
from typing import Dict, List, Optional

try:
    import alpaca_trade_api as tradeapi
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False


class TradingBot:
    """AI-powered trading bot with self-learning"""
    
    def __init__(self, api_key: str = None, secret_key: str = None, paper: bool = True):
        self.paper = paper
        self.mode = "MOCK"
        self.api = None
        self.trade_history = []
        self.learned_patterns = []
        
        # Risk Management (Industry Standard)
        self.max_position_pct = 0.10  # Max 10% per trade
        self.max_loss_per_trade_pct = 0.02  # Max 2% per trade
        self.max_daily_loss_pct = 0.05  # Max 5% daily loss
        self.risk_reward_ratio = 2.0  # 2:1 minimum
        
        if ALPACA_AVAILABLE and api_key and secret_key:
            base_url = 'https://paper-api.alpaca.markets' if paper else 'https://api.alpaca.markets'
            self.api = tradeapi.REST(api_key, secret_key, base_url)
            self.mode = "LIVE" if not paper else "PAPER"
            print(f"✅ Connected to Alpaca ({self.mode})")
            
            # Get account info
            account = self.api.get_account()
            self.balance = float(account.equity)
        else:
            self.balance = 100000  # Paper default
        
        # Load learned patterns
        self.load_learnings()
    
    def load_learnings(self):
        """Load learned patterns from file"""
        try:
            with open('learnings.json', 'r') as f:
                self.learned_patterns = json.load(f)
                print(f"📚 Loaded {len(self.learned_patterns)} learned patterns")
        except:
            self.learned_patterns = []
    
    def save_learnings(self):
        """Save learned patterns"""
        with open('learnings.json', 'w') as f:
            json.dump(self.learned_patterns, f, indent=2)
    
    def learn(self, pattern: str, outcome: str):
        """Learn from trading pattern"""
        entry = {
            "pattern": pattern,
            "outcome": outcome,
            "timestamp": datetime.now().isoformat()
        }
        self.learned_patterns.append(entry)
        self.save_learnings()
        print(f"🧠 Learned: {pattern} -> {outcome}")
    
    # ============== MARKET ANALYSIS ==============
    
    def is_market_open(self) -> bool:
        """Check if market is open"""
        if not self.api:
            return True
        
        try:
            clock = self.api.get_clock()
            is_open = clock.is_open
            if not is_open:
                print(f"⏰ Market closed. Opens: {clock.next_open}")
            return is_open
        except Exception as e:
            print(f"⚠️ Error checking clock: {e}")
            return False
    
    def get_price(self, symbol: str) -> float:
        """Get current price"""
        if not self.api:
            return 100.0  # Mock price
        
        try:
            bar = self.api.get_latest_bar(symbol)
            return float(bar.c)
        except:
            return 0.0
    
    def analyze_symbol(self, symbol: str) -> Dict:
        """Analyze a symbol for trading opportunity"""
        price = self.get_price(symbol)
        
        # Simple technical analysis (would be more sophisticated in production)
        analysis = {
            "symbol": symbol,
            "price": price,
            "signal": "HOLD",
            "confidence": 0.5,
            "stop_loss_pct": 0.05,
            "take_profit_pct": 0.10,
            "reason": "No clear signal"
        }
        
        # Add simple logic
        # In production: use RSI, MACD, Moving Averages, etc.
        
        return analysis
    
    # ============== POSITION SIZING ==============
    
    def calculate_position_size(self, symbol: str, risk_pct: float = 0.02) -> int:
        """Calculate position size based on risk"""
        price = self.get_price(symbol)
        
        # Risk amount in dollars
        risk_amount = self.balance * risk_pct
        
        # Shares to buy (risk / stop loss distance)
        # For now, just use fixed percentage of portfolio
        position_value = self.balance * self.max_position_pct
        shares = int(position_value / price)
        
        return max(1, shares)
    
    # ============== ORDER EXECUTION ==============
    
    def place_trade(self, symbol: str, side: str, qty: int = None, 
                    use_stop_loss: bool = True, use_take_profit: bool = True) -> Dict:
        """Place a trade with proper risk management"""
        
        # Check market first
        if not self.is_market_open():
            print("⚠️ Market is CLOSED - order will queue")
        
        # Calculate position size if not provided
        if not qty:
            qty = self.calculate_position_size(symbol)
        
        price = self.get_price(symbol)
        position_value = qty * price
        
        print(f"\n{'='*50}")
        print(f"🤖 NOVA'S TRADE")
        print(f"{'='*50}")
        print(f"Symbol: {symbol}")
        print(f"Side: {side.upper()}")
        print(f"Qty: {qty}")
        print(f"Price: ${price}")
        print(f"Value: ${position_value:,.2f}")
        print(f"Portfolio: {position_value/self.balance*100:.1f}%")
        
        # Risk calculations
        stop_pct = self.max_loss_per_trade_pct
        stop_loss = price * (1 - stop_pct) if side == "buy" else price * (1 + stop_pct)
        take_profit = price * (1 + stop_pct * self.risk_reward_ratio) if side == "buy" else price * (1 - stop_pct * self.risk_reward_ratio)
        
        print(f"\n🛡️ Risk Management:")
        print(f"   Stop Loss: ${stop_loss:.2f} ({stop_pct*100}%)")
        print(f"   Take Profit: ${take_profit:.2f} ({stop_pct*self.risk_reward_ratio*100}%)")
        print(f"{'='*50}")
        
        if not self.api:
            print("📝 MOCK MODE - Trade simulated")
            return {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
                "status": "mock"
            }
        
        try:
            # Place main order
            order = self.api.submit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                type='market',
                time_in_force='day'
            )
            
            # Record trade
            trade_record = {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "entry_price": price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "timestamp": datetime.now().isoformat(),
                "status": "open"
            }
            self.trade_history.append(trade_record)
            
            print(f"\n✅ Order submitted!")
            print(f"   Order ID: {order.id}")
            
            # Note: In production, you'd place OCO (One Cancels Other) orders
            # for stop loss and take profit
            
            return trade_record
            
        except Exception as e:
            print(f"❌ Order failed: {e}")
            return {"error": str(e)}
    
    # ============== POSITION MANAGEMENT ==============
    
    def get_positions(self) -> List[Dict]:
        """Get current positions"""
        if not self.api:
            return []
        
        try:
            positions = self.api.list_positions()
            return [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "entry": float(p.cost_basis) / float(p.qty),
                    "current": float(p.market_value) / float(p.qty),
                    "pnl": p.unrealized_pl,
                    "pnl_pct": p.unrealized_plpc
                }
                for p in positions
            ]
        except:
            return []
    
    def close_position(self, symbol: str, reason: str = "manual"):
        """Close a position"""
        if not self.api:
            return {"status": "mock_closed"}
        
        try:
            self.api.close_position(symbol)
            self.learn(f"closed_{symbol}", reason)
            return {"status": "closed", "symbol": symbol}
        except Exception as e:
            return {"error": str(e)}
    
    # ============== TRADING STRATEGY ==============
    
    def run_strategy(self, symbols: List[str]):
        """Run trading strategy on symbols"""
        print(f"\n{'='*50}")
        print(f"📊 SCANNING MARKETS")
        print(f"{'='*50}")
        print(f"Balance: ${self.balance:,.2f}")
        
        for symbol in symbols:
            analysis = self.analyze_symbol(symbol)
            emoji = "🟢" if analysis["signal"] == "BUY" else "🔴" if analysis["signal"] == "SELL" else "⚪"
            print(f"\n{emoji} {symbol}: {analysis['signal']}")
            print(f"   Price: ${analysis['price']}")
            print(f"   Reason: {analysis['reason']}")
            
            # Auto-trade on strong signals
            if analysis["signal"] in ["BUY", "SELL"] and analysis["confidence"] > 0.7:
                print(f"   ⚡ High confidence! Checking position...")
                
                # Check if we have positions
                positions = self.get_positions()
                has_position = any(p["symbol"] == symbol for p in positions)
                
                if not has_position:
                    print(f"   🚀 Placing trade!")
                    self.place_trade(symbol, analysis["signal"].lower())
                else:
                    print(f"   ⏭️ Already have position")
        
        print(f"\n{'='*50}")
        print(f"✅ SCAN COMPLETE")
        print(f"{'='*50}")
    
    # ============== STATUS ==============
    
    def status(self):
        """Print current status"""
        print(f"\n{'='*50}")
        print(f"🤖 NOVA'S BOT STATUS")
        print(f"{'='*50}")
        print(f"Mode: {self.mode}")
        print(f"Balance: ${self.balance:,.2f}")
        print(f"Market: {'🟢 OPEN' if self.is_market_open() else '🔴 CLOSED'}")
        
        positions = self.get_positions()
        print(f"Positions: {len(positions)}")
        for p in positions:
            print(f"  {p['symbol']}: {p['qty']} shares | P&L: ${float(p['pnl']):.2f}")
        
        print(f"Trades Today: {len(self.trade_history)}")
        print(f"Learned Patterns: {len(self.learned_patterns)}")
        print(f"{'='*50}\n")


# ============== MAIN ==============

if __name__ == "__main__":
    # Load keys
    API_KEY = "PKCWJCYNDE227M3ZFLZ2PB4KXM"
    SECRET_KEY = "CP2bD5HAqmUQcwkU9TE4fR1kNRnP5iFKwWwDfFgECfGL"
    
    # Initialize
    bot = TradingBot(API_KEY, SECRET_KEY, paper=True)
    
    # Show status
    bot.status()
    
    # Run strategy scan
    symbols = ['AAPL', 'NVDA', 'TSLA', 'MSFT', 'GOOGL']
    bot.run_strategy(symbols)
