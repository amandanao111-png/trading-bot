# Trading Bot - Practical Guide

## 🎯 How It Works

### 1. Signal Detection
The bot monitors multiple indicators:
- **RSI** (Relative Strength Index)
  - RSI < 30 = Oversold = BUY signal
  - RSI > 70 = Overbought = SELL signal
- **MACD** (Moving Average Convergence Divergence)
- **VWAP** (Volume Weighted Average Price)
- **Bollinger Bands**

### 2. Entry Rules
- Signal must have confidence >= 35
- Must be in uptrend (for BUY)
- Must not be in danger zone (first/last 30 min)
- Position size adjusted by volatility

### 3. Exit Rules
- Stop loss: 2% or 1.5x ATR
- Take profit: 6%
- Trailing stop: After 8% profit, trail 4% below peak
- Partial take: At 5% profit, sell 50%

### 4. Self-Learning
- Records every trade with signal reasons
- Analyzes win rate by signal type
- Adjusts confidence threshold based on performance

## 🔍 Monitoring

### Check Positions
```bash
cd ~/.openclaw/workspace/trading_bot
source venv/bin/activate
python3 -c "
import os, requests
API_KEY = os.environ['ALPACA_API_KEY']
SECRET_KEY = os.environ['ALPACA_SECRET_KEY']
r = requests.get('https://paper-api.alpaca.markets/v2/positions',
    headers={'APCA-API-KEY-ID': API_KEY, 'APCA-API-SECRET-KEY': SECRET_KEY})
for p in r.json(): print(f\"{p['symbol']}: {p['qty']} @ {p['current_price']}\")
"
```

### Check Learning Stats
```bash
python3 learning.py analyze
python3 learning.py report
python3 learning.py optimal
```

## ⚠️ Important Notes

1. **Paper Trading Only** - Currently using Alpaca paper account
2. **Market Hours** - Only trades during US market hours (14:00-20:00 UK time)
3. **Bug Fixed** - Now only sells stocks we actually own

## 🛠️ Troubleshooting

### Bot not trading?
- Check market is open (14:00-20:00 UK time)
- Check RSI is < 30 or > 70

### Want to stop auto-trading?
```bash
openclaw cron disable realtime-trade-monitor
```

### Want to run manually?
```bash
cd ~/.openclaw/workspace/trading_bot
source venv/bin/activate
python3 auto_trade.py
```
