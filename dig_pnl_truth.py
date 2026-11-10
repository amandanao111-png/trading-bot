#!/usr/bin/env python3
"""Dig: get the FULL Alpaca order history (proper pagination) + reconcile equity.

The earlier reconcile_pnl.py broke out of its pagination loop after one page
(500 orders), so it only saw the oldest 500. This walks every page.
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

acct = requests.get(f'{BASE}/account', headers=H, timeout=10).json()
eq = float(acct.get('equity', 0))
print(f'Equity ${eq:,.2f}  cash ${float(acct.get("cash",0)):,.2f}')

# ---- FULL order pagination ----
print('\nFetching FULL order history (paginated)...')
all_orders = []
cursor = None
pages = 0
while True:
    p = {'status': 'all', 'direction': 'asc', 'limit': 500}
    if cursor:
        p['page_token'] = cursor
    r = requests.get(f'{BASE}/orders', headers=H, params=p, timeout=25)
    if r.status_code != 200:
        print(f'  page {pages} failed: {r.status_code} {r.text[:120]}')
        break
    batch = r.json()
    if not batch:
        break
    all_orders.extend(batch)
    pages += 1
    cursor = batch[-1].get('id') if batch else None
    if len(batch) < 500:
        break
    if pages > 20:
        break

filled = [o for o in all_orders if o.get('status') == 'filled' and o.get('filled_at')]
filled.sort(key=lambda o: o['filled_at'])
print(f'  pages={pages}  orders={len(all_orders)}  filled={len(filled)}')
if filled:
    print(f'  first fill: {filled[0]["filled_at"][:10]}   last fill: {filled[-1]["filled_at"][:10]}')

# ---- position-aware FIFO (long + short) ----
book = defaultdict(list)
closed = []
for o in filled:
    sym = o['symbol']
    qty = float(o['qty'])
    px = float(o['filled_avg_price'])
    if o['side'] == 'buy':
        while qty > 0 and book[sym] and book[sym][0][0] < 0:
            lot = book[sym][0]
            take = min(qty, -lot[0])
            closed.append({'sym': sym, 'qty': take, 'entry': lot[1], 'exit': px,
                           'pnl': (px - lot[1]) * take, 'date': o['filled_at'][:10], 'side': 'short'})
            lot[0] += take
            qty -= take
            if abs(lot[0]) < 1e-9:
                book[sym].pop(0)
        if qty > 0:
            book[sym].append([qty, px, o['filled_at'][:10]])
    else:
        while qty > 0 and book[sym] and book[sym][0][0] > 0:
            lot = book[sym][0]
            take = min(qty, lot[0])
            closed.append({'sym': sym, 'qty': take, 'entry': lot[1], 'exit': px,
                           'pnl': (px - lot[1]) * take, 'date': o['filled_at'][:10], 'side': 'long'})
            lot[0] -= take
            qty -= take
            if abs(lot[0]) < 1e-9:
                book[sym].pop(0)
        if qty > 0:
            book[sym].append([-qty, px, o['filled_at'][:10]])

pnls = [c['pnl'] for c in closed]
w = [p for p in pnls if p > 0]
l = [-p for p in pnls if p <= 0]
print()
print('=' * 70)
print(f'FULL-HISTORY REALISED: {len(closed)} round-trips, {len(w)}W/{len(l)}L, '
      f'{len(w)/max(1,len(closed))*100:.1f}% WR, ${sum(pnls):+,.2f}')
if w:
    print(f'  avg win ${sum(w)/len(w):,.2f}   avg loss ${-sum(l)/len(l):,.2f}' if l else '')
print(f'  profit factor {(sum(w)/sum(l)) if sum(l) else 0:.2f}')

open_lots = {s: [(round(l[0]), round(l[1], 2), l[2]) for l in v if abs(l[0]) > 1e-9]
              for s, v in book.items() if any(abs(l[0]) > 1e-9 for l in v)}
print(f'\n  STILL-OPEN LOTS: {open_lots}')

# Equity reconciliation: what was the starting balance?
print()
print('=' * 70)
print('EQUITY RECONCILIATION')
print('=' * 70)
print(f'  realised (full hist)  ${sum(pnls):>+12,.2f}')
print(f'  equity now           ${eq:>12,.2f}')
print(f'  => implied start     ${eq - sum(pnls):>12,.2f}')

# journal comparison
journal = json.load(open(f'{TB}/trade_journal.json'))
jc = [t for t in journal if t.get('closed')]
ph = [t for t in jc if 'CLEANUP' in str(t.get('exit_reason', ''))]
real = [t for t in jc if 'CLEANUP' not in str(t.get('exit_reason', ''))]
rp = [t.get('pnl', 0) for t in real]
rw = sum(1 for p in rp if p > 0)
print(f'\n  journal closed: {len(jc)}  (phantoms {len(ph)})')
print(f'  journal minus phantoms: {len(real)} closed, {rw}W/{len(real)-rw}L, ${sum(rp):+,.2f}')
print(f'  journal coverage of Alpaca round-trips: {len(real)}/{len(closed)}')

# which journal rows are missing?
jkeys = set()
for t in real:
    jkeys.add((t.get('timestamp', '')[:10], t.get('symbol'), round(t.get('price', 0), 2)))
cbydate = defaultdict(list)
for c in closed:
    cbydate[(c['date'], c['sym'])].append(c)
missing = []
for c in closed:
    found = False
    for t in real:
        if t.get('symbol') == c['sym'] and t.get('timestamp', '')[:10] == c['date']:
            found = True
            break
    if not found:
        missing.append(c)
print(f'\n  Alpaca round-trips with NO journal row: {len(missing)}')
bysym = defaultdict(list)
for c in missing:
    bysym[c['sym']].append(c)
for s, cs in sorted(bysym.items(), key=lambda x: -len(x[1]))[:12]:
    tot = sum(c['pnl'] for c in cs)
    print(f'    {s:6s} n={len(cs):>2}  ${tot:>+9,.2f}   dates={cs[0]["date"]}..{cs[-1]["date"]}')

print(f'\n  TOTAL MISSING REALISED P&L: ${sum(c["pnl"] for c in missing):+,.2f}')
print(f'  journal-minus-phantoms + missing = ${sum(rp) + sum(c["pnl"] for c in missing):+,.2f}')
