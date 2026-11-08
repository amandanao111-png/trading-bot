# 🤖 Nova's Trading Bot V2.0

AI-powered autonomous trading with self-learning capabilities.

## 🚀 Features

| Feature | Description |
|---------|-------------|
| **Paper Trading** | Test with $100,000 fake money |
| **Risk Management** | 2% max loss per trade |
| **Self-Learning** | Remembers patterns & outcomes |
| **Technical Analysis** | RSI, Moving Averages |
| **Auto-Trading** | Trades when market opens |

## 📦 Files

```
trading_bot_v2.py       # Main trading bot (enhanced)
technical_analysis.py   # Technical analysis module
signal_generator.py    # Signal generation
journal.md             # Trading journal
learnings.json         # Learned patterns
README.md             # This file
```

## 🎯 Quick Start

```bash
# Set API keys (required!)
export ALPACA_API_KEY="your_key_here"
export ALPACA_API_SECRET="your_secret_here"

# Check signals
python3 technical_analysis.py

# Run trading bot
python3 trading_bot_v2.py
```

> ⚠️ **Security Note**: API keys are now loaded from environment variables. Never hardcode secrets!

## 📊 Current Signals (V2)

| Rank | Symbol | Signal | Confidence |
|------|--------|--------|------------|
| 1 | MSFT | BUY 🟢 | 60% |
| 2 | GOOGL | BUY 🟢 | 51% |
| 3 | NVDA | BUY 🟢 | 42% |

## 🛡️ Risk Management

| Rule | Value |
|------|-------|
| Max Position | 10% of portfolio |
| Max Loss/Trade | 2% |
| Max Daily Loss | 5% |
| Risk:Reward | 2:1 minimum |

## 🔧 Setup Live Trading

1. Get Alpaca API keys: https://app.alpaca.markets/
2. Update API_KEY and SECRET_KEY in code
3. Set paper=True for testing, False for live

## 🤖 Autonomous Trading

When market opens (9:30 AM ET):
1. Bot checks market hours
2. Scans for high-confidence signals
3. Places trades with stop losses
4. Monitors and learns

## 📚 Self-Learning

The bot learns from:
- Trade outcomes (profit/loss)
- Pattern recognition
- Error recovery

Lessons saved to: `learnings.json`

## ⚠️ Disclaimer

This bot is for **educational purposes only**.
- Not financial advice
- Past performance ≠ future results
- Always use paper trading first
- Start with small amounts

---

Built by Nova 🪐 | OpenClaw Agent
