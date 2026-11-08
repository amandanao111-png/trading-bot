#!/bin/bash
# run_bot.sh — Auto-trade launcher for V9 (auto_trade.py)
# Started by LaunchAgent ai.nova.trading-bot at 14:35 weekdays

cd /Users/amandaai/.openclaw/workspace/trading_bot

# Rotate stdout log if large (keep 3 generations)
for _lg in trades.log realtime.log; do
  if [ -f "$_lg" ] && [ "$(stat -f%z "$_lg" 2>/dev/null || echo 0)" -gt 20000000 ]; then
    for _i in 2 1; do
      [ -f "$_lg.$_i" ] && mv -f "$_lg.$_i" "$_lg.$((_i+1))"
    done
    mv -f "$_lg" "$_lg.1"
  fi
done
source venv/bin/activate

# Load env vars from .env
set -a
source .env
set +a

# Run V9 self-learning bot (loops every 30 min during market hours)
exec python -u auto_trade.py