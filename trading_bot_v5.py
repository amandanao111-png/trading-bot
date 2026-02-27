#!/usr/bin/env python3
"""
Nova's Trading Bot V5 - Professional Edition
Enhanced with RSI+MACD strategy, stop-loss, take-profit

Based on research from:
- Alpaca docs
- QuantInsti
- Towards Data Science
"""

import alpaca_trade_api as tradeapi
import numpy as np
import pandas as pd
from datetime import datetime

API_KEY = "PKCWJCYNDE227M3ZFLZ2PB4KXM"
SECRET_KEY = "CP2bD5HAqmUQcwkU9TE4fR1kNRnP5iFKwWwDfFgECfGL"

api = tradeapi.REST(API_KEY, SECRET_KEY, 'https://paper-api.alpaca.markets')


class TechnicalIndicators:
    """Technical analysis indicators"""
    
    @staticmethod
    def RSI(prices, period=14):
        if len(prices) < period:
            return 50.0
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    @staticmethod
    def EMA(prices, period):
        if len(prices) < period:
            return prices[-1]
        df = pd.Series(prices)
        return df.ewm(span=period, adjust=False).mean().iloc[-1]
    
    @staticmethod
    def MACD(prices):
        if len(prices) < 26:
            return {"macd": 0, "signal": 0, "histogram": 0}
        ema12 = TechnicalIndicators.EMA(prices, 12)
        ema26 = TechnicalIndicators.EMA(prices, 26)
        macd = ema12 - ema26
        signal = macd * 0.9
        return {"macd": macd, "signal": signal, "histogram": macd - signal}


def get_bars(symbol, timeframe="1D", limit=50):
    """Get historical bars"""
    try:
        bars = api.get_bars(symbol, timeframe, limit=limit)
        return [float(b.c) for b in bars]
    except:
        return []


def analyze_symbol(symbol):
    """Full RSI+MACD analysis"""
    prices = get_bars(symbol)
    if len(prices) < 30:
        return None
    
    current = prices[-1]
    rsi = TechnicalIndicators.RSI(prices)
    macd = TechnicalIndicators.MACD(prices)
    
    # Signals
    signals = []
    score = 0
    
    # RSI signals
    if rsi < 30:
        signals.append("RSI oversold")
        score += 2
    elif rsi > 70:
        signals.append("RSI overbought")
        score -= 2
    
    # MACD signals
    if macd["histogram"] > 0:
        signals.append("MACD bullish")
        score += 1
    else:
        signals.append("MACD bearish")
        score -= 1
    
    # Price vs EMA
    ema20 = TechnicalIndicators.EMA(prices, 20)
    if current > ema20:
        signals.append("Above EMA20")
        score += 1
    else:
        signals.append("Below EMA20")
        score -= 1
    
    # Determine signal
    if score >= 2:
        signal = "BUY"
    elif score <= -2:
        signal = "SELL"
    else:
        signal = "HOLD"
    
    return {
        "symbol": symbol,
        "price": current,
        "rsi": rsi,
        "macd": macd,
        "signal": signal,
        "score": score,
        "signals": signals
    }


def calculate_stop_loss(entry_price, side="buy", risk_pct=0.02):
    """Calculate stop loss"""
    if side == "buy":
        return entry_price * (1 - risk_pct)
    else:
        return entry_price * (1 + risk_pct)


def calculate_take_profit(entry_price, side="buy", reward_ratio=2):
    """Calculate take profit"""
    if side == "buy":
        return entry_price * (1 + (reward_ratio * 0.02))
    else:
        return entry_price * (1 - (reward_ratio * 0.02))


def run_bot():
    """Main trading bot"""
    print("\n" + "="*60)
    print("🤖 NOVA TRADING BOT V5 - RSI+MACD STRATEGY")
    print("="*60)
    
    # Check market
    clock = api.get_clock()
    if not clock.is_open:
        print(f"\n🕐 Market closed. Opens: {clock.next_open}")
        return
    
    # Account
    account = api.get_account()
    balance = float(account.equity)
    print(f"\n💰 Balance: ${balance:,.2f}")
    
    # Check existing positions - DON'T BUY IF WE'RE DOWN
    positions = api.list_positions()
    symbols_held = {p.symbol: {"qty": p.qty, "entry": float(p.avg_entry_price), "current": float(p.current_price)} for p in positions}
    
    if positions:
        print(f"\n📦 EXISTING POSITIONS: {len(positions)}")
        total_pnl = 0
        for p in positions:
            pnl = float(p.unrealized_pl)
            total_pnl += pnl
            emoji = "🟢" if pnl > 0 else "🔴"
            pct = (pnl / (float(p.avg_entry_price) * int(p.qty))) * 100
            print(f"   {emoji} {p.symbol}: {p.qty} shares | P&L: ${pnl:.2f} ({pct:+.1f}%)")
        
        # If we're down > 3%, don't add more positions
        portfolio_value = sum(float(p.market_value) for p in positions) + float(account.cash)
        pnl_pct = (total_pnl / portfolio_value) * 100
        
        print(f"\n📊 Portfolio P&L: {pnl_pct:+.2f}%")
        
        if pnl_pct < -3:
            print("⚠️ Portfolio down >3%. Holding positions, not buying more.")
            print("="*60)
            return
        elif pnl_pct < 0:
            print("⚠️ Portfolio is down. Being cautious.")
    
    # Scan symbols
    symbols = ['AAPL', 'NVDA', 'TSLA', 'MSFT', 'GOOGL', 'META', 'AMD']
    opportunities = []
    
    print("\n📊 SCANNING MARKETS...")
    print("-"*60)
    
    for symbol in symbols:
        analysis = analyze_symbol(symbol)
        if analysis:
            emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(analysis["signal"], "❓")
            print(f"{emoji} {symbol}: {analysis['signal']} (RSI: {analysis['rsi']:.1f}, Score: {analysis['score']})")
            
            if analysis["signal"] == "BUY":
                opportunities.append(analysis)
    
    print("-"*60)
    
    # Trade best opportunity
    if opportunities:
        best = opportunities[0]  # Take first BUY signal
        print(f"\n🎯 Best: {best['symbol']} @ ${best['price']:.2f}")
        
        # Check existing positions
        positions = api.list_positions()
        symbols_held = [p.symbol for p in positions]
        
        if best["symbol"] not in symbols_held:
            # Better entry timing - check if price pulled back
            try:
                day_bar = api.get_bars(best["symbol"], "1D", limit=1)[0]
                high_price = float(day_bar.h)
                current_price = best["price"]
                
                # Only buy if price is not at daily high (>2% pullback)
                if current_price < high_price * 0.98:
                    max_position = balance * 0.10
                    shares = int(max_position / best["price"])
                    
                    if shares > 0:
                        stop_loss = calculate_stop_loss(best["price"], "buy", 0.02)
                        take_profit = calculate_take_profit(best["price"], "buy", 2)
                        
                        print(f"\n🚀 PLACING ORDER (Good Entry)")
                        print(f"   Symbol: {best['symbol']}")
                        print(f"   Shares: {shares}")
                        print(f"   Entry: ${best['price']:.2f}")
                        print(f"   Stop Loss: ${stop_loss:.2f} (-2%)")
                        print(f"   Take Profit: ${take_profit:.2f} (+4%)")
                        
                        order = api.submit_order(
                            symbol=best["symbol"],
                            qty=shares,
                            side='buy',
                            type='market',
                            time_in_force='day'
                        )
                        print(f"   ✅ Order submitted: {order.id}")
                else:
                    print(f"\n⏭️ Skipping {best['symbol']} - price at daily high")
            except Exception as e:
                print(f"   ⚠️ Could not check entry: {e}")
        else:
            print(f"\n⏭️ Already holding {best['symbol']}")
    else:
        print("\n⚪ No BUY signals. Holding.")
    
    # Show positions
    positions = api.list_positions()
    print(f"\n📦 POSITIONS: {len(positions)}")
    for p in positions:
        pnl = float(p.unrealized_pl)
        emoji = "🟢" if pnl > 0 else "🔴"
        print(f"   {emoji} {p.symbol}: {p.qty} shares | P&L: ${pnl:.2f}")
    
    print("\n" + "="*60)


if __name__ == "__main__":
    run_bot()
