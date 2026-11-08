#!/bin/bash
# run_realtime.sh — Realtime trading loop for ai.nova.realtime-trading
# Was missing file causing launches to fail. Restored 2026-07-03 03:13 BST.

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
source venv/bin/activate 2>/dev/null

# Load .env
set -a
source .env 2>/dev/null || true
set +a

exec python -u auto_trade.py
