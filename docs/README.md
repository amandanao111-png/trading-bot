# Nova's Trading Bot - Documentation

## 📁 File Structure

```
trading_bot/
├── auto_trade.py          # Main trading bot (V9 - Self-Learning)
├── realtime_trade.py      # Real-time monitor script
├── learning.py            # Self-learning module
├── docs/
│   ├── README.md          # This file
│   ├── PRACTICAL-GUIDE.md # How to use in practice
│   └── QUICK-REFERENCE.md # Quick commands
└── trade_journal.json     # Trade history
└── learned_stats.json     # Learning data
```

## 🚀 Quick Start

```bash
# Activate environment
cd ~/.openclaw/workspace/trading_bot
source venv/bin/activate

# Run trading bot
python3 auto_trade.py

# Check learning stats
python3 learning.py analyze
python3 learning.py report
```

## 📊 Features

- **Self-Learning**: Learns from trade history, adjusts confidence threshold
- **Multi-Indicator**: RSI, MACD, VWAP, Bollinger Bands, Volume
- **Trend Filter**: Only buy in uptrend
- **Smart Exit**: Trailing stop, partial take profit
- **Time Filter**: Avoid dangerous market open/close periods
- **Volatility Adjustment**: Adjust position size based on volatility

## 🔧 Configuration

Edit `auto_trade.py`:

```python
MAX_POSITION_PCT = 0.15   # 15% max position
MIN_CONFIDENCE = 35       # Signal threshold
STOP_LOSS_PCT = 0.02     # 2% stop loss
TAKE_PROFIT_PCT = 0.06   # 6% take profit
```

## 📈 Performance

- Paper Trading Account
- Runs every 30 minutes during market hours
- Auto-trades on RSI < 30 (BUY) or RSI > 70 (SELL)

## 🔄 Cron Jobs

| Job | Schedule | Description |
|-----|----------|-------------|
| realtime-trade-monitor | */30 14-20 * * 1-5 | Monitor & trade |
| auto-trade-morning | 35 14 * * 1-5 | Morning report |
| trading-report | 50 21 * * 1-5 | Evening report |
