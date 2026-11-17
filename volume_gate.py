"""
volume_gate.py — same-time-of-day volume baseline for the entry volume gate.

WHY (2026-11-17)
----------------
The old gate (`Indicators.VolumeConfirmation`) compared ONE completed 5-minute
bar against the mean of the previous 20 completed 5-minute bars, i.e. a single
bar against a mixed ~100-minute window. That measures nothing about whether
the market is alive at the moment of the decision and it flips on noise:
on Mon Nov 16 it rejected 15/15 symbols at 16:15 ET — including the only BUY
the bot printed (SLV, ratio 0.19) — so the book stayed 100% cash for a
measurement reason instead of a market reason.

WHAT THIS DOES
--------------
Compare like with like. Today's session-cumulative volume up to the decision
clock is compared against the SAME clock-time cumulative volume averaged over
the last N completed sessions:

    ratio = volume_today(regular session open -> C)
          / mean_over_sessions( volume_session(regular session open -> C) )

Both sides use the same clock C and the same regular-session window
(09:30-16:00 ET), so a symbol at 16:15 ET is judged against 16:15 ET history
and never against a mean that contains the fat 10:30 ET open hour.

Both sides only count COMPLETED bars (a bar is counted when it has ended), so
a forming bar can never make one side look smaller than the other.

FALLBACKS ARE ALWAYS EXPLICIT
-----------------------------
If the history cannot be built the ratio is NEUTRAL (1.0) — never 0 — and the
reason is reported in the returned info dict (`baseline` key). A gate that
cannot see the market must not block the market.

The module is deliberately free of trading decisions: it computes numbers.
The gate itself is still enforced at the entry-decision site in auto_trade.py.
"""

import os
import statistics
import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - very old Python
    ET = timezone(timedelta(hours=-5))

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:  # credentials are only needed for the live path; unit tests never hit them
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:  # pragma: no cover
    pass


DATA_URL = "https://data.alpaca.markets"
BAR_MINUTES = 5                 # bars fetched with timeframe=5Min
NEUTRAL_RATIO = 1.0              # fallback ratio: never 0, never blocks
BASELINE_SESSIONS = 20           # how many prior sessions make the baseline
MIN_BASELINE_SESSIONS = 5        # fewer than this -> neutral fallback
SESSION_OPEN_MIN = 9 * 60 + 30   # 09:30 ET regular session open
SESSION_CLOSE_MIN = 16 * 60      # 16:00 ET regular session close
RECENT_WINDOW_MIN = 30           # "is the market busy right now" window
HISTORY_CHUNK_DAYS = (13, 26, 39)  # chunked history pulls (some deployments cap a response)

_HISTORY_CACHE = {}   # symbol -> {"day": "YYYY-MM-DD", "sessions": [session, ...]}
_TODAY_CACHE = {}    # symbol -> {"day": ..., "ts": epoch, "bars": [...]}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _headers(headers=None):
    if headers:
        return headers
    return {
        "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.environ.get("ALPACA_API_SECRET", ""),
    }


def _parse_ts(value):
    """Alpaca returns either an ISO string ('2026-11-16T15:45:00Z') or an epoch."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone(ET)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET)


def _clock_str(cutoff_min):
    return f"{int(cutoff_min) // 60:02d}:{int(cutoff_min) % 60:02d}"


def _utc_iso(dt_et):
    """ET datetime -> Alpaca start/end string (UTC ISO8601)."""
    return dt_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_bars(symbol, headers=None, data_url=DATA_URL, timeframe="5Min",
               start=None, end=None, limit=10000, retries=4, backoff=1.5):
    """Fetch bars from the Alpaca data API (retry + backoff).

    Returns a list of {"t": datetime(ET), "v": float} sorted by time, or None
    when the API could not be read. Never raises.
    """
    if requests is None:
        return None
    url = f"{data_url}/v2/stocks/{symbol}/bars"
    params = {"timeframe": timeframe, "limit": limit, "adjustment": "raw"}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    hdr = _headers(headers)
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=hdr, params=params, timeout=30)
            if r.status_code == 200:
                data = r.json()
                raw = data.get("bars") if isinstance(data, dict) else None
                out = []
                for b in raw or []:
                    ts = _parse_ts(b.get("t"))
                    v = b.get("v")
                    if ts is None or v is None:
                        continue
                    try:
                        out.append({"t": ts, "v": float(v)})
                    except (TypeError, ValueError):
                        continue
                out.sort(key=lambda x: x["t"])
                return out
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(backoff * (attempt + 1))
                continue
            return None
        except Exception:
            time.sleep(backoff * (attempt + 1))
    return None


def split_sessions(bars):
    """Group bars into trading sessions (keyed by ET calendar date).

    Each bar becomes {"start_min", "end_min", "v"} in ET minutes-since-midnight.
    """
    sessions = {}
    for b in bars or []:
        day = b["t"].date().isoformat()
        start_min = b["t"].hour * 60 + b["t"].minute
        sessions.setdefault(day, []).append(
            {"start_min": start_min, "end_min": start_min + BAR_MINUTES, "v": b["v"]}
        )
    for lst in sessions.values():
        lst.sort(key=lambda x: x["start_min"])
    return sessions


def _in_regular_session(bar):
    """Regular session only: 09:30-16:00 ET (pre/post market volume excluded)."""
    return bar["end_min"] > SESSION_OPEN_MIN and bar["start_min"] < SESSION_CLOSE_MIN


def cumulative(session, cutoff_min):
    """Regular-session volume traded from the open up to `cutoff_min`.

    Only COMPLETED bars are counted (bar end <= cutoff), so both sides of the
    comparison (today and history) describe the same elapsed window.
    """
    total = 0.0
    for bar in session or []:
        if not _in_regular_session(bar):
            continue
        if bar["end_min"] <= cutoff_min:
            total += bar["v"]
    return total


def window_volume(session, cutoff_min, minutes=RECENT_WINDOW_MIN):
    """Volume traded in the `minutes` window ending at `cutoff_min`."""
    total = 0.0
    for bar in session or []:
        if not _in_regular_session(bar):
            continue
        if cutoff_min - minutes < bar["end_min"] <= cutoff_min:
            total += bar["v"]
    return total


def regular_bar_count(session, cutoff_min=None):
    """How many regular-session bars the session carries (data-quality check)."""
    n = 0
    for bar in session or []:
        if not _in_regular_session(bar):
            continue
        if cutoff_min is None or bar["end_min"] <= cutoff_min:
            n += 1
    return n


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------
def compute_gate(today_session, hist_sessions, cutoff_min, min_sessions=MIN_BASELINE_SESSIONS):
    """Pure math: today's cumulative vs the same-clock historical average.

    `hist_sessions` is a list of completed sessions (most recent last).
    Returns (ratio, recent_ratio, info).
    """
    info = {
        "baseline": "none",
        "sessions": 0,
        "today_volume": 0.0,
        "baseline_volume": 0.0,
        "cutoff_et": _clock_str(cutoff_min),
        "recent_window_min": RECENT_WINDOW_MIN,
    }

    hist_sessions = list(hist_sessions or [])
    today_vol = cumulative(today_session, cutoff_min)
    info["today_volume"] = today_vol

    if len(hist_sessions) < min_sessions:
        info["baseline"] = "insufficient_history"
        info["sessions"] = len(hist_sessions)
        return NEUTRAL_RATIO, NEUTRAL_RATIO, info

    values = []
    for sess in hist_sessions:
        v = cumulative(sess, cutoff_min)
        if v > 0:
            values.append(v)
    if not values:
        info["baseline"] = "zero_baseline"
        info["sessions"] = len(hist_sessions)
        return NEUTRAL_RATIO, NEUTRAL_RATIO, info

    # MEDIAN, not mean. Replay over the last month (2026-11-17) showed a few
    # volume-spike sessions (QQQ 2026-11-05 ran 94M shares by 14:46 vs a ~25M
    # normal day) dragging the 20-session MEAN up by 40-60%. A mean baseline
    # then rejects perfectly normal days; the median tracks the typical day
    # at the same clock and only a genuinely dead session falls through it.
    baseline = statistics.median(values)
    info["baseline"] = "same_time_of_day"
    info["sessions"] = len(values)
    info["baseline_volume"] = baseline
    info["baseline_mean_volume"] = sum(values) / len(values)
    info["baseline_median_volume"] = baseline

    if today_vol <= 0:
        # No volume yet for today (pre-open, or the feed has not caught up):
        # stay neutral instead of blocking the market.
        info["baseline"] = "no_today_volume"
        return NEUTRAL_RATIO, NEUTRAL_RATIO, info

    ratio = today_vol / baseline if baseline > 0 else NEUTRAL_RATIO

    # "is the market busy right now" metric: the last 30 completed minutes
    # versus the same 30 minutes on past sessions.
    today_recent = window_volume(today_session, cutoff_min)
    hist_recent = [window_volume(s, cutoff_min) for s in hist_sessions]
    hist_recent = [v for v in hist_recent if v > 0]
    if hist_recent:
        recent_ratio = today_recent / (sum(hist_recent) / len(hist_recent))
    else:
        recent_ratio = NEUTRAL_RATIO

    return ratio, recent_ratio, info


def load_history(symbol, headers=None, now=None, data_url=DATA_URL,
                 sessions=BASELINE_SESSIONS):
    """Completed sessions for the baseline (most recent last).

    Cached per symbol per trading day — historical sessions do not change
    intraday, so this costs a couple of API pulls per symbol per day, not per
    cycle. Returns [] when the history cannot be read.
    """
    now = now or datetime.now(ET)
    day = now.date().isoformat()
    cached = _HISTORY_CACHE.get(symbol)
    if cached and cached.get("day") == day:
        return cached["sessions"]

    collected = {}
    for offset in HISTORY_CHUNK_DAYS:
        start = _utc_iso(now - timedelta(days=offset))
        try:
            bars = fetch_bars(symbol, headers=headers, data_url=data_url,
                              timeframe="5Min", start=start, limit=10000)
        except Exception:
            bars = None
        if bars is None:
            continue
        for d, lst in split_sessions(bars).items():
            if d == day:
                continue  # today is measured separately
            collected.setdefault(d, lst)
        if len(collected) >= sessions:
            break

    ordered = [collected[d] for d in sorted(collected)[-sessions:]]
    _HISTORY_CACHE[symbol] = {"day": day, "sessions": ordered}
    return ordered


def load_today(symbol, headers=None, now=None, data_url=DATA_URL, ttl=120):
    """Today's bars (regular session), refreshed every `ttl` seconds."""
    now = now or datetime.now(ET)
    day = now.date().isoformat()
    cached = _TODAY_CACHE.get(symbol)
    if cached and cached.get("day") == day and (time.time() - cached.get("ts", 0)) < ttl:
        return cached["bars"]

    start = _utc_iso(now.replace(hour=0, minute=0, second=0, microsecond=0))
    try:
        bars = fetch_bars(symbol, headers=headers, data_url=data_url,
                          timeframe="5Min", start=start, limit=500)
    except Exception:
        bars = None
    today = split_sessions(bars or []).get(day, [])
    _TODAY_CACHE[symbol] = {"day": day, "ts": time.time(), "bars": today}
    return today


def evaluate(symbol, headers=None, now=None, data_url=DATA_URL):
    """Evaluate the volume gate for `symbol` right now.

    Returns {"ratio", "recent_ratio", "info"}. `ratio` is what the entry gate
    compares against MIN_VOLUME_RATIO; `recent_ratio` is the busy-right-now
    metric used for the "high volume" score bonus. Both are neutral (1.0)
    whenever the baseline cannot be built — see info["baseline"].
    """
    now = now or datetime.now(ET)
    cutoff = now.hour * 60 + now.minute

    if cutoff < SESSION_OPEN_MIN:
        return NEUTRAL_RATIO, NEUTRAL_RATIO, {
            "baseline": "pre_open", "sessions": 0, "today_volume": 0.0,
            "baseline_volume": 0.0, "cutoff_et": _clock_str(cutoff),
            "recent_window_min": RECENT_WINDOW_MIN,
        }

    try:
        hist = load_history(symbol, headers=headers, now=now, data_url=data_url)
    except Exception:
        hist = []
    try:
        today = load_today(symbol, headers=headers, now=now, data_url=data_url)
    except Exception:
        today = []

    ratio, recent_ratio, info = compute_gate(today, hist, cutoff)
    info["symbol"] = symbol
    info["today_bars"] = regular_bar_count(today)
    return ratio, recent_ratio, info


def clear_caches():
    """Drop cached history/today bars (used by tests and by the replay tool)."""
    _HISTORY_CACHE.clear()
    _TODAY_CACHE.clear()
