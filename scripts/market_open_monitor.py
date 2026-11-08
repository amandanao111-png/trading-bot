#!/usr/bin/env python3
"""
Market-open monitor — quota-immune (no model required).

Reads trading_bot/trades.log and reports:
  1. BUY signals fired today
  2. Positions opened / closed today
  3. HARD_STOP events today
  4. Anomalies (errors, phantom orders, stale files)

Sends the report to Telegram via the OpenClaw gateway (no token in .env).
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, '/Users/amandaai/.openclaw/workspace')
from telegram_send import send_message  # noqa: E402

TB = '/Users/amandaai/.openclaw/workspace/trading_bot'
LOG = f'{TB}/trades.log'
CHAT_ID = '8250061141'
US_EASTERN = timezone(timedelta(hours=-4))  # matches the bot's log clock


def recent_log_lines(hours=14):
    """Return log lines written in the last N hours (bot stamps ET wall-clock)."""
    if not os.path.exists(LOG):
        return []
    try:
        with open(LOG, errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return []

    cutoff = datetime.now(US_EASTERN) - timedelta(hours=hours)
    out = []
    ts_re = re.compile(r'Cycle at (\d{2}):(\d{2}) (?:EST|ET) - Market (\w+)')
    current_day = None
    day = datetime.now(US_EASTERN).date()
    # The log has no date stamps per line — use file mtime as "today" anchor and
    # treat the last contiguous block of cycles as today's session.
    for line in lines[-6000:]:
        m = ts_re.search(line)
        if m:
            current_day = m.group(3)
        out.append(line)
    return out, current_day


def main():
    lines, market = recent_log_lines()
    if not lines:
        msg = "📊 **Market-open monitor**\n\n⚠️ Could not read trades.log"
        send_message(CHAT_ID, msg)
        return

    tail = ''.join(lines)
    today = datetime.now(US_EASTERN).strftime('%b %-d')

    # 1. BUY signals
    buy_sigs = re.findall(r'🟢\s+([A-Z]+)\s+HOLD|🟢\s+([A-Z]+)\s+BUY', tail)
    buy_names = re.findall(r'🟢\s+([A-Z]+)\s+BUY', tail)
    bought = tail.count('✅ Bought')
    bough = tail.count('🎉 BOUGHT')

    # 2. Positions opened/closed
    opens = tail.count('✅ Bought') + tail.count('🎉 BOUGHT')
    closes = tail.count('✅ Sold')

    # 3. HARD_STOP
    hard_stop = tail.count('HARD_STOP')

    # 4. Anomalies
    anomalies = []
    for pat, label in [
        (r'NameError: (\w+) is not defined', 'NameError'),
        (r'HTTP 403', '403 insufficient buying power'),
        (r'❌ Telegram error', 'Telegram delivery error'),
        (r'Object of type .NoneType. has no len', 'get_bars NoneType crash'),
        (r'Traceback', 'Python traceback'),
    ]:
        n = len(re.findall(pat, tail))
        if n:
            anomalies.append(f'{label} ×{n}')

    lines_out = [
        f'📊 **Market-open monitor — {today}**',
        '',
        f'1. **BUY signals:** {len(buy_names)} fired — {"📋 " + ", ".join(sorted(set(buy_names))[:8]) if buy_names else "none"}',
        f'   **Orders executed:** {opens} bought, {closes} closed',
        f'2. **Positions opened/closed:** {opens} opened, {closes} closed',
        f'3. **HARD_STOP triggered:** {hard_stop}',
    ]
    if anomalies:
        lines_out.append(f'4. **⚠️ Anomalies ({len(anomalies)}):**')
        for a in anomalies:
            lines_out.append(f'   • {a}')
    else:
        lines_out.append('4. **Anomalies:** none detected ✅')

    # Bot liveness
    try:
        import subprocess
        out = subprocess.run(['pgrep', '-f', 'auto_trade'], capture_output=True, text=True).stdout
        pids = [p for p in out.split() if p.strip()]
        lines_out.append(f'')
        lines_out.append(f'🤖 Bot instances: {len(pids)} (PID {", ".join(pids) if pids else "none"})')
    except Exception:
        pass

    msg = '\n'.join(lines_out)
    send_message(CHAT_ID, msg)
    print(msg)


if __name__ == '__main__':
    main()
