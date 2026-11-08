#!/usr/bin/env python3
"""Cleanup 120 phantom journal entries: mark closed=True for symbols not in current Alpaca positions."""
import sys
sys.path.insert(0, '/Users/amandaai/.openclaw/workspace')
from env_loader import require
import requests
import json
from datetime import datetime
import shutil
from collections import Counter

api_key = require('ALPACA_API_KEY')
secret = require('ALPACA_API_SECRET')
headers = {'APCA-API-KEY-ID': api_key, 'APCA-API-SECRET-KEY': secret}

# Get current positions
r = requests.get('https://paper-api.alpaca.markets/v2/positions', headers=headers, timeout=10)
current_positions = {p['symbol'] for p in r.json()} if r.status_code == 200 else set()
print(f"Current Alpaca positions: {current_positions}")

# Load journal
with open('/Users/amandaai/.openclaw/workspace/trading_bot/trade_journal.json') as f:
    journal = json.load(f)

phantom_count = 0
real_count = 0
cleaned_symbols = []
for entry in journal:
    if entry.get('closed'):
        continue
    sym = entry.get('symbol', '')
    if sym not in current_positions:
        entry['closed'] = True
        entry['exit_reason'] = 'CLEANUP_PHANTOM'
        entry['pnl'] = 0.0
        entry['cleanup_note'] = f"Phantom entry cleaned up at {datetime.now().isoformat()} — symbol not in current Alpaca positions"
        phantom_count += 1
        cleaned_symbols.append(sym)
    else:
        real_count += 1

# Backup before write
shutil.copy('/Users/amandaai/.openclaw/workspace/trading_bot/trade_journal.json',
            '/Users/amandaai/.openclaw/workspace/trading_bot/trade_journal.json.bak.2026-09-18')

with open('/Users/amandaai/.openclaw/workspace/trading_bot/trade_journal.json', 'w') as f:
    json.dump(journal, f, indent=2)

print(f"\nPhantom entries cleaned: {phantom_count}")
print(f"Real open entries kept: {real_count}")
print(f"Top cleaned symbols: {Counter(cleaned_symbols).most_common(5)}")

closed = [e for e in journal if e.get('closed')]
open_ = [e for e in journal if not e.get('closed')]
print(f"\n=== POST-CLEANUP STATE ===")
print(f"Closed: {len(closed)}, Open: {len(open_)}")
print(f"Open entries (should match Alpaca positions):")
for e in open_:
    print(f"  {e.get('symbol')}  type={e.get('type')}  conf={e.get('confidence')}")
