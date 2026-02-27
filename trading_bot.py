#!/usr/bin/env python3
"""
Nova's Trading Bot - Paper Trading Edition
Built by AI Agent for demonstration purposes
"""

import os
import time
import json
from datetime import datetime
from typing import Dict, List, Optional

# Try to import Alpaca - if not available, use mock mode
try:
    import alpaca_trade_api as tradeapi
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    print("Alpaca SDK not found - running in MOCK mode")


class TradingBot:
    """AI-powered trading bot with risk management"""
    
    def __init__(self, api_key: str = None, secret_key: str = None, paper: bool = True):
        self.paper = paper
        self.positions = []
        self.trade_history = []
        
        if ALPACA_AVAILABLE and api_key and secret_key:
            base_url = 'https://paper-api.alpaca.markets' if paper else 'https://api.alpaca.markets'
            self.api = tradeapi.REST(api_key, secret_key, base_url)
            self.mode = "LIVE"
        else:
            self.mode = "MOCK"
            self.balance = 100000  # Paper trading balance
            
        # Risk management settings
        self.max_position_size = 0.1  # Max 10% of portfolio per trade
        self.max_loss_per_trade = 0.02  # Max 2% loss per trade
        self.max_daily_loss = 0.05  # Max 5% daily loss
        
    def get_account_info(self) -> Dict:
        """Get account information"""
        if self.mode == "MOCK":
            return {
                "balance": self.balance,
                "equity": self.balance,
                "mode": "MOCK (Paper Trading)"
            }
        else:
            account = self.api.get_account()
            return {
                "balance": float(account.cash),
                "equity": float(account.equity),
                "mode": "LIVE"
            }
    
    def get_current_price(self, symbol: str) -> float:
        """Get current price of a symbol"""
        if self.mode == "MOCK":
            # Mock prices for demo
            mock_prices = {
                "GBP/USD": 1.35,
                "GBP/JPY": 212.0,
                "EUR/USD": 1.08,
                "BTC/USD": 45000,
                "AAPL": 180,
                "TSLA": 200
            }
            return mock_prices.get(symbol, 100.0)
        else:
            try:
                bar = self.api.get_latest_bar(symbol)
                return bar.c
            except:
                return 0.0
    
    def analyze_market(self, symbol: str) -> Dict:
        """Analyze market and generate signals"""
        price = self.get_current_price(symbol)
        
        # Simple moving average analysis (mock for demo)
        # In real implementation, would use historical data
        signals = {
            "symbol": symbol,
            "current_price": price,
            "signal": "NEUTRAL",
            "confidence": 0.5,
            "timestamp": datetime.now().isoformat()
        }
        
        # Add market analysis based on common patterns
        if "GBP" in symbol:
            signals["analysis"] = "Pound near resistance - consider SELL"
            signals["resistance"] = price * 1.02
            signals["support"] = price * 0.98
        elif "BTC" in symbol:
            signals["analysis"] = "Crypto showing volatility"
            
        return signals
    
    def calculate_position_size(self, symbol: str, risk_amount: float) -> float:
        """Calculate position size based on risk"""
        account = self.get_account_info()
        price = self.get_current_price(symbol)
        
        max_position = account["balance"] * self.max_position_size
        risk_position = risk_amount / self.max_loss_per_trade
        
        position = min(max_position, risk_position)
        return position
    
    def is_market_open(self) -> bool:
        """Check if market is currently open"""
        if self.mode == "MOCK":
            return True  # Always open in mock mode
        
        try:
            clock = self.api.get_clock()
            return clock.is_open
        except:
            return False
    
    def wait_for_market_open(self, timeout_seconds: int = 300):
        """Wait for market to open"""
        import time
        print(f"⏳ Waiting for market to open...")
        
        while timeout_seconds > 0:
            if self.is_market_open():
                print(f"✅ Market is open!")
                return True
            time.sleep(10)
            timeout_seconds -= 10
            print(f"   Still waiting... ({timeout_seconds}s)")
        
        return False
    
    def place_order(self, symbol: str, qty: float, side: str, 
                   order_type: str = "market", 
                   stop_loss: float = None,
                   take_profit: float = None) -> Dict:
        """Place a trade order"""
        
        # Check market hours first!
        if not self.is_market_open():
            print(f"⚠️ Market is CLOSED. Order will queue.")
            print(f"   Next open: {self.api.get_clock().next_open}")
        
        order = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "type": order_type,
            "status": "pending",
            "timestamp": datetime.now().isoformat()
        }
        
        if stop_loss:
            order["stop_loss"] = stop_loss
        if take_profit:
            order["take_profit"] = take_profit
            
        if self.mode == "MOCK":
            # Simulate order execution
            price = self.get_current_price(symbol)
            order["entry_price"] = price
            order["status"] = "filled"
            self.positions.append(order)
            self.balance -= qty * price if side == "buy" else -qty * price
            print(f"✅ MOCK ORDER FILLED: {side.upper()} {qty} {symbol} @ {price}")
        else:
            try:
                self.api.submit_order(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    type=order_type,
                    stop_loss=stop_loss,
                    take_profit=take_profit
                )
                order["status"] = "submitted"
            except Exception as e:
                order["status"] = "error"
                order["error"] = str(e)
                
        self.trade_history.append(order)
        return order
    
    def close_position(self, symbol: str) -> Dict:
        """Close an existing position"""
        if self.mode == "MOCK":
            for pos in self.positions:
                if pos["symbol"] == symbol:
                    self.positions.remove(pos)
                    return {"status": "closed", "symbol": symbol}
        else:
            try:
                self.api.close_position(symbol)
                return {"status": "closed", "symbol": symbol}
            except Exception as e:
                return {"status": "error", "error": str(e)}
    
    def get_positions(self) -> List[Dict]:
        """Get current positions"""
        if self.mode == "MOCK":
            return self.positions
        else:
            return self.api.list_positions()
    
    def generate_signal(self, symbol: str) -> str:
        """Generate trading signal based on analysis"""
        analysis = self.analyze_market(symbol)
        
        # Simple signal logic (would be more sophisticated in production)
        price = analysis["current_price"]
        resistance = analysis.get("resistance", price * 1.05)
        support = analysis.get("support", price * 0.95)
        
        if price >= resistance:
            return "SELL"
        elif price <= support:
            return "BUY"
        else:
            return "HOLD"
    
    def run_trading_cycle(self, symbols: List[str]):
        """Run one cycle of trading analysis"""
        print(f"\n{'='*50}")
        print(f"🚀 TRADING BOT CYCLE - {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*50}")
        
        account = self.get_account_info()
        print(f"📊 Account: £{account['balance']:.2f} ({account['mode']})")
        
        signals = []
        for symbol in symbols:
            signal = self.generate_signal(symbol)
            analysis = self.analyze_market(symbol)
            signals.append({
                "symbol": symbol,
                "signal": signal,
                "price": analysis["current_price"]
            })
            
            emoji = "🔴" if signal == "SELL" else "🟢" if signal == "BUY" else "⚪"
            print(f"{emoji} {symbol}: {signal} @ {analysis['current_price']}")
        
        return signals


# Demo usage
if __name__ == "__main__":
    print("🚀 Nova's Trading Bot - Starting...")
    
    # Alpaca API Keys - UPDATE THESE WITH YOUR KEYS
# Get keys from: https://app.alpaca.markets/
API_KEY = "PKCWJCYNDE227M3ZFLZ2PB4KXM"
SECRET_KEY = "CP2bD5HAqmUQcwkU9TE4fR1kNRnP5iFKwWwDfFgECfGL"

# Initialize with paper trading
bot = TradingBot(api_key=API_KEY, secret_key=SECRET_KEY, paper=True)
    
    # Show account info
    account = bot.get_account_info()
    print(f"\n📊 Account Mode: {account['mode']}")
    print(f"💰 Balance: £{account['balance']:,.2f}")
    
    # Trading symbols
    symbols = ["GBP/USD", "GBP/JPY", "EUR/USD", "BTC/USD"]
    
    # Run analysis
    signals = bot.run_trading_cycle(symbols)
    
    print(f"\n✅ Trading bot initialized successfully!")
    print(f"📝 To connect to live trading, provide Alpaca API keys")
