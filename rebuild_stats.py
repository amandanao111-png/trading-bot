#!/usr/bin/env python3
"""Rebuild learned_stats.json from trade_journal.json.

The bot's printed record (166 trades / 65W / 59.1% / -$3,641) was inflated by
103 CLEANUP phantom rows: closed=True, pnl=0.0. record_trade() counts pnl=0.0
as a LOSS (the test is pnl > 0), so every phantom dragged the win rate down
while the trade count went up.

Rebuild from the journal, excluding CLEANUP rows. Target (2026-11-10 brief):
71 closed / 30W / 41L / 42.3% WR / -$5,966.32.

Note on signal_stats: the fresh journal records each closed trade as a single
row with type=BUY + pnl attached (the v6-era BUY/SELL pair schema is gone),
and those rows carry no exit_reason. signal_stats is therefore keyed by the
entry signal rather than the old exit-reason key.
"""
import json
import shutil
from datetime import datetime

TB = '/Users/amandaai/.openclaw/workspace/trading_bot'
JOURNAL = f'{TB}/trade_journal.json'
STATS = f'{TB}/learned_stats.json'
BAK = f'{STATS}.bak.2026-11-10'

with open(JOURNAL) as f:
    journal = json.load(f)

closed = [t for t in journal if t.get('closed')]
real = [t for t in closed if 'CLEANUP' not in str(t.get('exit_reason', ''))]
phantoms = [t for t in closed if 'CLEANUP' in str(t.get('exit_reason', ''))]

stats = {
    'total_trades': 0,
    'winning_trades': 0,
    'losing_trades': 0,
    'total_pnl': 0.0,
    'signal_stats': {},
    'symbol_stats': {},
    'optimizations': [],
}

for t in real:
    pnl = float(t.get('pnl') or 0.0)
    sym = t.get('symbol', '?')
    sig = str(t.get('exit_reason') or t.get('signal') or 'UNKNOWN')
    win = pnl > 0

    stats['total_trades'] += 1
    if win:
        stats['winning_trades'] += 1
    else:
        stats['losing_trades'] += 1
    stats['total_pnl'] += pnl

    ss = stats['symbol_stats'].setdefault(sym, {'total': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0})
    ss['total'] += 1
    if win:
        ss['wins'] += 1
    else:
        ss['losses'] += 1
    ss['pnl'] += pnl

    gs = stats['signal_stats'].setdefault(sig, {'total': 0, 'wins': 0, 'losses': 0})
    gs['total'] += 1
    if win:
        gs['wins'] += 1
    else:
        gs['losses'] += 1

# preserve the optimizations list from the old file
try:
    with open(STATS) as f:
        old = json.load(f)
    stats['optimizations'] = old.get('optimizations', [])
except Exception:
    pass

shutil.copy(STATS, BAK)
with open(STATS, 'w') as f:
    json.dump(stats, f, indent=2)

w = stats['winning_trades']
l = stats['losing_trades']
print(f"Rebuilt learned_stats.json  (backup: {BAK.split('/')[-1]})")
print(f"  {stats['total_trades']} trades, {w}W/{l}L, {w / max(1, w + l) * 100:.1f}% WR, ${stats['total_pnl']:+,.2f}")
print(f"  target: 71 trades, 30W/41L, 42.3% WR, $-5,966.32")
print(f"  MATCH:  {stats['total_trades'] == 71 and w == 30 and l == 41 and abs(stats['total_pnl'] + 5966.32) < 0.01}")
print(f"  excluded {len(phantoms)} CLEANUP phantom rows")
print()
print(f"  signal_stats buckets: { {k: v['total'] for k, v in stats['signal_stats'].items()} }")
print(f"  symbols tracked: {len(stats['symbol_stats'])}")
print()
print("  top symbols by P&L:")
for sym, s in sorted(stats['symbol_stats'].items(), key=lambda x: -x[1]['pnl'])[:6]:
    print(f"    {sym:6s} n={s['total']:>2} {s['wins']}W/{s['losses']}L  ${s['pnl']:>+8.2f}")
