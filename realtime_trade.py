#!/usr/bin/env python3
"""
Nova's Real-Time Trading Bot
Monitors continuously and trades when opportunities arise
"""

import requests
import numpy as np
import pandas as pd
from datetime import datetime
import time
import sys
import os

# API Keys
API_KEY = "PKCWJCYNDE227M3ZFLZ2PB4KXM"
SECRET_KEY = "CP2bD5HAqmUQcwkU9TE4fR1kNRnP5iFKwWwDfFgECfGL"
BASE_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": SECRET_KEY
}

# Config
MAX_POSITION_PCT = 0.10  # 10% max position
MIN_CONFIDENCE = 65       # 65% min confidence
CHECK_INTERVAL = 300      # Check every 5 minutes
NOTIFY_INTERVAL = 1800    # Notify every 30 minutes

# Telegram for alerts
TELEGRAM_TOKEN = "8763028842:AAGjI1ChOjRqrtK5inuVoqkFCTfHr2C7180"
TELEGRAM_CHAT_ID = "8250061141"


def send_telegram(msg):
    """Send alert to Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=data, timeout=10)
    except:
        pass


def get_bars(symbol, timeframe="5Min", limit=50):
    """Get stock bars"""
    url = f"{DATA_URL}/v2/stocks/{symbol}/bars"
    params = {"timeframe": timeframe, "limit": limit}
    try:
        r = requests.get(url, headers=HEADERS, params=params)
        data = r.json()
        if "bars" in data and len(data["bars"]) > 0:
            return [b["c"] for b in data["bars"]]
    except:
        pass
    return None


def get_account():
    """Get account info"""
    url = f"{BASE_URL}/v2/account"
    try:
        r = requests.get(url, headers=HEADERS)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return {"portfolio_value": "100000"}


def get_positions():
    """Get current positions"""
    url = f"{BASE_URL}/v2/positions"
    try:
        r = requests.get(url, headers=HEADERS)
        if r.status_code == 200:
            return {p["symbol"]: int(p["qty"]) for p in r.json()}
    except:
        pass
    return {}


class TechnicalIndicators:
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


def analyze_stock(symbol):
    """Analyze stock"""
    prices = get_bars(symbol)
    if not prices or len(prices) < 20:
        return None
    
    rsi = TechnicalIndicators.RSI(prices[-20:])
    current_price = prices[-1]
    
    signal = "HOLD"
    confidence = 0
    
    if rsi < 30:  # Strong oversold
        signal = "BUY"
        confidence = min(90, 75 + (30 - rsi))
    elif rsi < 40:  # Moderately oversold
        signal = "BUY"
        confidence = min(80, 60 + (40 - rsi))
    elif rsi > 70:  # Overbought
        signal = "SELL"
        confidence = min(90, 75 + (rsi - 70))
    
    return {
        "symbol": symbol,
        "price": current_price,
        "rsi": rsi,
        "signal": signal,
        "confidence": confidence
    }


def place_order(symbol, qty, side="buy"):
    """Place order"""
    url = f"{BASE_URL}/v2/orders"
    data = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "day"
    }
    try:
        r = requests.post(url, headers=HEADERS, json=data)
        if r.status_code in [200, 201]:
            return True, f"✅ {side.upper()} {qty} {symbol} @ ${r.json().get('filled_avg_price', 'market')}"
        else:
            return False, f"❌ Order failed: {r.text[:100]}"
    except Exception as e:
        return False, f"❌ Error: {str(e)[:100]}"


def run_real_time():
    """Main loop"""
    print("\n" + "="*60)
    print("🚀 Nova's Real-Time Trading Bot")
    print("="*60)
    
    stocks = ["AAPL", "NVDA", "TSLA", "MSFT", "GOOGL", "AMZN", "META", "NFLX"]
    
    last_notify = time.time()
    last_prices = {}
    start_time = time.time()
    
    send_telegram("🚀 *Real-time Trading Bot Started!*\n\nMonitoring: " + ", ".join(stocks))
    
    print(f"Monitoring {len(stocks)} stocks...")
    print(f"Check interval: {CHECK_INTERVAL}s | Notify interval: {NOTIFY_INTERVAL}s")
    print("Press Ctrl+C to stop\n")
    
    while True:
        try:
            # Get account & positions
            account = get_account()
            portfolio_value = float(account.get("portfolio_value", 100000))
            positions = get_positions()
            
            # Check each stock
            for symbol in stocks:
                analysis = analyze_stock(symbol)
                if not analysis:
                    continue
                
                price = analysis['price']
                rsi = analysis['rsi']
                signal = analysis['signal']
                confidence = analysis['confidence']
                
                # Detect price change
                price_change = ""
                if symbol in last_prices:
                    change = ((price - last_prices[symbol]) / last_prices[symbol]) * 100
                    if abs(change) > 2:
                        price_change = f" ({'+' if change > 0 else ''}{change:.1f}%)"
                
                last_prices[symbol] = price
                
                # Show status
                emoji = "� eco" if confidence > 0 else "⬛"
                print(f"{symbol}: ${price:.2f} | RSI: {rsi:.1f} | {signal} ({confidence}%){price_change}")
                
                # Execute trade if signal is strong
                if signal == "BUY" and confidence >= MIN_CONFIDENCE:
                    if symbol in positions:
                        print(f"   → Already own {positions[symbol]} shares, skipping")
                        continue
                    
                    # Calculate position
                    max_position = portfolio_value * MAX_POSITION_PCT
                    qty = int(max_position / price)
                    
                    if qty > 0:
                        print(f"   🚀 Executing BUY...")
                        success, msg = place_order(symbol, qty, "buy")
                        print(f"   {msg}")
                        
                        if success:
                            positions[symbol] = qty
                            send_telegram(f"� *TRADE EXECUTED!*\n\n{msg}\n\nRSI: {rsi:.1f} | Confidence: {confidence}%")
                
                elif signal == "SELL" and confidence >= MIN_CONFIDENCE:
                    if symbol in positions:
                        print(f"   🚀 Executing SELL...")
                        success, msg = place_order(symbol, positions[symbol], "sell")
                        print(f"   {msg}")
                        
                        if success:
                            del positions[symbol]
                            send_telegram(f"� *SELL ORDER!*\n\n{msg}\n\nRSI: {rsi:.1f} | Confidence: {confidence}%")
            
            # Periodic notification
            current_time = time.time()
            if current_time - last_notify >= NOTIFY_INTERVAL:
                runtime = int((current_time - start_time) / 60)
                msg = f"📊 *Bot Update* ({runtime}min running)\n\n"
                msg += f"Portfolio: ${portfolio_value:,.2f}\n"
                msg += f"Positions: {positions if positions else 'None'}\n\n"
                msg += f"Monitoring {len(stocks)} stocks..."
                send_telegram(msg)
                last_notify = current_time
            
            print(f"\nNext check in {CHECK_INTERVAL}s...")
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n\n🛑 Bot stopped by user")
            send_telegram("🛑 *Trading Bot Stopped*")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(60)  # Wait 1 min on error


if __name__ == "__main__":
    run_real_time()
