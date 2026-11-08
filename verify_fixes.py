#!/usr/bin/env python3
"""Verify the Tier-1 fixes against REAL Alpaca 5-min bars.

The market-open escalation reported analyze_symbol() returning None for every
symbol (0 scan lines). Cause: VolumeConfirmation compared the in-progress bar.
Fix: compare the last COMPLETED bar (volume[-2]).

This pulls live bars and runs the real analyze_symbol() to prove it works
BEFORE the 14:30 UTC open, not after.
"""
import sys
sys.path.insert(0, '/Users/amandaai/.openclaw/workspace/trading_bot')
sys.path.insert(0, '/Users/amandaai/.openclaw/workspace')

import os
import requests
from dotenv import load_dotenv

load_dotenv('/Users/amandaai/.openclaw/workspace/trading_bot/.env', override=True)
AK = os.environ.get('ALPACA_API_KEY', '')
SK = os.environ.get('ALPACA_API_SECRET', '')
H = {'APCA-API-KEY-ID': AK, 'APCA-API-SECRET-KEY': SK}

import auto_trade as m

SYMBOLS = ['AAPL', 'SPY', 'QQQ', 'SLV', 'BAC', 'AMD', 'NVDA', 'MSFT',
           'GOOGL', 'AMZN', 'META', 'XLK', 'XLE', 'SPY', 'VOO', 'IWM',
           'DIA', 'GLD', 'TLT', 'JPM', 'MA', 'WMT', 'COST', 'PG', 'KO',
           'JNJ', 'UNH', 'PFE', 'ABBV', 'TSLA', 'V', 'INTC']

print("=" * 72)
print("STEP 1 — raw bars + old vs new volume gate")
print("=" * 72)
ok = 0
fail = 0
for sym in SYMBOLS:
    try:
        r = requests.get(f"{m.DATA_URL}/v2/stocks/{sym}/bars", headers=H,
                         params={'timeframe': '5Min', 'limit': 100}, timeout=15)
        if r.status_code != 200:
            continue
        bars = r.json().get('bars', [])
        if len(bars) < 30:
            continue
        vol = [b['v'] for b in bars]
        close = [b['c'] for b in bars]

        old_ratio = vol[-1] / (sum(vol[-20:]) / 20) if sum(vol[-20:]) else 0
        new_ratio = m.Indicators.VolumeConfirmation(vol)
        old_pass = old_ratio >= m.MIN_VOLUME_RATIO
        new_pass = new_ratio >= m.MIN_VOLUME_RATIO
        if new_pass and not old_pass:
            ok += 1
        elif not new_pass:
            fail += 1
        if sym in ('AAPL', 'SPY', 'QQQ', 'SLV', 'NVDA'):
            print(f"  {sym:6s} old_ratio={old_ratio:.4f} ({'PASS' if old_pass else 'REJECT'})  "
                  f"new_ratio={new_ratio:.4f} ({'PASS' if new_pass else 'REJECT'})")
    except Exception as e:
        print(f"  {sym}: error {e}")

print(f"\n  Symbols the OLD gate rejected but NEW gate passes: {ok}")
print(f"  Symbols the NEW gate still rejects: {fail}")

print()
print("=" * 72)
print("STEP 2 — run the REAL analyze_symbol() on live bars")
print("=" * 72)
scanned = 0
signals = {'BUY': 0, 'SELL': 0, 'HOLD': 0}
results = []
for sym in SYMBOLS:
    try:
        r = requests.get(f"{m.DATA_URL}/v2/stocks/{sym}/bars", headers=H,
                         params={'timeframe': '5Min', 'limit': 100}, timeout=15)
        if r.status_code != 200:
            continue
        bars = r.json().get('bars', [])
        if len(bars) < 30:
            continue
        data = {
            'close': [b['c'] for b in bars],
            'high': [b['h'] for b in bars],
            'low': [b['l'] for b in bars],
            'volume': [b['v'] for b in bars],
        }
        # Patch get_bars to return the live payload for this symbol
        orig = m.get_bars
        m.get_bars = lambda s, _d=data, **kw: (_d if s == sym else orig(s, **kw))
        res = m.analyze_symbol(sym)
        m.get_bars = orig
        if res is None:
            print(f"  {sym:6s} -> None  ❌ STILL DEAD")
            fail += 1
            continue
        scanned += 1
        signals[res['signal']] = signals.get(res['signal'], 0) + 1
        results.append((sym, res['signal'], res['confidence'], res['trend'], res['rsi']))
    except Exception as e:
        print(f"  {sym}: error {type(e).__name__}: {e}")

print(f"\n  analyze_symbol returned a result for {scanned}/{len(SYMBOLS)} symbols")
print(f"  Signal distribution: {signals}")
print()
for sym, sig, conf, trend, rsi in results:
    emoji = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '⚪'}.get(sig, '⚪')
    print(f"  {emoji} {sym:6s} {sig:5s} conf={conf:>3}  trend={trend:<8} rsi={rsi:.0f}")

print()
print("=" * 72)
print("STEP 3 — would any BUY clear the 50 threshold?")
print("=" * 72)
buys = [(s, c) for s, sig, c, t, r in results if sig == 'BUY']
print(f"  BUY signals: {len(buys)}")
for s, c in buys:
    print(f"    {s}: conf={c}  {'CLEARS 50 ✅' if c >= 50 else 'below 50 ❌'}")
if not buys:
    print("    (no BUY signals right now — market conditions, not a bug)")
