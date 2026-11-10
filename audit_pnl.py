#!/usr/bin/env python3
"""Audited trading P&L — reconciles the two books (learned_stats vs raw journal)."""
import os
import json
import requests
from collections import Counter
from datetime import datetime, timezone
from dotenv import load_dotenv

TB = '/Users/amandaai/.openclaw/workspace/trading_bot'
load_dotenv(f'{TB}/.env', override=True)
H = {'APCA-API-KEY-ID': os.environ.get('ALPACA_API_KEY', ''),
     'APCA-API-SECRET-KEY': os.environ.get('ALPACA_API_SECRET', '')}

print('=' * 72)
print('LIVE ALPACA (paper)')
print('=' * 72)
acct = requests.get('https://paper-api.alpaca.markets/v2/account', headers=H, timeout=10).json()
eq = float(acct.get('equity', 0))
cash = float(acct.get('cash', 0))
print(f'  Equity:       ${eq:,.2f}')
print(f'  Cash:         ${cash:,.2f}  ({cash/eq*100:.0f}% cash)')
print(f'  Buying power: ${float(acct.get("buying_power",0)):,.2f}')
pos = requests.get('https://paper-api.alpaca.markets/v2/positions', headers=H, timeout=10).json()
print(f'  Positions:    {len(pos)}')

print()
print('=' * 72)
print('REALISED P&L — from Alpaca order history (ground truth)')
print('=' * 72)
orders = requests.get('https://paper-api.alpaca.markets/v2/orders', headers=H, timeout=15,
                      params={'status': 'all', 'limit': 500, 'direction': 'asc'}).json()
filled = [o for o in orders if o.get('status') == 'filled' and o.get('filled_at')]

# FIFO-matched realised P&L per symbol
lots = {}
realised = []
for o in filled:
    sym = o['symbol']
    qty = float(o['qty'])
    px = float(o['filled_avg_price'])
    side = o['side']
    if side == 'buy':
        lots.setdefault(sym, []).append([qty, px, o['filled_at'][:10]])
    else:
        remaining = qty
        while remaining > 0 and lots.get(sym):
            lot = lots[sym][0]
            take = min(remaining, lot[0])
            pnl = (px - lot[1]) * take
            realised.append({'symbol': sym, 'qty': take, 'pnl': pnl,
                            'entry': lot[1], 'exit': px,
                            'date': o['filled_at'][:10], 'held': lot[2]})
            lot[0] -= take
            remaining -= take
            if lot[0] <= 0:
                lots[sym].pop(0)

total_real = sum(r['pnl'] for r in realised)
wins_r = [r for r in realised if r['pnl'] > 0]
loss_r = [r for r in realised if r['pnl'] <= 0]
print(f'  Closed fills matched: {len(realised)}')
print(f'  Wins/Losses: {len(wins_r)}/{len(loss_r)}')
print(f'  Win rate:    {len(wins_r)/len(realised)*100:.1f}%' if realised else '  n/a')
print(f'  Avg win:     ${sum(r["pnl"] for r in wins_r)/len(wins_r):,.2f}' if wins_r else '')
print(f'  Avg loss:    ${sum(r["pnl"] for r in loss_r)/len(loss_r):,.2f}' if loss_r else '')
print(f'  REALISED:    ${total_real:+,.2f}')

print()
print('  By symbol:')
bysym = {}
for r in realised:
    bysym.setdefault(r['symbol'], []).append(r['pnl'])
for sym, ps in sorted(bysym.items(), key=lambda x: -sum(x[1])):
    w = sum(1 for p in ps if p > 0)
    print(f'    {sym:6s} n={len(ps):>2} {w}W/{len(ps)-w}L  ${sum(ps):+9,.2f}')

print()
print('  Last 12 closed fills:')
for r in realised[-12:]:
    print(f"    {r['date']}  {r['symbol']:6s}  qty={r['qty']:>6.0f}  {r['entry']:>8.2f} -> {r['exit']:>8.2f}  ${r['pnl']:+9,.2f}")

print()
print('=' * 72)
print('THE TWO BOOKS — reconciled')
print('=' * 72)
stats = json.load(open(f'{TB}/learned_stats.json'))
print(f"  learned_stats.json (what the bot prints): {stats.get('total_trades')} trades, "
      f"{stats.get('winning_trades')}W/{stats.get('losing_trades')}L, "
      f"{stats.get('winning_trades')/(stats.get('winning_trades',1)+stats.get('losing_trades',1))*100:.1f}% WR, "
      f"${stats.get('total_pnl',0):+,.2f}")

journal = json.load(open(f'{TB}/trade_journal.json'))
closed = [t for t in journal if t.get('closed')]
phantoms = [t for t in closed if 'CLEANUP' in str(t.get('exit_reason', ''))]
real = [t for t in closed if 'CLEANUP' not in str(t.get('exit_reason', ''))]
rp = [t.get('pnl', 0) for t in real]
rw = sum(1 for p in rp if p > 0)
rl = sum(1 for p in rp if p <= 0)
print(f"  journal minus CLEANUP rows (audited):   {len(real)} closed, "
      f"{rw}W/{rl}L, {rw/len(real)*100:.1f}% WR, ${sum(rp):+,.2f}")
print(f"  CLEANUP phantom rows inflating the printed book: {len(phantoms)}")

print()
print('  Unclosed SELL rows (never finalised):')
openc = [t for t in journal if not t.get('closed')]
print(f'    {len(openc)} open rows: {dict(Counter(t.get("symbol","?") for t in openc))}')

print()
print('=' *72)
print('DRAWDOWN / PEAK')
print('='*72)
eqs = [r['exit'] for r in realised]
print(f'  Equity now:  ${eq:,.2f}')
print(f'  Realised:    ${total_real:+,.2f}')
print(f'  Implied peak (equity - realised): ${eq - total_real:,.2f}')
print(f'  Drawdown from implied peak: ${eq - (eq - total_real):,.2f} ({(eq/(eq-total_real)-1)*100:+.2f}%)')

print()
print('='*72)
print('BOT STATUS')
print('='*72)
out = os.popen('pgrep -fla auto_trade.py').read()
procs = [l for l in out.split('\n') if l.strip() and 'grep' not in l]
print(f'  Instances: {len(procs)}')
for p in procs:
    pid = p.split()[0]
    et = os.popen(f'ps -o etime= -p {pid}').read().strip()
    print(f'    PID {pid}  uptime {et}')

print()
print('  Source-file mtimes vs bot start (running pre-fix code?):')
for f in ['auto_trade.py', 'event_guard.py', 'earnings_sentiment.py', 'evolution.py']:
    p = f'{TB}/{f}'
    if os.path.exists(p):
        print(f'    {f:24s} {datetime.fromtimestamp(os.path.getmtime(p)):%Y-%m-%d %H:%M}')
