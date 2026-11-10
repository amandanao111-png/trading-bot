#!/usr/bin/env python3
"""
Earnings Sentiment Analysis — LIVE earnings calendar (Nasdaq API).

WHY THIS EXISTS (2026-11-09):
The original version of this script shipped a HARDCODED calendar of ten
symbols with April-May 2026 dates. It ran weekly, wrote
earnings_sentiment.json, printed a confident report, and exited 0 — and the
event guard (event_guard.py) correctly refused to trust that file as stale.
Net effect: a guard that was wired up and reported "ok" while protecting
nothing. A monitor that reports the wrong thing is worse than no monitor.

This version fetches the REAL earnings calendar from the Nasdaq public
calendar API and writes real dates. If the fetch fails it says so loudly and
leaves the previous file untouched (the guard then reports "not enforcing",
which is the honest state).

Tracked symbols = the trading bot's own universe (STOCKS + ETFS) plus every
symbol that appears in the trade journal (i.e. anything the bot could
actually hold or trade). Tracking the whole market would bloat the file
without protecting anything.

Data source: https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOURNAL_FILE = os.path.join(BASE_DIR, "trade_journal.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "earnings_sentiment.json")

# How many days forward to pull. 21 days matches the guard's staleness
# window (EARNINGS_STALE_AFTER_DAYS) so a two-week miss can never leave
# the guard silently unprotected.
LOOKAHEAD_DAYS = 21

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")

# Fallback tracked set, used if auto_trade.py cannot be parsed.
FALLBACK_SYMBOLS = [
    "AAPL", "NVDA", "MSFT", "AMD", "INTC", "COST", "ABBV", "JPM", "BAC", "V",
    "QQQ", "SPY", "IWM", "VOO", "DIA", "GLD", "SLV", "TLT", "XLK", "XLE",
    "XLF", "XLV",
]


def tracked_symbols():
    """Symbols worth protecting: bot universe + anything in the journal."""
    syms = set()

    # 1. The bot's own universe, parsed straight from auto_trade.py so the
    #    list can never drift from what the bot actually trades.
    try:
        src = open(os.path.join(BASE_DIR, "auto_trade.py")).read()
        for name in ("STOCKS", "ETFS"):
            m = re.search(rf"^{name}\s*=\s*\[(.*?)\]", src, re.S | re.M)
            if m:
                syms.update(re.findall(r'"([A-Z]{1,5})"', m.group(1)))
    except Exception:
        syms.update(FALLBACK_SYMBOLS)

    # 2. Anything the bot has actually traded or holds.
    try:
        with open(JOURNAL_FILE) as fh:
            journal = json.load(fh)
        for t in journal:
            if isinstance(t, dict) and t.get("symbol"):
                syms.add(str(t["symbol"]).upper())
    except Exception:
        pass

    return sorted(syms)


def _parse_eps(raw):
    """'$2.49' -> 2.49, '($0.44)' -> -0.44, 'N/A'/'' -> None."""
    if raw is None:
        return None
    s = str(raw).strip().replace("$", "").replace(",", "").replace("%", "")
    if not s or s.upper() in ("N/A", "NA", "--", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def fetch_day(d):
    """Return the Nasdaq earnings rows for one ISO date. [] on failure."""
    url = f"https://api.nasdaq.com/api/calendar/earnings?date={d}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return []
    data = payload.get("data") or {}
    rows = data.get("rows") or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sym = (row.get("symbol") or "").strip().upper()
        if not sym or "." in sym:      # drop class-A style tickers like PBR.A
            continue
        out.append({
            "symbol": sym,
            "name": row.get("name"),
            "time": row.get("time"),          # time-pre-market / time-after-hours
            "eps_estimate": _parse_eps(row.get("epsForecast")),
            "eps_previous": _parse_eps(row.get("lastYearEPS")),
        })
    return out


def sentiment_for(estimate, previous):
    """Year-over-year EPS growth: consensus forecast vs last year's actual.

    NAMING NOTE (2026-11-09): the old script called this "surprise_potential"
    and mapped it to BUY/SELL labels. On live data that produced nonsense -
    NVDA (consensus $2.49 vs $1.24 last year, i.e. +100% growth) was labelled
    STRONG_SELL because the formula was (previous - estimate) / previous.
    A higher forecast simply means more EPS, not a bearish signal, and a
    report that says "STRONG_SELL" about NVIDIA is worse than no report.

    So the number is now what it actually is - YoY EPS growth - and the
    labels describe the DIRECTION of earnings, not a trade recommendation.
    Nothing in the bot consumes these labels; only the dates matter for
    the event guard. Growth is not a buy signal.
    """
    if estimate is None or previous in (None, 0):
        growth = 0.0
    else:
        growth = ((estimate - previous) / previous) * 100.0

    if growth > 20:
        label, score = "STRONG_GROWTH", 80
    elif growth > 10:
        label, score = "GROWTH", 40
    elif growth > 0:
        label, score = "SLIGHT_GROWTH", 20
    elif growth > -10:
        label, score = "FLAT", 0
    elif growth > -20:
        label, score = "CONTRACTION", -40
    else:
        label, score = "STRONG_CONTRACTION", -80

    return round(growth, 4), label, score


def collect(lookahead=LOOKAHEAD_DAYS):
    """Fetch the calendar for the next `lookahead` days.

    Returns (rows, fetched_ok, days_with_rows). fetched_ok counts days the
    endpoint ANSWERED (including weekends, which legitimately have zero
    earnings). Counting only non-empty days made a working fetch look like
    7 failures.
    """
    tracked = set(tracked_symbols())
    rows = []
    fetched_ok = 0
    days_with_rows = 0
    today = date.today()

    for i in range(lookahead + 1):
        d = today + timedelta(days=i)
        day_rows = fetch_day(d.isoformat())
        if day_rows is not None:
            fetched_ok += 1
        if day_rows:
            days_with_rows += 1
        for r in day_rows:
            if r["symbol"] in tracked:
                surprise, label, score = sentiment_for(r["eps_estimate"], r["eps_previous"])
                rows.append({
                    "symbol": r["symbol"],
                    "earnings_date": d.isoformat(),
                    "eps_estimate": r["eps_estimate"],
                    "eps_previous": r["eps_previous"],
                    "eps_yoy_growth_pct": surprise,
                    "sentiment": label,
                    "score": score,
                    "session": r["time"],
                    "name": r["name"],
                })
        time.sleep(0.4)   # be polite to the endpoint

    return rows, fetched_ok, days_with_rows


def analyze_all():
    print("\n" + "=" * 70)
    print("📈 EARNINGS SENTIMENT ANALYSIS (live Nasdaq calendar)")
    print("=" * 70)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    rows, fetched_ok, days_with_rows = collect()
    print(f"Calendar fetched for {fetched_ok}/{LOOKAHEAD_DAYS + 1} days "
          f"({days_with_rows} had earnings)")

    if not rows:
        print("\n⚠️  NO EARNINGS DATA RETRIEVED — leaving "
              f"{os.path.basename(OUTPUT_FILE)} untouched.")
        print("   The event guard will report 'earnings guard NOT enforcing'")
        print("   until a run succeeds. That is the honest state.")
        return []

    by_date = {}
    for r in rows:
        by_date.setdefault(r["earnings_date"], []).append(r)

    print("\n📅 UPCOMING EARNINGS (tracked symbols only)")
    print("-" * 70)
    print(f"{'Symbol':<8} {'Date':<12} {'Est EPS':<10} {'Prev EPS':<10} "
          f"{'YoY EPS':<10} {'When':<18}")
    print("-" * 70)

    for d in sorted(by_date):
        for r in sorted(by_date[d], key=lambda x: x["symbol"]):
            est = f"${r['eps_estimate']:.2f}" if r["eps_estimate"] is not None else "-"
            prev = f"${r['eps_previous']:.2f}" if r["eps_previous"] is not None else "-"
            print(f"{r['symbol']:<8} {d:<12} {est:<10} {prev:<10} "
                  f"{r['eps_yoy_growth_pct']:>+9.1f}% {str(r['session'] or ''):<18}")

    # Portfolio impact — open positions only.
    print("\n🎯 PORTFOLIO POSITIONS IMPACT")
    print("-" * 70)
    try:
        with open(JOURNAL_FILE) as fh:
            journal = json.load(fh)
        open_symbols = {
            e["symbol"] for e in journal
            if isinstance(e, dict) and e.get("type") == "BUY"
            and not e.get("closed", False) and e.get("symbol")
        }
        first_upcoming = {}
        for r in rows:
            first_upcoming.setdefault(r["symbol"], r)
        hits = [s for s in sorted(open_symbols) if s in first_upcoming]
        if hits:
            for s in hits:
                r = first_upcoming[s]
                print(f"  {s}: {r['earnings_date']} ({r['session']}) | "
                      f"{r['sentiment']} | ⚠️ EARNINGS INSIDE GUARD WINDOW")
        else:
            print("  No upcoming earnings for current positions")
    except Exception as e:
        print(f"  Could not load positions: {e}")

    print("\n📊 SUMMARY")
    print("-" * 70)
    print(f"  Tracked symbols: {len(tracked_symbols())}")
    print(f"  Earnings rows: {len(rows)}")
    print(f"  Strong growth: {len([r for r in rows if r['sentiment'] == 'STRONG_GROWTH'])}")
    print(f"  Growth: {len([r for r in rows if r['sentiment'] == 'GROWTH'])}")
    print(f"  Contraction: {len([r for r in rows if r['sentiment'] in ('CONTRACTION', 'STRONG_CONTRACTION')])}")

    report = {
        "date": datetime.now().isoformat(),
        "source": "nasdaq.com/api/calendar/earnings",
        "lookahead_days": LOOKAHEAD_DAYS,
        "days_fetched": fetched_ok,
        "days_with_earnings": days_with_rows,
        "earnings": rows,
    }
    tmp = OUTPUT_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(report, fh, indent=2)
    os.replace(tmp, OUTPUT_FILE)
    print(f"\n✅ Wrote {len(rows)} rows to {os.path.basename(OUTPUT_FILE)}")
    return rows


if __name__ == "__main__":
    analyze_all()
