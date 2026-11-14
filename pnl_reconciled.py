#!/usr/bin/env python3
"""Reconciled trading P&L for the Alpaca paper account.

Why this exists (2026-11-14):
  pnl_authoritative.py / pnl_truth_final.py disagreed by ~3,274 shares and
  reported $0.00 fees. Three concrete bugs, all found by probing the live API:

  1) `side=sell` on /v2/orders EXCLUDES short sells (type=sell_short).
     Those orders come back with side='sell' only when queried with
     side=sell_short, so a side='sell' sweep silently drops every short sale.
     -> 3,207 SLV shares sold went missing.
  2) Filtering orders by `status == 'filled'` drops CANCELED orders that had a
     partial fill (filled_qty>0, filled_at set). Those are real executions.
     -> 67 XLK shares bought went missing.
  3) /v2/account/activities rejects page_size=1000 (max 100) with HTTP 422,
     rejects activity_types=ACH, and returns FEE rows with `net_amount`
     (not `qty`). The old code asked for all three and so read 0 rows and
     $0.00 fees.

  This script takes FILL activities as the execution ledger (ground truth for
  what actually traded), cross-checks them against the orders endpoint, and
  HARD-FAILS if the flat-account balance test does not reconcile to zero.

Usage: python3 pnl_reconciled.py
"""
import os
import json
import time
import requests
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from dotenv import load_dotenv

TB = '/Users/amandaai/.openclaw/workspace/trading_bot'
load_dotenv(f'{TB}/.env', override=True)
H = {'APCA-API-KEY-ID': os.environ.get('ALPACA_API_KEY', ''),
     'APCA-API-SECRET-KEY': os.environ.get('ALPACA_API_SECRET', '')}
B = 'https://paper-api.alpaca.markets/v2'
CAP = 500
PROBLEMS = []


def get(path, **params):
    """GET with retry on non-200 / transport errors."""
    last = None
    for attempt in range(5):
        try:
            r = requests.get(f'{B}{path}', headers=H, timeout=30, params=params)
            if r.status_code == 200:
                return r.json()
            last = f'HTTP {r.status_code} {r.text[:160]}'
            if r.status_code == 422:      # bad params will not fix themselves
                break
        except Exception as e:
            last = repr(e)
        time.sleep(1.5 * (attempt + 1))
    PROBLEMS.append(f'{path} failed: {last}')
    return []


# ---------------------------------------------------------------- account
acct = get('/account')
if not acct:
    raise SystemExit('account fetch failed; aborting (no partial P&L)')
eq = float(acct.get('equity', 0))
cash = float(acct.get('cash', 0))
pos = get('/positions')
buying_power = float(acct.get('buying_power', 0))
day_pl = eq - float(acct.get('last_equity', 0))

# ------------------------------------------------- FILL activities (ledger)
# max page_size is 100; page_token continues the cursor.
fills = {}
cursor = None
for _ in range(80):
    p = {'direction': 'asc', 'page_size': 100, 'activity_types': 'FILL'}
    if cursor:
        p['page_token'] = cursor
    rows = get('/account/activities', **p)
    if not rows:
        break
    new = 0
    for a in rows:
        k = a.get('id')
        if k and k not in fills:
            fills[k] = a
            new += 1
    if new == 0:
        break
    cursor = rows[-1].get('id')
if not fills:
    raise SystemExit('no FILL activities fetched; refusing to print a P&L')

# ------------------------------------------------------- fees / deposits
acts = {}
cursor = None
for _ in range(80):
    p = {'direction': 'asc', 'page_size': 100}
    if cursor:
        p['page_token'] = cursor
    rows = get('/account/activities', **p)
    if not rows:
        break
    new = 0
    for a in rows:
        k = a.get('id')
        if k and k not in acts:
            acts[k] = a
            new += 1
    if new == 0:
        break
    cursor = rows[-1].get('id')
fees = sum(float(a.get('net_amount', 0) or 0) for a in acts.values()
           if a.get('activity_type') in ('FEE', 'CFEE', 'FEE_REFR'))
deposits = sum(float(a.get('net_amount', a.get('qty', 0)) or 0) for a in acts.values()
               if a.get('activity_type') in ('CSD', 'ACH'))
atypes = Counter(a.get('activity_type') for a in acts.values())

# ------------------------------------------------- orders endpoint (cross-check)
def fetch_orders(a, b, depth=0):
    rows = get('/orders', status='all', direction='asc', limit=CAP,
               after=a.isoformat() + 'Z', until=b.isoformat() + 'Z')
    if len(rows) >= CAP and depth < 6 and (b - a) > timedelta(seconds=1):
        mid = a + (b - a) / 2
        return fetch_orders(a, mid, depth + 1) + fetch_orders(mid, b, depth + 1)
    return rows

orders = {}
d = datetime(2026, 1, 1)
end = datetime.now() + timedelta(days=1)
while d < end:
    nxt = min(d + timedelta(days=31), end)
    for o in fetch_orders(d, nxt):
        if o.get('filled_at') or float(o.get('filled_qty') or 0) > 0:
            orders[o['id']] = o
    d = nxt

# order fills by side, including partial fills on canceled orders
ord_buy = defaultdict(float)
ord_sell = defaultdict(float)
ord_short = defaultdict(float)
for o in orders.values():
    q = float(o.get('filled_qty') or 0)
    if q <= 0:
        continue
    s = o.get('side')
    t = o.get('type') or ''
    if s == 'buy':
        ord_buy[o['symbol']] += q
    elif t == 'sell_short':
        ord_short[o['symbol']] += q
    else:
        ord_sell[o['symbol']] += q

# activity fills by side
act_buy = defaultdict(float)
act_sell = defaultdict(float)
act_short = defaultdict(float)
buy_notional = 0.0
sell_notional = 0.0
for a in fills.values():
    sym = a.get('symbol')
    q = float(a.get('qty') or 0)
    px = float(a.get('price') or 0)
    s = a.get('side')
    if s == 'buy':
        act_buy[sym] += q
        buy_notional += q * px
    elif s == 'sell':
        act_sell[sym] += q
        sell_notional += q * px
    elif s == 'sell_short':
        act_short[sym] += q
        sell_notional += q * px

buys = buy_notional
sells = sell_notional
cash_pnl = sells - buys

print('=' * 78)
print('RECONCILED P&L  (FILL activities = ledger, orders = cross-check)')
print('=' * 78)
print(f"  Equity:       ${eq:,.2f}")
print(f"  Cash:         ${cash:,.2f}  ({cash/eq*100:.1f}%)")
print(f"  Buying power: ${buying_power:,.2f}")
print(f"  Day P&L:      ${day_pl:,.2f}")
print(f"  Positions:    {len(pos)}" +
      (('  ' + ', '.join(f"{p['symbol']} {float(p['qty']):+.0f}" for p in pos)) if pos else '  (flat)'))
print(f"\n  FILL fills: {len(fills)}   orders with any fill: {len(orders)}")
print(f"  activities rows: {len(acts)}  types={dict(atypes)}")
print(f"  fees (net_amount): ${fees:,.2f}   deposits: ${deposits:,.2f}")

print('\n  PER-SYMBOL RECONCILIATION (activity vs orders)')
print('  symbol      act_buy act_sell act_short | ord_buy ord_sell ord_short | diff')
syms = sorted(set(act_buy) | set(act_sell) | set(act_short) |
              set(ord_buy) | set(ord_sell) | set(ord_short))
total_diff = 0.0
for s in syms:
    a_tot = act_buy[s] + act_sell[s] + act_short[s]
    o_tot = ord_buy[s] + ord_sell[s] + ord_short[s]
    diff = a_tot - o_tot
    total_diff += abs(diff)
    flag = '' if abs(diff) < 0.5 else '   <-- MISMATCH'
    print(f"  {s:8s} {act_buy[s]:9.0f} {act_sell[s]:8.0f} {act_short[s]:9.0f} | "
          f"{ord_buy[s]:8.0f} {ord_sell[s]:8.0f} {ord_short[s]:9.0f} | {diff:+.0f}{flag}")

print()
print('=' * 78)
print('CASH BASIS (exact for a flat account)')
print('=' * 78)
print(f"  gross buys  ${buys:>16,.2f}")
print(f"  gross sells ${sells:>16,.2f}")
print(f"  = realised  ${cash_pnl:>+16,.2f}")
print(f"  fees        ${fees:>+16,.2f}")
print(f"  NET        ${cash_pnl + fees:>+16,.2f}")

print()
print('=' * 78)
print('RECONCILIATION')
print('=' * 78)
implied_start = eq - cash_pnl
print(f"  equity now            ${eq:>15,.2f}")
print(f"  cash-basis realised   ${cash_pnl:>+13,.2f}")
print(f"  => implied start      ${implied_start:>15,.2f}")
print(f"  NET vs $100K paper start: ${eq - 100000:>+13,.2f}  ({(eq-100000)/100000*100:+.2f}%)")

# ---- HARD completeness test: flat account => every symbol's shares must net to 0
print()
print('=' * 78)
print('COMPLETENESS TEST (flat account: bought == sold per symbol)')
print('=' * 78)
net = {}
for s in syms:
    # a flat account must have bought exactly what it sold, per symbol
    n = act_buy[s] - act_sell[s] - act_short[s]
    net[s] = n
    if abs(n) > 0.5:
        print(f"  {s:8s} net shares {n:+9.0f}   <-- NOT RECONCILED")
if all(abs(v) < 0.5 for v in net.values()):
    print('  all symbols reconcile to zero  [PASS]')
else:
    PROBLEMS.append('flat-account share balance test FAILED')

if total_diff > 0.5:
    PROBLEMS.append(f'activity vs orders qty mismatch: {total_diff:.0f} shares')
if fees == 0 and len(acts) > 0:
    PROBLEMS.append('fees read as $0.00 despite activity rows present')

print()
if PROBLEMS:
    print('!! INCOMPLETE / UNVERIFIED:')
    for p in PROBLEMS:
        print('   -', p)
    print('   Do NOT quote lifetime P&L from this run.')
else:
    print('VERIFIED: fills complete, fees read, balance test passed.')

json.dump({'equity': eq, 'cash': cash, 'fees': fees, 'deposits': deposits,
           'cash_pnl': cash_pnl, 'fills': len(fills), 'orders': len(orders),
           'implied_start': implied_start, 'problems': PROBLEMS},
          open(f'{TB}/pnl_reconciled_last.json', 'w'), indent=1)
