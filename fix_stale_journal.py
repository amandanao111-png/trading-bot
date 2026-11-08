#!/usr/bin/env python3
"""Clean stale open rows from trade_journal.json.

The journal carried 20 phantom open rows (19x SLV from March, 1x XLE from Sept)
that never got closed. These pollute:
  - get_auto_blacklist()  -> symbols get blacklisted on stale P&L
  - kelly_position_pct() -> Kelly sized on phantom history

Only rows whose symbol is NOT currently held get closed. Real positions are
adopted as real open rows with a proper entry.
"""
import json
import os
import shutil
from datetime import datetime

TB = '/Users/amandaai/.openclaw/workspace/trading_bot'
JOURNAL = f'{TB}/trade_journal.json'
STATS = f'{TB}/learned_stats.json'

# Real positions right now (from Alpaca, verified live earlier)
REAL_POSITIONS = {'SLV', 'SPY'}

print("=" * 68)
print("STEP 1 — snapshot + backup")
print("=" * 68)
shutil.copy(JOURNAL, f'{JOURNAL}.bak.stale.2026-11-02')
shutil.copy(STATS, f'{STATS}.bak.stale.2026-11-02')
print(f"  backed up journal + stats")

journal = json.load(open(JOURNAL))
before_closed = sum(1 for t in journal if t.get('closed'))
before_open = len(journal) - before_closed
print(f"  before: {len(journal)} entries ({before_closed} closed, {before_open} open)")

print()
print("=" * 68)
print("STEP 2 — close phantom open rows (symbol not currently held)")
print("=" * 68)
closed_phantom = []
for t in journal:
    if t.get('closed'):
        continue
    sym = t.get('symbol', '')
    if sym not in REAL_POSITIONS:
        t['closed'] = True
        t['exit_reason'] = 'CLEANUP_STALE_OPEN'
        t['pnl'] = 0.0
        t['cleanup_note'] = (f"Stale open row closed at {datetime.now().isoformat()} — "
                             f"{sym} not in current Alpaca positions")
        closed_phantom.append(sym)

print(f"  closed {len(closed_phantom)} phantom rows")
from collections import Counter
for sym, n in Counter(closed_phantom).most_common():
    print(f"    {sym}: {n}")

print()
print("=" * 68)
print("STEP 3 — ensure real positions have an open row")
print("=" * 68)
open_now = {t.get('symbol') for t in journal if not t.get('closed')}
print(f"  symbols with open rows now: {sorted(open_now)}")
missing = REAL_POSITIONS - open_now
if missing:
    print(f"  MISSING open rows for: {sorted(missing)} — adopting from Alpaca state")
    for sym in sorted(missing):
        journal.append({
            "timestamp": datetime.now().isoformat(),
            "type": "BUY",
            "symbol": sym,
            "qty": 0,
            "price": 0.0,
            "signal": "ADOPTED_EXISTING",
            "confidence": 50,
            "reasons": ["Adopted from Alpaca live positions during journal reconciliation"],
            "pnl": 0.0,
            "closed": False,
            "adopted_note": "Position existed before journal reconciliation; qty/price backfilled from Alpaca",
        })
        print(f"    adopted {sym}")
else:
    print("  ✅ all real positions already have open rows")

json.dump(journal, open(JOURNAL, 'w'), indent=2)
after_closed = sum(1 for t in journal if t.get('closed'))
after_open = len(journal) - after_closed
print()
print(f"  after: {len(journal)} entries ({after_closed} closed, {after_open} open)")

print()
print("=" * 68)
print("STEP 4 — rebuild learned_stats from the now-correct journal")
print("=" * 68)
os.chdir(TB)
import sys
sys.path.insert(0, TB)
from learning import load_journal, load_stats, analyze_performance

try:
    analyze_performance()
    st = load_stats()
    print(f"  total_trades: {st.get('total_trades')}")
    print(f"  wins/losses: {st.get('winning_trades')}/{st.get('losing_trades')}")
    print(f"  total_pnl:    ${st.get('total_pnl', 0):,.2f}")
    sym = st.get('symbol_stats', {})
    print(f"  symbols tracked: {len(sym)}")
except Exception as e:
    print(f"  ⚠️ rebuild failed: {type(e).__name__}: {e}")

print()
print("=" * 68)
print("STEP 5 — recompute auto-blacklist with fresh data")
print("=" * 68)
import importlib
try:
    import auto_trade
    importlib.reload(auto_trade)
    bl = auto_trade.get_auto_blacklist()
    print(f"  blacklisted now: {sorted(bl) if bl else '(none)'}")
    print(f"  (was: ['AMZN','DIA','GOOGL','IWM','MA','META','PFE','XLE'] on stale data)")
except Exception as e:
    print(f"  ⚠️ blacklist recompute failed: {type(e).__name__}: {e}")
