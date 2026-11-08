#!/bin/bash
# Restart the real trading bot entry point if it is not alive.
set -u
cd /Users/amandaai/.openclaw/workspace/trading_bot || exit 1
source venv/bin/activate

BOT_PID=$(pgrep -f "python.*auto_trade.py" | head -1)
if [ -z "$BOT_PID" ]; then
    echo "[$(date)] Bot dead — restarting auto_trade.py..." >> watchdog.log
    nohup python -u auto_trade.py >> trades.log 2>&1 &
    sleep 2
    NEW_PID=$(pgrep -f "python.*auto_trade.py" | head -1)
    if [ -z "$NEW_PID" ]; then
        echo "[$(date)] Restart FAILED — auto_trade.py did not stay alive" >> watchdog.log
        exit 1
    fi
    echo "[$(date)] Restarted auto_trade.py (PID $NEW_PID)" >> watchdog.log
fi
