# Quick Reference

## Commands

```bash
# Run trading bot
python3 ~/.openclaw/workspace/trading_bot/auto_trade.py

# Check learning
python3 ~/.openclaw/workspace/trading_bot/learning.py analyze
python3 ~/.openclaw/workspace/trading_bot/learning.py report
python3 ~/.openclaw/workspace/trading_bot/learning.py optimal

# Check cron
openclaw cron list
openclaw cron run realtime-trade-monitor

# Disable cron
openclaw cron disable realtime-trade-monitor
```

## Indicators

| Indicator | Buy Signal | Sell Signal |
|-----------|------------|-------------|
| RSI | < 30 | > 70 |
| MACD | Bullish cross | Bearish cross |
| VWAP | Above | Below |
| Bollinger | At lower band | At upper band |

## Current Settings

- Max Position: 15%
- Min Confidence: 35 (auto-adjusted)
- Stop Loss: 2%
- Take Profit: 6%
- Trailing Stop: 8% → 4%

## Market Hours

- US: 9:30 AM - 4:00 PM ET
- UK: 14:30 - 21:00

## Contact

- Telegram: @Auragpx_bot
- Summary: @summary111_bot
- Security: @knowlegg_bot
