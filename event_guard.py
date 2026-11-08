"""Event guard — block or shrink new entries around high-impact market events.

WHY THIS EXISTS (2026-11-08):
The bot's existing risk gates are all intraday-shape gates: danger zone, kill
zone, sector cooldown, daily loss limit, correlation limit. None of them
know about the CALENDAR. That meant the bot was structurally willing to open
a fresh position straight into the CPI print (Tue 10 Nov, 08:30 ET) and into
the FOMC decision (9 Dec, 14:00 ET) — precisely the two releases where the
rate path that this whole book trades on gets repriced.

Two different failure modes are handled:

  MACRO EVENTS  — release times come from macro_events.json, which is
  human-curated and always in the near future. Trusted, and enforced.

  EARNINGS      — the only earnings data available is earnings_sentiment.json,
  whose earnings_date values were stale (Apr-May 2026). A guard that silently
  trusted stale dates would be a guard that silently does nothing. So the
  earnings guard REFUSES to be trusted when the data is stale: it reports
  "not enforcing" loudly instead of pretending to protect.

SAFETY CONTRACT (the important part):
  * This guard governs NEW ENTRIES ONLY. It must never be able to stop a
    protective exit (stop-loss, trailing stop, take-profit). An exit that
    cannot fire because a CPI print is tomorrow is a catastrophic bug.
  * Every function never raises. A crash in the guard must not take down the
    trading loop; a broken guard degrades to "no event filtering" and says so.
  * Time comparisons use US/Eastern, matching the rest of the bot.

Usage:
    from event_guard import event_guard_state, entry_size_multiplier

    st = event_guard_state()
    if st["block_entries"]:
        ...skip new entries, but still evaluate exits...
    mult = entry_size_multiplier(symbol)   # 0.0 => blocked, 1.0 => normal
"""

import json
import os
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MACRO_EVENTS_FILE = os.path.join(BASE_DIR, "macro_events.json")
EARNINGS_FILE = os.path.join(BASE_DIR, "earnings_sentiment.json")

# Earnings data older than this is not trusted for blocking.
EARNINGS_STALE_AFTER_DAYS = 21

# Extra buffer around a macro release before it is considered "past".
MACRO_GRACE_MINUTES = 5

# How long before a release entries are blocked.
DEFAULT_BLOCK_BEFORE_MIN = 30

# How long after a release entries are blocked.
DEFAULT_BLOCK_AFTER_MIN = 30

# For a PRE-MARKET release (before 09:30 ET) the reaction lands at the open,
# so entries are blocked for this many minutes after the open instead.
DEFAULT_BLOCK_FIRST_OPEN_MIN = 45

# Soft window: this many minutes before a release, entries are ALLOWED but
# HALVED in size rather than blocked outright. A hard block right at the
# release would be over-reaction; a full-size entry into a CPI print is
# negligence. So the size tapers in as the event approaches.
DEFAULT_SOFT_BEFORE_MIN = 120

# The 09:30 ET open, in minutes from midnight.
MARKET_OPEN_MINUTES = 9 * 60 + 30


def _us_eastern_now():
    """Aware US/Eastern datetime. Never raises."""
    now_utc = datetime.now(timezone.utc)
    if ZoneInfo is not None:
        return now_utc.astimezone(ZoneInfo("America/New_York"))
    # Fallback: US/Eastern is UTC-5 (EST) or UTC-4 (EDT). Use a crude
    # approximation rather than raising - the guard degrades, it does not die.
    return now_utc.astimezone(timezone(timedelta(hours=-4)))


def _parse_hhmm(value):
    """'08:30' -> 510 minutes. Returns None on anything unexpected."""
    try:
        h, m = str(value).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _load_macro_events():
    """Return the curated macro event list. Empty list if the file is broken."""
    data = _load_json(MACRO_EVENTS_FILE)
    if not isinstance(data, dict):
        return []
    events = data.get("events")
    if not isinstance(events, list):
        return []
    return [e for e in events if isinstance(e, dict)]


def _earnings_state(now=None):
    """Return (enforced, symbols_by_date, note).

    enforced is False when the earnings data is missing or stale. Callers
    must treat a non-enforced earnings guard as "no earnings protection",
    NOT as "no earnings today".
    """
    now = now or _us_eastern_now()
    data = _load_json(EARNINGS_FILE)
    if not isinstance(data, dict):
        return False, {}, "earnings data unreadable"

    rows = data.get("earnings")
    if not isinstance(rows, list) or not rows:
        return False, {}, "earnings data empty"

    by_date = {}
    newest = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        date_str = row.get("earnings_date")
        if not symbol or not date_str:
            continue
        try:
            d = datetime.strptime(str(date_str), "%Y-%m-%d").date()
        except Exception:
            continue
        by_date.setdefault(d, set()).add(str(symbol).upper())
        if newest is None or d > newest:
            newest = d

    if newest is None:
        return False, {}, "earnings data had no parseable dates"

    age_days = (now.date() - newest).days
    if age_days > EARNINGS_STALE_AFTER_DAYS:
        return False, {}, (f"earnings data stale (newest {newest}, "
                           f"{age_days}d old > {EARNINGS_STALE_AFTER_DAYS}d) "
                           f"- earnings guard NOT enforcing")
    # Stale in the other direction (all dates in the future) is fine.
    return True, by_date, f"earnings data current (newest {newest})"


def event_guard_state(now=None, symbol=None):
    """Describe the event context for right now.

    Returns a dict:
      block_entries   bool  - no NEW entries at all right now
      block_symbols   set   - symbols with an earnings event today
      reason          str   - human-readable explanation
      events          list  - macro events currently in force
      earnings_enforced bool - whether earnings blocking is actually live
      notes           list  - warnings (stale data, unreadable files)
    """
    now = now or _us_eastern_now()
    notes = []
    events = []

    block_entries = False
    reason = ""
    block_symbols = set()
    soft_events = []

    # ---- macro events ----
    for ev in _load_macro_events():
        date_str = ev.get("date")
        time_str = ev.get("time_et")
        if not date_str or not time_str:
            continue
        try:
            day = datetime.strptime(str(date_str), "%Y-%m-%d").date()
        except Exception:
            continue
        mins = _parse_hhmm(time_str)
        if mins is None:
            continue
        if day != now.date():
            continue

        before = ev.get("block_before_min", DEFAULT_BLOCK_BEFORE_MIN)
        after = ev.get("block_after_min", DEFAULT_BLOCK_AFTER_MIN)
        first_open = ev.get("block_first_open_min", DEFAULT_BLOCK_FIRST_OPEN_MIN)
        soft_before = ev.get("soft_before_min", DEFAULT_SOFT_BEFORE_MIN)
        name = ev.get("name", "macro event")

        now_minutes = now.hour * 60 + now.minute
        in_pre = (now_minutes >= mins - before) and (now_minutes < mins)
        in_post = (now_minutes >= mins) and (now_minutes <= mins + after)
        in_soft = ((now_minutes >= mins - soft_before)
                   and (now_minutes < mins - before))

        if in_soft:
            soft_events.append({"name": name, "date": date_str,
                                "time_et": time_str, "impact": ev.get("impact", "high")})

        # A pre-market release (before 09:30 ET) has its reaction AT the open.
        if first_open and mins < MARKET_OPEN_MINUTES:
            in_post = in_post or (now_minutes >= MARKET_OPEN_MINUTES
                                  and now_minutes < MARKET_OPEN_MINUTES + first_open)

        if in_pre or in_post:
            block_entries = True
            phase = "pre-release" if in_pre else "post-release"
            reason = f"{name} {phase} ({date_str} {time_str} ET)"
            events.append({"name": name, "date": date_str, "time_et": time_str,
                           "phase": phase, "impact": ev.get("impact", "high")})

    # ---- earnings ----
    enforced, by_date, note = _earnings_state(now)
    if not enforced:
        notes.append(note)
    else:
        todays = by_date.get(now.date())
        if todays:
            block_symbols = set(todays)

    return {
        "block_entries": block_entries,
        "block_symbols": block_symbols,
        "reason": reason,
        "events": events,
        "soft_events": soft_events,
        "earnings_enforced": enforced,
        "notes": notes,
        "now_et": now.strftime("%Y-%m-%d %H:%M:%S ET"),
    }


def entry_size_multiplier(symbol=None, state=None, now=None):
    """Position-size multiplier for a NEW entry.

    0.0 => do not enter. 0.5 => half size. 1.0 => normal.
    Never raises; on any internal error returns 1.0 (no protection, but also
    no accidental blocking of trading).
    """
    try:
        st = state or event_guard_state(now=now)
        if st.get("block_entries"):
            return 0.0
        syms = st.get("block_symbols") or set()
        if symbol and str(symbol).upper() in syms:
            return 0.0
        if st.get("soft_events"):
            return 0.5
        return 1.0
    except Exception:
        return 1.0


def describe(state=None, now=None):
    """One-line human summary for logs."""
    try:
        st = state or event_guard_state(now=now)
        parts = []
        if st.get("block_entries"):
            parts.append(f"BLOCKED: {st.get('reason')}")
        else:
            parts.append("no macro event in force")
        if st.get("block_symbols"):
            parts.append("earnings today: " + ",".join(sorted(st["block_symbols"])))
        if not st.get("earnings_enforced"):
            parts.append("(earnings guard NOT enforcing)")
        return " | ".join(parts)
    except Exception:
        return "event guard unavailable"
