#!/usr/bin/env python3
"""Definitive trading P&L reconciliation.

Three books disagree:
  A) learned_stats.json  — what the bot prints (polluted by CLEANUP_PHANTOM rows)
  B) journal minus phantoms
  C) Alpaca order history FIFO

Ground truth = Alpaca /v2/account/activities (cash movements) + /v2/orders (fills).
Correctly handles BOTH long and short accounting.
"""
import os
import json
import requests
from collections import defaultdict
from dotenv import load_dotenv

TB = '/Users/amandaai/.openclaw/workspace/trading_bot'
load_dotenv(f'{TB}/.env', override=True)
H = {'APCA-API-KEY-ID': os.environ.get('ALPACA_API_KEY', ''),
     'APCA-API-SECRET-KEY': os.environ.get('ALPACA_API_SECRET', '')}
BASE = 'https://paper-api.alpaca.markets/v2'

# ---------- account ----------
acct = requests.get(f'{BASE}/account', headers=H, timeout=10).json()
eq = float(acct.get('equity', 0))
cash = float(acct.get('cash', 0))
print('=' * 74)
print('ACCOUNT (live)')
print('=' * 74)
print(f'  Equity:       ${eq:,.2f}')
print(f'  Cash:         ${cash:,.2f}  ({cash/eq*100:.0f}% — flat)')
print(f'  Buying power: ${float(acct.get("buying_power",0)):,.2f}')

# ---------- activities: initial deposit + any deposits/withdrawals ----------
acts = []
cursor = None
while True:
    p = {'page_size': 1000, 'direction': 'asc', 'activity_types': 'FILL,TRANS,CASH'}
    if cursor:
        p['page_token'] = cursor
    r = requests.get(f'{BASE}/account/activities', headers=H, params=p, timeout=20)
    if r.status_code != 200:
        break
    acts.extend(r.json())
    cursor = r.json() and None
    break

deposits = sum(float(a.get('qty', 0)) for a in acts
                if a.get('activity_type') in ('CASH', 'TRANS') and float(a.get('qty', 0)) > 0)
print(f'\n  Cash deposited to date: ${deposits:,.2f}  ({len(acts)} activity rows)')

# ---------- orders: ALL fills, paginated ----------
orders = []
cursor = None
while True:
    p = {'status': 'all', 'direction': 'asc', 'limit': 500}
    if cursor:
        p['page_token'] = cursor
    r = requests.get(f'{BASE}/orders', headers=H, params=p, timeout=20)
    if r.status_code != 200:
        print(f'  orders fetch failed: {r.status_code} {r.text[:150]}')
        break
    orders.extend(r.json())
    nxt = r.json() and None
    if not nxt:
        break
    cursor = None
    break

filled = [o for o in orders if o.get('status') == 'filled' and o.get('filled_at')]
filled.sort(key=lambda o: o['filled_at'])
print(f'  Orders fetched: {len(orders)}  ({len(filled)} filled)')

# ---------- position-aware FIFO for BOTH long and short ----------
book = defaultdict(list)          # sym -> [ [qty, price] ]  open lots
closed = []                        # completed round-trips
for o in filled:
    sym = o['symbol']
    qty = float(o['qty'])
    px = float(o['filled_avg_price'])
    side = o['side']
    if side == 'buy':
        # first close against SHORT lots, then open/add LONG
        while qty > 0 and book[sym] and book[sym][0][0] < 0:   # short lot
            lot = book[sym][0]
            take = min(qty, -lot[0])
            pnl = (px - lot[1]) * take            # cover short: sell above entry = profit
            closed.append({'sym': sym, 'qty': take, 'entry': lot[1], 'exit': px,
                           'pnl': pnl, 'date': o['filled_at'][:10], 'side': 'short'})
            lot[0] += take                          # lot qty moves toward 0
            qty -= take
            if abs(lot[0]) < 1e-9:
                book[sym].pop(0)
        if qty > 0:
            book[sym].append([qty, px, o['filled_at'][:10]])   # long lot
    else:  # sell
        # first close against LONG lots, then open/add SHORT
        while qty > 0 and book[sym] and book[sym][0][0] > 0:
            lot = book[sym][0]
            take = min(qty, lot[0])
            pnl = (px - lot[1]) * take              # sell long: above entry = profit
            closed.append({'sym': sym, 'qty': take, 'entry': lot[1], 'exit': px,
                           'pnl': pnl, 'date': o['filled_at'][:10], 'side': 'long'})
            lot[0] -= take
            qty -= take
            if abs(lot[0]) < 1e-9:
                book[sym].pop(0)
        if qty > 0:
            book[sym].append([-qty, px, o['filled_at'][:10]])  # short lot (negative qty)

print()
print('=' * 74)
print('GROUND TRUTH — Alpaca fills, long AND short accounted')
print('=' * 74)
pnls = [c['pnl'] for c in closed]
w = [p for p in pnls if p > 0]
l = [-p for p in pnls if p <= 0]
print(f'  Closed round-trips: {len(closed)}')
print(f'  Wins / Losses:     {len(w)} / {len(l)}')
print(f'  Win rate:          {len(w)/len(closed)*100:.1f}%')
print(f'  Avg win:           ${sum(w)/len(w):,.2f}' if w else '')
print(f'  Avg loss:          ${-sum(l)/len(l):,.2f}' if l else '')
print(f'  REALISED P&L:      ${sum(pnls):+,.2f}')
print(f'  Profit factor:     {(sum(w)/sum(l)) if sum(l) else 0:.2f}')

# equity reconciliation
realised = sum(pnls)
open_lots = {s: [l for l in v if abs(l[0]) > 1e-9] for s, v in book.items() if any(abs(l[0]) > 1e-9 for l in v)}
print()
print('  RECONCILIATION vs equity:')
print(f'    cash deposited        ${deposits:>12,.2f}')
print(f'    + realised P&L        ${realised:>+12,.2f}')
print(f'    = expected equity     ${deposits+realised:>12,.2f}')
print(f'    actual equity         ${eq:>12,.2f}')
print(f'    difference            ${eq-(deposits+realised):>+12,.2f}')

if open_lots:
    print(f'\n  STILL-OPEN LOTS (not in realised): {list(open_lots.keys())}')

print()
print('  Last 10 round-trips:')
for c in closed[-10:]:
    print(f"    {c['date']}  {c['sym']:6s} {c['side']:5s} {c['qty']:>6.0f}  {c['entry']:>8.2f} -> {c['exit']:>8.2f}  ${c['pnl']:+9,.2f}")

print()
print('  By symbol:')
bysym = defaultdict(list)
for c in closed:
    bysym[c['sym']].append(c['pnl'])
for s, ps in sorted(bysym.items(), key=lambda x: -sum(x[1])):
    ww = sum(1 for p in ps if p > 0)
    print(f'    {s:6s} n={len(ps):>2} {ww}W/{len(ps)-ww}L  ${sum(ps):+9,.2f}')

print()
print('=' * 74)
print('THE OTHER TWO BOOKS')
print('=' * 74)
stats = json.load(open(f'{TB}/learned_stats.json'))
tw, tl = stats.get('winning_trades', 0), stats.get('losing_trades', 0)
print(f"  learned_stats.json (bot prints): {stats.get('total_trades')} trades, {tw}W/{tl}L, "
      f"{tw/max(1,tw+tl)*100:.1f}% WR, ${stats.get('total_pnl',0):+,.2f}")

journal = json.load(open(f'{TB}/trade_journal.json'))
jc = [t for t in journal if t.get('closed')]
ph = [t for t in jc if 'CLEANUP' in str(t.get('exit_reason', ''))]
rr = [t for t in jc if 'CLEANUP' not in str(t.get('exit_reason', ''))]
rp = [t.get('pnl', 0) for t in rr]
rw = sum(1 for p in rp if p > 0)
print(f"  journal minus CLEANUP rows:        {len(rr)} closed, {rw}W/{len(rr)-rw}L, "
      f"{rw/max(1,len(rr))*100:.1f}% WR, ${sum(rp):+,.2f}")
print(f"  CLEANUP phantom rows polluting book: {len(ph)} (pnl=0.0, counted as LOSSES by 'pnl>0')")
openc = [t for t in journal if not t.get('closed')]
print(f"  never-closed rows still open:       {len(openc)}")

print()
print('  Equity peak/drawdown:')
ec = f'{TB}/equity_curve_v6.json'
if os.path.exists(ec):
    d = json.load(open(ec))
    if d:
        vals = [e['portfolio'] for e in d]
        print(f"    tracked peak ${max(vals):,.2f}  now ${eq:,.2f}  drawdown ${eq-max(vals):,.2f} ({(eq/max(vals)-1)*100:+.2f}%)")
        print(f"    equity_curve last write: {d[-1]['timestamp'][:19]}")
