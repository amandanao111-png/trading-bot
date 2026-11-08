#!/bin/bash
# Auto-trade launcher for the current bot entry point.
cd /Users/amandaai/.openclaw/workspace/trading_bot || exit 1
source venv/bin/activate
set -a
source .env
set +a
exec python -u auto_trade.py
