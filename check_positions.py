import json

with open('trade_journal.json') as f:
    trades = json.load(f)

# Get open positions (not closed)
positions = {}
for t in trades:
    if t.get('closed', False):
        continue
    sym = t['symbol']
    if t['type'] == 'BUY':
        if sym not in positions:
            positions[sym] = {'shares': 0, 'entry_price': 0, 'cost': 0}
        positions[sym]['shares'] += t['qty']
        positions[sym]['cost'] += t['qty'] * t['price']
    elif t['type'] == 'SELL':
        if sym in positions:
            positions[sym]['shares'] -= t['qty']
            if positions[sym]['shares'] <= 0:
                del positions[sym]

# Filter positive positions
positions = {k: v for k, v in positions.items() if v['shares'] > 0}

# Get realized P&L
total_pnl = sum(t.get('pnl', 0) for t in trades if t.get('closed', False))
cash = 10000

print("=== CURRENT PORTFOLIO ===")
print(f"Cash: ${cash:,.2f}")
print(f"Realized P&L: ${total_pnl:,.2f}")
print(f"\nOpen Positions ({len(positions)}):")
for sym, p in positions.items():
    avg_price = p['cost'] / p['shares']
    print(f"  {sym}: {p['shares']} shares @ ${avg_price:.2f} (${p['cost']:,.2f})")

pos_value = sum(p['cost'] for p in positions.values())
total_value = cash + pos_value
print(f"\nTotal Est. Value: ${total_value:,.2f}")
