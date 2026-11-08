#!/bin/bash
# Keep the real trading bot alive.  The single-instance flock in
# auto_trade.py means a second process exits cleanly; do not loop on that case.
cd /Users/amandaai/.openclaw/workspace/trading_bot || exit 1
source venv/bin/activate

while true; do
    if pgrep -f "python.*auto_trade.py" >/dev/null 2>&1; then
        echo "[$(date)] auto_trade.py already running — no second instance" >> watchdog.log
        sleep 60
        continue
    fi
    echo "[$(date)] Starting auto_trade.py..." >> watchdog.log
    python -u auto_trade.py >> trades.log 2>&1
    echo "[$(date)] Bot exited, restarting in 60s..." >> watchdog.log
    sleep 60
done
