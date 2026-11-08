#!/bin/bash
# run_evolution.sh — Daily self-improvement pass for trading bot
# Runs at 21:00 BST (after US market close) every day

cd /Users/amandaai/.openclaw/workspace/trading_bot
source venv/bin/activate

set -a
source .env
set +a

echo "=== $(date '+%Y-%m-%d %H:%M:%S') — Evolution Pass Starting ==="
python evolution.py 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') — Evolution Pass Complete ==="