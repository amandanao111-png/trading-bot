#!/usr/bin/env python3
"""Check trading bot + Alpaca health without printing secrets."""
import sys, json, os, subprocess
sys.path.insert(0, '/Users/amandaai/.openclaw/workspace')

print("=== PROCESS HEALTH ===")
out = subprocess.run(['pgrep', '-fla', 'auto_trade'], capture_output=True, text=True).stdout
procs = [l for l in out.split('\n') if l.strip() and 'grep' not in l and 'bin/zsh' not in l and 'check_bot' not in l]
print(f"  auto_trade processes: {len(procs)}")
for p in procs:
    print(f"    {p.strip()[:90]}")

out = subprocess.run(['pgrep', '-fla', 'realtime'], capture_output=True, text=True).stdout
rt = [l for l in out.split('\n') if l.strip() and 'grep' not in l]
print(f"  realtime processes:  {len(rt)}")

print()
print("=== ALPACA API ===")
try:
    import requests
    from dotenv import load_dotenv
    load_dotenv('/Users/amandaai/.openclaw/workspace/.env', override=False)
    load_dotenv('/Users/amandaai/.openclaw/workspace/trading_bot/.env', override=True)
    ak = os.environ.get('ALPACA_API_KEY', '')
    sk = os.environ.get('ALPACA_API_SECRET', '') or os.environ.get('ALPACA_SECRET_KEY', '')
    if not ak or not sk:
        print("  ERROR: keys not found in env files")
    else:
        h = {'APCA-API-KEY-ID': ak, 'APCA-API-SECRET-KEY': sk}
        r = requests.get('https://paper-api.alpaca.markets/v2/account', headers=h, timeout=10)
        print(f"  account status: {r.status_code}")
        if r.status_code == 200:
            d = r.json()
            eq = float(d.get('equity', 0))
            print(f"  equity:    ${eq:,.2f}")
            print(f"  cash:      ${float(d.get('cash',0)):,.2f}")
            print(f"  day P&L:   ${eq - float(d.get('last_equity',0)):+.2f}")
        else:
            print(f"  body: {r.text[:200]}")
        r = requests.get('https://paper-api.alpaca.markets/v2/positions', headers=h, timeout=10)
        if r.status_code == 200:
            pos = r.json()
            print(f"  positions: {len(pos)}")
            for p in pos:
                sym = p['symbol']; qty = float(p['qty']); pl = float(p['unrealized_pl'])
                pc = float(p['unrealized_plpc'])*100
                side = 'SHORT' if qty < 0 else 'LONG'
                print(f"    {sym:6s} {side:5s} qty={qty:>7.1f}  entry=${float(p['avg_entry_price']):>7.2f}  now=${float(p['current_price']):>7.2f}  uPL=${pl:+.2f} ({pc:+.1f}%)")
except Exception as e:
    print(f"  ERROR: {e}")

print()
print("=== CRASH LOG FRESHNESS ===")
tb = '/Users/amandaai/.openclaw/workspace/trading_bot'
for f in ['trades.err.log', 'trades.log', 'realtime.log', 'bot_digest_latest.txt']:
    p = os.path.join(tb, f)
    if os.path.exists(p):
        import datetime
        m = datetime.datetime.fromtimestamp(os.path.getmtime(p))
        age_h = (datetime.datetime.now() - m).total_seconds()/3600
        flag = "  <-- STALE" if age_h > 24 else ""
        print(f"  {f:26s} mtime={m:%Y-%m-%d %H:%M}  age={age_h:.1f}h{flag}")
    else:
        print(f"  {f:26s} MISSING")

print()
print("=== LAST 3 ERR LINES ===")
p = os.path.join(tb, 'trades.err.log')
if os.path.exists(p):
    lines = [l for l in open(p).read().split('\n') if l.strip()][-3:]
    for l in lines:
        print(f"  {l[:100]}")