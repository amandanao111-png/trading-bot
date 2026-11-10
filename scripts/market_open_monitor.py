#!/usr/bin/env python3
"""
Market-open monitor — quota-immune (no model required).

Reads trading_bot/trades.log and reports TODAY's session only:
  1. BUY signals seen (and how many were skipped vs entered)
  2. Orders executed today (bought / closed)
  3. HARD_STOP events today
  4. Anomalies today (errors, phantom orders, stale files)

2026-11-10 fix: the previous version counted the last 6000 log lines, which
spans several days, so it reported stale fills (it told Telegram "4 bought,
5 closed, HARD_STOP 1" on a day with zero trades) and its BUY-signal regex
never matched the real log format, so it always said "0 fired".
Now the log slice is anchored to the last ET-midnight rollover and the
journal is used as a second source for today's fills.

Usage: market_open_monitor.py [--dry-run]   (dry-run prints without sending)
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, '/Users/amandaai/.openclaw/workspace')
from telegram_send import send_message  # noqa: E402

TB = '/Users/amandaai/.openclaw/workspace/trading_bot'
LOG = f'{TB}/trades.log'
JOURNAL = f'{TB}/v7_journal.json'
CHAT_ID = '8250061141'

try:
    from zoneinfo import ZoneInfo
    US_EASTERN = ZoneInfo('America/New_York')
except Exception:  # pragma: no cover - fallback if tzdata is unavailable
    US_EASTERN = timezone(timedelta(hours=-5))

# Both log stamp formats: "Cycle at 09:52 ET - Market OPEN" and
# "⏰ 09:22 ET - Market CLOSED".
TS_RE = re.compile(r'(?:Cycle at |⏰\s+)(\d{2}):(\d{2}) (?:EST|ET)')
BUY_RE = re.compile(r'🟢\s+([A-Z]+):.*?\bBUY\(')
SELL_RE = re.compile(r'🔴\s+([A-Z]+):.*?\bSELL\(')


def et_now():
    return datetime.now(US_EASTERN)


def log_slice_today():
    """Return (lines, anchored) for today's ET session.

    The bot stamps wall-clock ET but no dates, so the ET-midnight rollover
    (hour drops by more than 6) marks the start of the current session.
    Falls back to a bounded recent window when no rollover is present.
    """
    if not os.path.exists(LOG):
        return [], False
    try:
        with open(LOG, errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return [], False

    cut = None
    prev = None
    for i, line in enumerate(lines):
        m = TS_RE.search(line)
        if not m:
            continue
        hour = int(m.group(1))
        if prev is not None and prev - hour > 6:
            cut = i
        prev = hour
    if cut is None:
        return lines[-3000:], False
    return lines[cut:], True


def journal_today_counts(today_et_date):
    """Cross-check fills from the journal (JSONL despite the .json suffix).

    Journal timestamps are machine-local (this host runs on GMT == UTC), so
    they are read as UTC and converted to ET before the date is compared.
    """
    counts = {'entries': 0, 'trades': [], 'exits': [], 'last': None}
    if not os.path.exists(JOURNAL):
        return counts
    try:
        with open(JOURNAL, errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = rec.get('time')
                if not ts:
                    continue
                try:
                    dt_utc = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if dt_utc.astimezone(US_EASTERN).date() != today_et_date:
                    continue
                counts['entries'] += 1
                counts['trades'].extend(rec.get('trades') or [])
                counts['exits'].extend(rec.get('exits') or [])
                counts['last'] = ts
    except Exception:
        pass
    return counts


def parse_signals(lines):
    """Count BUY/SELL signals and classify each as entered / skipped / no-action.

    A signal is 'entered' only when a real fill follows it, 'skipped' when the
    bot prints "→ Skipped: ...", and 'no_action' when the next ticker line
    arrives with no disposition (e.g. a SELL while the book is 100% cash).
    """
    buys, sells = [], []
    pending = None  # ('BUY'|'SELL', record)

    def close_pending():
        nonlocal pending
        if pending:
            pending[1]['state'] = pending[1].get('state') or 'no_action'
            pending = None

    for line in lines:
        mb = BUY_RE.search(line)
        ms = SELL_RE.search(line)
        if mb or ms:
            close_pending()
            if mb:
                rec = {'symbol': mb.group(1), 'state': None}
                buys.append(rec)
                pending = ('BUY', rec)
            else:
                rec = {'symbol': ms.group(1), 'state': None}
                sells.append(rec)
                pending = ('SELL', rec)
            continue
        if pending and ('→' in line or '✅' in line or '🎉' in line or '⏳' in line):
            if 'Skipped' in line:
                pending[1]['state'] = 'skipped'
                pending = None
            elif '✅ Bought' in line or '🎉 BOUGHT' in line or '✅ Sold' in line or '✅ Filled' in line:
                pending[1]['state'] = 'entered'
                pending = None
    close_pending()
    return buys, sells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true',
                    help='print the report without sending it to Telegram')
    args = ap.parse_args()

    now_et = et_now()
    today = now_et.strftime('%b %-d')
    today_date = now_et.date()

    lines, anchored = log_slice_today()
    if not lines:
        msg = '📊 **Market-open monitor**\n\n⚠️ Could not read trades.log'
        if not args.dry_run:
            send_message(CHAT_ID, msg)
        print(msg)
        return

    tail = ''.join(lines)

    # Signals: green/red ticker lines, with skip/enter state from the next line.
    buys, sells = parse_signals(lines)
    buy_entered = sum(1 for b in buys if b['state'] == 'entered')
    buy_skipped = sum(1 for b in buys if b['state'] == 'skipped')
    buy_no_action = sum(1 for b in buys if b['state'] == 'no_action')
    sell_entered = sum(1 for s in sells if s['state'] == 'entered')
    sell_no_action = sum(1 for s in sells if s['state'] == 'no_action')

    # Orders executed (today's log slice only).
    bought = tail.count('✅ Bought') + tail.count('🎉 BOUGHT')
    closed = tail.count('✅ Sold')
    hard_stop = tail.count('HARD_STOP')

    # Cross-check with the journal.
    j = journal_today_counts(today_date)

    # Last activity / freshness.
    last_stamp = None
    for line in reversed(lines):
        m = TS_RE.search(line)
        if m:
            last_stamp = m.group(0)
            break
    last_cycle_hours = None
    if last_stamp:
        m = TS_RE.search(last_stamp)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            dt = now_et.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if dt > now_et:
                dt -= timedelta(days=1)
            last_cycle_hours = round((now_et - dt).total_seconds() / 3600, 1)

    # Event guard state (latest line wins).
    guard = 'none reported'
    for line in lines:
        if 'Event guard: BLOCKING' in line:
            guard = line.strip().lstrip('⚠️ ').strip()
    if any('earnings data stale' in l for l in lines):
        guard += ' | ⚠️ earnings data stale'

    # Anomalies (today only).
    anomalies = []
    for pat, label in [
        (r'NameError: (\w+) is not defined', 'NameError'),
        (r'HTTP 403', '403 insufficient buying power'),
        (r'❌ Telegram error', 'Telegram delivery error'),
        (r'Object of type .NoneType. has no len', 'get_bars NoneType crash'),
        (r'Traceback', 'Python traceback'),
        (r'Learning record failed', 'learning-record write failed'),
    ]:
        n = len(re.findall(pat, tail))
        if n:
            anomalies.append(f'{label} ×{n}')

    out = [
        f'📊 **Market-open monitor — {today}**',
        '',
        f'1. **BUY signals seen:** {len(buys)} ({buy_entered} entered, {buy_skipped} skipped, '
        f'{buy_no_action} no action) — '
        + (', '.join(sorted({b["symbol"] for b in buys})) if buys else 'none'),
        f'   **SELL signals seen:** {len(sells)} ({sell_entered} entered, {sell_no_action} no action)',
        f'   **Orders executed:** {bought} bought, {closed} closed',
        f'2. **HARD_STOP triggered:** {hard_stop}',
        f'3. **Event guard:** {guard}',
        f'   **Last cycle:** {last_stamp or "n/a"}' + (f' ({last_cycle_hours}h ago)' if last_cycle_hours else ''),
    ]
    if anomalies:
        out.append(f'4. **⚠️ Anomalies ({len(anomalies)}):**')
        for a in anomalies:
            out.append(f'   • {a}')
    else:
        out.append('4. **Anomalies:** none detected ✅')

    out.append('')
    out.append(f'🧾 Journal cross-check: {j["entries"]} cycles today, '
               f'{len(j["trades"])} trades, {len(j["exits"])} exits'
               + (f' (last {j["last"]})' if j['last'] else ''))
    out.append(f'🕒 Session anchor: {"ET-midnight rollover" if anchored else "FALLBACK recent window"}')

    try:
        import subprocess
        out_p = subprocess.run(['pgrep', '-f', 'auto_trade'], capture_output=True, text=True).stdout
        pids = [p for p in out_p.split() if p.strip()]
        out.append(f'🤖 Bot instances: {len(pids)} (PID {", ".join(pids) if pids else "none"})')
    except Exception:
        pass

    msg = '\n'.join(out)
    if not args.dry_run:
        send_message(CHAT_ID, msg)
    print(msg)


if __name__ == '__main__':
    main()
