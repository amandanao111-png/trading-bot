#!/bin/bash
cd /Users/amandaai/.openclaw/workspace/trading_bot
python3 signal_generator.py > signals_latest.txt
echo "Signals saved at $(date)" >> signals_latest.txt
