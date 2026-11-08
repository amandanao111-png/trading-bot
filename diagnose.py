#!/usr/bin/env python3
"""Diagnose the trading-bot defect list. Read-only."""
import ast
import json
import os
import re
import subprocess
from collections import Counter

TB = '/Users/amandaai/.openclaw/workspace/trading_bot'

print("=" * 72)
print("1. ENTRY PATH — is place_order reachable? (unconditional continue check)")
print("=" * 72)
src = open(f'{TB}/auto_trade.py').read()
lines = src.split('\n')

# find the BUY branch
buy_line = None
for i, l in enumerate(lines):
    if 'elif s == "BUY" and c >= optimal_conf:' in l:
        buy_line = i
        break
print(f"  BUY branch starts at line {buy_line + 1}")

# find the place_order inside it
po_line = None
for i in range(buy_line, min(buy_line + 120, len(lines))):
    if 'place_order(symbol, qty, "buy")' in lines[i]:
        po_line = i
        break
print(f"  place_order(buy) at line {po_line + 1}")

# walk the branch, tracking indentation; find `continue` at the branch's top indent level
branch_indent = len(lines[buy_line]) - len(lines[buy_line].lstrip())
print(f"  branch base indent: {branch_indent} spaces")
print(f"  scanning for UNCONDITIONAL continue at indent {branch_indent}...")

unconditional = []
for i in range(buy_line + 1, po_line):
    l = lines[i]
    if not l.strip():
        continue
    indent = len(l) - len(l.lstrip())
    if l.strip().startswith('#'):
        continue
    if l.strip() == 'continue' and indent == branch_indent:
        unconditional.append((i + 1, l.strip()))
    # any deeper-indented continue is inside an if/try -> conditional, fine

if unconditional:
    print(f"  ❌ FOUND {len(unconditional)} UNCONDITIONAL continue(s) — DEAD CODE CONFIRMED:")
    for ln, t in unconditional:
        print(f"     line {ln}: {t}")
else:
    print("  ✅ NO unconditional continue — entry path IS reachable")

# what is the actual success message string?
print()
print("  Success print strings in BUY path:")
for i in range(buy_line, min(buy_line + 120, len(lines))):
    st = lines[i].strip()
    if 'print' in st and ('Bought' in st or 'BOUGHT' in st or '✅' in st):
        print(f"     line {i+1}: {st[:90]}")

print()
print("=" * 72)
print("2. JOURNAL POINTER — learning.py reads which file? bot writes which?")
print("=" * 72)
learn = open(f'{TB}/learning.py').read()
for l in learn.split('\n'):
    if 'JOURNAL_FILE' in l or 'STATS_FILE' in l:
        print(f"  learning.py: {l.strip()}")
print()
# what does auto_trade write?
for l in lines:
    if 'save_journal' in l or 'v7_journal' in l or 'JOURNAL' in l.upper() and '=' in l:
        print(f"  auto_trade.py: {l.strip()[:100]}")
        if 'save_journal' in l:
            break

print()
print("  File states:")
for f in ['trade_journal.json', 'v7_journal.json', 'learned_stats.json', 'v6_stats.json']:
    p = f'{TB}/{f}'
    if os.path.exists(p):
        import datetime
        mt = datetime.datetime.fromtimestamp(os.path.getmtime(p))
        try:
            d = json.load(open(p))
            n = len(d) if isinstance(d, list) else 'dict'
            closed = sum(1 for t in d if t.get('closed')) if isinstance(d, list) else '-'
            print(f"    {f:22s} mtime={mt:%Y-%m-%d %H:%M}  entries={n}  closed={closed}")
            if isinstance(d, list) and d:
                print(f"      first keys: {list(d[0].keys())[:9]}")
        except Exception as e:
            print(f"    {f:22s} mtime={mt:%Y-%m-%d %H:%M}  parse error: {e}")
    else:
        print(f"    {f:22s} MISSING")

print()
print("=" * 72)
print("3. STALE OPEN ROWS in trade_journal.json")
print("=" * 72)
tj = json.load(open(f'{TB}/trade_journal.json'))
op = [t for t in tj if not t.get('closed')]
print(f"  open rows: {len(op)}")
by_sym = Counter(t.get('symbol', '?') for t in op)
print(f"  by symbol: {dict(by_sym)}")
for t in op[:3]:
    print(f"    {t.get('timestamp','?')[:16]}  {t.get('symbol')}  type={t.get('type')}  conf={t.get('confidence')}")

print()
print("=" * 72)
print("4. DEAD CRON JOBS (MiniMax 429) + rebalancing staleness + log size")
print("=" * 72)
out = subprocess.run(['openclaw', 'cron', 'list'], capture_output=True, text=True).stdout
dead = [l for l in out.split('\n') if 'error' in l.lower() or '429' in l]
print(f"  cron lines mentioning error/429: {len(dead)}")
for l in dead[:10]:
    parts = l.split()
    if len(parts) > 3:
        print(f"    {parts[1]:28s} {parts[3] if len(parts)>3 else ''} {parts[-3] if len(parts)>3 else ''}")

for f in ['rebalancing_report.json', 'trades.log']:
    p = f'{TB}/{f}'
    if os.path.exists(p):
        import datetime
        mt = datetime.datetime.fromtimestamp(os.path.getmtime(p))
        age = (datetime.datetime.now() - mt).days
        sz = os.path.getsize(p) / 1024 / 1024
        print(f"  {f:26s} mtime={mt:%Y-%m-%d} age={age}d size={sz:.1f}MB")

print()
print("=" * 72)
print("5. BOT PROCESS + LOCKFILE")
print("=" * 72)
out = subprocess.run(['pgrep', '-fla', 'auto_trade'], capture_output=True, text=True).stdout
procs = [l for l in out.split('\n') if l.strip() and 'grep' not in l]
print(f"  auto_trade instances: {len(procs)}")
for p in procs:
    print(f"    {p.strip()[:95]}")
for lf in ['auto_trade.lock', 'bot.lock', '.lock']:
    p = f'{TB}/{lf}'
    if os.path.exists(p):
        print(f"  lockfile {lf}: present")
