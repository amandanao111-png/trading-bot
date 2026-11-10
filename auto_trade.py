#!/usr/bin/env python3
"""
Nova's Trading Bot V9 - Self-Learning Edition
Based on research: RSI+MACD combined strategy (73% win rate)
V9 Features:
- Self-learning from trade history
- Adaptive confidence thresholds
- Performance analysis
- Weekly reports
"""

import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
import sys
import json
import os
from pathlib import Path
import time
import traceback
import warnings
from dotenv import load_dotenv

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 fallback
    ZoneInfo = None

# Silence harmless numpy RuntimeWarnings on degenerate series (single-element stddev,
# division-by-zero in variance when only 1 bar in window). Doesn't affect trading logic.
warnings.filterwarnings('ignore', category=RuntimeWarning)

# Load environment variables from .env file
load_dotenv()

# Import learning module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from learning import record_trade, analyze_performance, get_optimal_confidence, get_weekly_report, load_stats, load_journal

# Event guard — blocks or halves NEW entries around CPI/PPI/FOMC.
# Imports are wrapped: a guard that cannot load must degrade to "no event
# filtering", never take the trading loop down with it.
try:
    from event_guard import event_guard_state, entry_size_multiplier
except Exception:  # pragma: no cover
    event_guard_state = None
    entry_size_multiplier = None

# API Keys - MUST BE SET VIA ENVIRONMENT VARIABLES
# Set these before running: 
#   export ALPACA_API_KEY="your_key"
#   export ALPACA_API_SECRET="your_secret"
import os
API_KEY = os.environ.get("ALPACA_API_KEY", "")
SECRET_KEY = os.environ.get("ALPACA_API_SECRET", "")

# API Keys - MUST be set via environment variables or .env file
# Set in .env file or export before running:
#   export ALPACA_API_KEY="your_key"
#   export ALPACA_API_SECRET="your_secret"
API_KEY = os.environ.get("ALPACA_API_KEY", "")
SECRET_KEY = os.environ.get("ALPACA_API_SECRET", "")

if not API_KEY or not SECRET_KEY:
    print("ERROR: ALPACA_API_KEY and ALPACA_API_SECRET must be set in .env file")
    print("Please create a .env file in the trading_bot directory")
    sys.exit(1)

BASE_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": SECRET_KEY
}

# === OPTIMIZED CONFIG ===
MAX_POSITION_PCT = 0.15     # 15% max position per stock (legacy — overridden by kelly_position_pct below)
MIN_CONFIDENCE_BUY = 55     # Higher threshold for BUY signals (was 35)
MIN_CONFIDENCE = 35         # Default threshold for other signals


def kelly_position_pct(symbol):
    """Half-Kelly position sizing per symbol. Conservative defaults for unknown symbols.
    Returns fractional position size (e.g., 0.02 = 2% of portfolio).
    Based on closed-trade history. Negative Kelly = minimal probe size (0.5%).
    """
    try:
        stats = load_stats().get("symbol_stats", {}).get(symbol, {})
        n = stats.get("total", 0)
        if n < 3:
            return 0.01  # 1% default for symbols without history

        p = stats.get("wins", 0) / n
        q = 1 - p
        if q <= 0 or p <= 0:
            return 0.005

        journal = load_journal()
        sym_trades = [t for t in journal if t.get("symbol") == symbol and t.get("closed")]
        sym_wins = [t.get("pnl", 0) for t in sym_trades if t.get("pnl", 0) > 0]
        sym_losses = [abs(t.get("pnl", 0)) for t in sym_trades if t.get("pnl", 0) < 0]

        if not sym_wins or not sym_losses:
            return 0.01
        aw = sum(sym_wins) / len(sym_wins)
        al = sum(sym_losses) / len(sym_losses)
        if al == 0 or aw == 0:
            return 0.01

        b = aw / al
        kelly = (p * b - q) / b
        if kelly <= 0:
            return 0.005  # 0.5% — minimal probe for negative-edge symbols

        # Half-Kelly, capped at 3% (down from 15%)
        return max(0.005, min(kelly * 0.5, 0.03))
    except Exception:
        return 0.01  # Safe default on any error
STOP_LOSS_PCT = 0.02       # 2% stop
TAKE_PROFIT_PCT = 0.08     # 8% take profit (sell remaining after 5% partial)

# === NEW: RISK MANAGEMENT ===
MAX_POSITIONS = 15          # Never exceed 15 open positions
MAX_DAILY_LOSS = 0.03       # Stop if -3% today
MAX_PORTFOLIO_RISK = 0.10   # Never risk more than 10% total
CORRELATION_FILTER = 0.7    # Max 70% similar sector positions

# === NEW: SCALE-OUT STRATEGY ===
SCALE_OUT_1_PCT = 0.05      # Sell 50% at 5% profit
SCALE_OUT_2_PCT = 0.10      # Sell remaining at 10% profit
SCALE_OUT_RATIO = 0.5       # First scale: sell 50%

# === NEW: TRAILING STOP ===
TRAILING_STOP_ENABLED = True
TRAILING_START_PCT = 0.06   # Start trailing at 6% profit
TRAILING_DISTANCE_PCT = 0.03 # Keep 3% below peak

# === NEW: MEAN REVERSION (ETFs) ===
MEAN_REVERSION_ENABLED = True
ETF_LIST = ["QQQ", "SPY", "IWM", "VOO", "DIA", "XLK", "XLE", "XLF", "XLV", "GLD", "SLV"]

# === LOOP SETTINGS ===
LOOP_INTERVAL_MINUTES = 30   # Check every 30 minutes during market hours
MR_RSI_OVERSOLD = 35        # Buy when RSI < 35
MR_PRICE_VWAP_DISCOUNT = 0.02  # Buy when price is 2% below VWAP

# === IMPROVEMENT 1: Tighter PARTIAL_TAKE ===
# PARTIAL_TAKE_50% has 100% WR on 34 trades (avg $132 win).
# Move from +5% to +3% to capture more of these high-probability wins earlier.
PARTIAL_TAKE = 0.03         # Sell 50% at 3% profit (was 5%)

# === IMPROVEMENT 2: Auto-blacklist losers based on history ===
# Symbols with negative P&L AND <50% WR (with >=3 trades) get auto-blacklisted
AUTO_BLACKLIST_ENABLED = True
AUTO_BLACKLIST_MIN_TRADES = 3   # Need at least this many closed trades
AUTO_BLACKLIST_MAX_WIN_RATE = 0.50  # Below this WR = blacklist
AUTO_BLACKLIST_MAX_PNL = -100   # And below this P&L = blacklist
AUTO_BLACKLIST_FILE = "/Users/amandaai/.openclaw/workspace/trading_bot/auto_blacklist.json"

# === IMPROVEMENT 3: Required volume confirmation ===
# Volume > 1.0x average required for entries (was a soft 10pt bonus)
REQUIRE_VOLUME_CONFIRMATION = True
MIN_VOLUME_RATIO = 1.0      # Volume must be >= average

# === IMPROVEMENT 4: Stronger trend filter ===
# Require price > EMA20 by minimum margin (was just "above")
REQUIRE_TREND_STRENGTH = True
MIN_TREND_MARGIN_PCT = 0.5  # Price must be 0.5% above EMA20 (or below for shorts)

# === IMPROVEMENT 5: Trailing stop starts at PARTIAL_TAKE threshold ===
# After taking 50% profit at PARTIAL_TAKE, trail remaining to lock gains
TRAILING_START = 0.03       # Start trailing at 3% profit (matches PARTIAL_TAKE)
TRAILING_DISTANCE = 0.025   # Keep 2.5% below peak (tighter than before)

# === IMPROVEMENT 6: Skip high-volatility entries ===
# If ATR > 4%, intraday noise makes management unreliable
MAX_ENTRY_ATR_PCT = 4.0     # Skip entries when ATR > 4% of price

# === IMPROVEMENT 7: Time-based entry quality ===
# First 60 min and last 30 min historically lose money
EXTENDED_DANGER_ZONE = True
OPEN_DANGER_MINUTES = 60    # Skip entries first 60 min (was 30)
CLOSE_DANGER_MINUTES = 30   # Skip entries last 30 min

# === NEW: SECTOR COOLDOWN ===
SECTOR_COOLDOWN_HOURS = 24     # Wait 24h after STOP_LOSS in sector
_sector_cooldown = {}          # Internal: tracks cooldown end times

# === EVENT GUARD (CPI / PPI / FOMC) ===
# The bot's other risk gates are all intraday-SHAPE gates (danger zone, kill
# zone, cooldown, daily loss). None of them knew about the CALENDAR, so the
# bot was structurally willing to open a fresh position straight into a CPI
# print -- exactly where the rate path this book trades on gets repriced.
# Governs NEW ENTRIES ONLY. Protective exits are never gated by this: a
# stop-loss that cannot fire because CPI is tomorrow would be a disaster.
EVENT_GUARD_ENABLED = True

def check_sector_cooldown(symbol):
    """Check if sector is in cooldown after STOP_LOSS"""
    global _sector_cooldown
    
    sectors = {
        "AAPL": "tech", "NVDA": "tech", "MSFT": "tech", "GOOGL": "tech", "AMZN": "consumer_tech",
        "META": "tech", "TSLA": "tech", "AMD": "tech", "INTC": "tech",
        "JPM": "finance", "BAC": "finance", "V": "finance", "MA": "finance",
        "JNJ": "healthcare", "UNH": "healthcare", "PFE": "healthcare", "ABBV": "healthcare",
        "WMT": "consumer", "COST": "consumer", "PG": "consumer", "KO": "consumer",
        "QQQ": "index", "SPY": "index", "IWM": "index", "VOO": "index", "DIA": "index",
        "XLK": "sector_tech", "XLE": "sector_energy", "XLF": "sector_finance", "XLV": "sector_health",
        "GLD": "commodity", "SLV": "commodity", "TLT": "bond"
    }
    
    sector = sectors.get(symbol, "other")
    
    # Check if cooldown active
    from datetime import datetime, timedelta
    cooldown_end = _sector_cooldown.get(sector)
    if cooldown_end and datetime.now() < cooldown_end:
        remaining = (cooldown_end - datetime.now()).total_seconds() / 3600
        return False, f"Sector cooldown: {remaining:.1f}h left"
    
    return True, None

def set_sector_cooldown(symbol):
    """Set sector cooldown after STOP_LOSS"""
    global _sector_cooldown
    
    sectors = {
        "AAPL": "tech", "NVDA": "tech", "MSFT": "tech", "GOOGL": "tech", "AMZN": "consumer_tech",
        "META": "tech", "TSLA": "tech", "AMD": "tech", "INTC": "tech",
        "JPM": "finance", "BAC": "finance", "V": "finance", "MA": "finance",
        "JNJ": "healthcare", "UNH": "healthcare", "PFE": "healthcare", "ABBV": "healthcare",
        "WMT": "consumer", "COST": "consumer", "PG": "consumer", "KO": "consumer",
        "QQQ": "index", "SPY": "index", "IWM": "index", "VOO": "index", "DIA": "index",
        "XLK": "sector_tech", "XLE": "sector_energy", "XLF": "sector_finance", "XLV": "sector_health",
        "GLD": "commodity", "SLV": "commodity", "TLT": "bond"
    }
    
    from datetime import datetime, timedelta
    sector = sectors.get(symbol, "other")
    _sector_cooldown[sector] = datetime.now() + timedelta(hours=SECTOR_COOLDOWN_HOURS)
    print(f"   ⚠️ Sector {sector} cooldown set for {SECTOR_COOLDOWN_HOURS}h")

# === PROFIT ALERTS (NEW) ===
PROFIT_ALERT_1 = 0.08      # Alert at 8%
PROFIT_ALERT_2 = 0.10      # Alert at 10%
PROFIT_ALERT_3 = 0.15      # Alert at 15%
ENABLE_PROFIT_ALERTS = True  # Enable profit alerts

# === MOMENTUM EXTENSION (NEW) ===
TRAILING_STOP = True        # Enable trailing stop
# TRAILING_START, TRAILING_DISTANCE, and PARTIAL_TAKE are configured above
# in the IMPROVEMENT 1 and IMPROVEMENT 5 sections.
TRAILING_START = 0.03       # Start trailing at 3% profit (overrides IMPROVEMENT 5)
TRAILING_DISTANCE = 0.025   # Keep 2.5% below peak
PARTIAL_TAKE = 0.03         # Take 50% profit at 3% (overrides IMPROVEMENT 1)

# === VOLATILITY & TIME FILTERS (NEW) ===
AVOID_OPEN_30MIN = True     # Avoid first 30 min of market open
AVOID_CLOSE_30MIN = True    # Avoid last 30 min of market close
HIGH_VOLATILITY_CAP = 0.10  # Cap position at 10% in high volatility
LOW_VOLATILITY = 1.2        # Boost position by 20% in low volatility

# RSI Optimized Parameters
RSI_PERIOD = 14             # Standard period
RSI_OVERSOLD = 35          # Adjusted
RSI_OVERBOUGHT = 65        # Adjusted
RSI_MID = 50

# MACD Parameters
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Markets - Expanded portfolio
STOCKS = [
    # Tech (40%)
    "AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AMD", "INTC",
    # Consumer (20%)
    "AMZN", "WMT", "COST", "PG", "KO",
    # Healthcare (20%)
    "JNJ", "UNH", "PFE", "ABBV",
    # Finance (20%)
    "JPM", "BAC", "V", "MA"
]
ETFS = [
    # US Indices
    "QQQ", "SPY", "IWM", "VOO", "DIA",
    # Bonds/Commodities
    "GLD", "SLV", "TLT",
    # Sector
    "XLK", "XLE", "XLF", "XLV"
]
# Symbol whitelist - only trade these (sorted by performance)
SYMBOL_WHITELIST = ['SLV', 'AMD', 'NVDA', 'XLE', 'QQQ', 'XLK', 'AAPL', 'BAC', 'ABBV']

# Symbol blacklist - skip these entirely
SYMBOL_BLACKLIST = ['INTC', 'PG', 'GOOGL', 'META', 'WMT', 'MSFT', 'AMZN', 'KO', 'JNJ', 'MA', 'PFE', 'TSLA', 'UNH', 'V']

# === TIER 1.3: Symbol tier system for position sizing ===
# Tier A: high WR (≥77%) — size up 1.3x
# Tier B: positive realized P&L — default 1.0x
# Tier C: anything else — default 1.0x
SYMBOL_TIERS = {
    "A": ["SLV", "BAC", "QQQ", "NVDA", "JPM", "AMD"],
    "B": ["XLK", "XLE", "COST", "AAPL"],
}
TIER_MULTIPLIER = {"A": 1.3, "B": 1.0, "C": 1.0}

def get_symbol_tier(symbol):
    """Return tier letter (A/B/C) for a symbol; 'C' if unlisted."""
    for tier, symbols in SYMBOL_TIERS.items():
        if symbol in symbols:
            return tier
    return "C"

# Combined universe (deduped, then whitelist + blacklist filtered)
_raw_universe = list(dict.fromkeys(STOCKS + ETFS))  # preserve order, dedupe
ALL_SYMBOLS = [s for s in _raw_universe if s not in SYMBOL_BLACKLIST]


_MARKET_CLOCK_CACHE = {"ts": 0.0, "is_open": None, "source": None}


def _us_eastern_now():
    """Return an aware US/Eastern datetime without relying on a local import."""
    now_utc = datetime.now(timezone.utc)
    if ZoneInfo is not None:
        return now_utc.astimezone(ZoneInfo("America/New_York"))
    import pytz
    return now_utc.astimezone(pytz.timezone("US/Eastern"))


def _fallback_market_state(now=None):
    """Weekday-only market state used when Alpaca's clock is unavailable.

    This is deliberately conservative for entries: callers mark the clock as
    a fallback and block new BUY orders.  It still allows protective exits to
    be evaluated on an ordinary weekday.
    """
    now = now or _us_eastern_now()
    weekday = now.weekday()  # Monday=0, Friday=4
    minutes = now.hour * 60 + now.minute
    is_weekday = weekday <= 4
    return {
        "is_open": bool(is_weekday and 9 * 60 + 30 <= minutes <= 16 * 60),
        "weekday": is_weekday,
        "minutes": minutes,
    }


def _alpaca_market_state(max_age=60.0):
    """Return (is_open, source), where source is alpaca or local_fallback."""
    import time as _time
    now = _time.time()
    cached = _MARKET_CLOCK_CACHE
    if cached.get("is_open") is not None and (now - cached.get("ts", 0)) < max_age:
        return bool(cached["is_open"]), cached.get("source") or "unknown"

    result = None
    source = "local_fallback"
    try:
        r = requests.get("https://api.alpaca.markets/v2/clock",
                         headers=HEADERS, timeout=10)
        if r.status_code == 200:
            payload = r.json()
            result = bool(payload.get("is_open"))
            source = "alpaca"
    except Exception:
        result = None

    if result is None:
        fallback = _fallback_market_state()
        result = fallback["is_open"]
        print(f"   ⚠️ Alpaca clock unavailable — local weekday fallback "
              f"says open={result} (new BUY entries remain disabled)")

    cached["ts"] = now
    cached["is_open"] = result
    cached["source"] = source
    return result, source

def _alpaca_market_is_open(max_age=60.0):
    """Authoritative market-open state from Alpaca's clock API.

    Cached briefly because get_current_time_info() is called several times per
    cycle. Falls back to a local weekday+clock-time check if the API is
    unreachable — better than the old clock-time-only check, which ignored
    weekends entirely.
    """
    result, _source = _alpaca_market_state(max_age=max_age)
    return result


def get_current_time_info():
    """Get current time in US Eastern for market hours"""
    try:
        now_est = _us_eastern_now()
    except Exception:
        return {
            "est_hour": 0,
            "est_minute": 0,
            "is_market_open": False,
            "is_danger_zone": True,
            "is_kill_zone": True,
            "entries_allowed": False,
            "clock_source": "unavailable",
            "time_str": "unknown ET",
        }

    hour = now_est.hour
    minute = now_est.minute

    # Market hours: 9:30 - 16:00 EST
    market_open = 9 * 60 + 30  # 9:30 AM
    market_close = 16 * 60     # 4:00 PM
    current_minutes = hour * 60 + minute

    # Market open must come from Alpaca's clock, not from clock-time alone.
    # The old check (9:30 <= hh:mm <= 16:00 ET) had NO weekday or holiday
    # test, so the bot thought the market was open every Saturday and Sunday
    # and submitted orders on closed days (11 each on Oct 25 / Oct 31).
    # Alpaca's /v2/clock is authoritative: weekends AND market holidays.
    is_market_open, clock_source = _alpaca_market_state()

    # === IMPROVEMENT 7: Extended danger zone (60 min open, 30 min close) ===
    is_danger_zone = False
    open_skip = OPEN_DANGER_MINUTES if EXTENDED_DANGER_ZONE else 30
    close_skip = CLOSE_DANGER_MINUTES if EXTENDED_DANGER_ZONE else 30
    if AVOID_OPEN_30MIN and current_minutes < market_open + open_skip:
        is_danger_zone = True
    if AVOID_CLOSE_30MIN and current_minutes > market_close - close_skip:
        is_danger_zone = True

    return {
        "est_hour": hour,
        "est_minute": minute,
        "is_market_open": is_market_open,
        "is_danger_zone": is_danger_zone,
        "is_kill_zone": 14 <= hour <= 16,  # 14:00-16:00 EST - BUY entries prohibited
        # If the authoritative Alpaca clock is unavailable, protective exits
        # may still be evaluated on a normal weekday, but new entries must be
        # blocked so a holiday/network problem can never trigger a BUY.
        "entries_allowed": bool(is_market_open and clock_source == "alpaca"),
        "clock_source": clock_source,
        "time_str": now_est.strftime("%H:%M ET")
    }


def get_volatility_adjustment(symbol):
    """Calculate position size based on volatility"""
    data = get_bars(symbol, timeframe="1Hour", limit=50)
    if not data:
        return 1.0  # Default no adjustment

    close = data['close']
    high = data['high']
    low = data['low']

    # Calculate ATR as % of price
    atr = Indicators.ATR(high, low, close)
    current_price = close[-1]
    atr_pct = (atr / current_price) * 100

    # High volatility = lower position
    if atr_pct > 3.0:
        return HIGH_VOLATILITY_CAP / MAX_POSITION_PCT  # Cap at 10%
    # Low volatility = boost position
    elif atr_pct < 1.0:
        return LOW_VOLATILITY
    else:
        return 1.0


def detect_market_regime():
    """Detect current market regime: BULL, BEAR, or SIDEWAYS"""
    # Use SPY to determine market direction
    spy_data = get_bars("SPY", timeframe="1Day", limit=50)
    if not spy_data:
        return "SIDEWAYS", 1.0  # Default
    
    close = np.array(spy_data['close'])
    
    # Calculate trend using SMA
    sma_20 = np.mean(close[-20:])
    sma_50 = np.mean(close[-50:]) if len(close) >= 50 else sma_20
    current_price = close[-1]
    
    # Calculate volatility
    returns = np.diff(close) / close[:-1]
    volatility = np.std(returns) * 100
    
    # Determine regime
    if sma_20 > sma_50 * 1.02:  # 2% above 50-day MA
        regime = "BULL"
        confidence_boost = 1.2  # More aggressive in bull
    elif sma_20 < sma_50 * 0.98:  # 2% below 50-day MA
        regime = "BEAR"
        confidence_boost = 0.7  # More conservative in bear
    else:
        regime = "SIDEWAYS"
        confidence_boost = 1.0
    
    return regime, confidence_boost


def check_correlation_limit(new_symbol, current_positions):
    """Check if adding new position would exceed correlation limit"""
    if not current_positions:
        return True  # No positions yet
    
    # Define sector correlations (simplified)
    sectors = {
        "AAPL": "tech", "NVDA": "tech", "MSFT": "tech", "GOOGL": "tech", "AMZN": "consumer_tech",
        "META": "tech", "TSLA": "tech", "AMD": "tech", "INTC": "tech",
        "JPM": "finance", "BAC": "finance", "V": "finance", "MA": "finance",
        "JNJ": "healthcare", "UNH": "healthcare", "PFE": "healthcare", "ABBV": "healthcare",
        "WMT": "consumer", "COST": "consumer", "PG": "consumer", "KO": "consumer",
        "QQQ": "index", "SPY": "index", "IWM": "index", "VOO": "index", "DIA": "index",
        "XLK": "sector", "XLE": "sector", "XLF": "sector", "XLV": "sector"
    }
    
    new_sector = sectors.get(new_symbol, "other")
    sector_count = 0
    
    for sym in current_positions:
        if sectors.get(sym, "other") == new_sector:
            sector_count += 1
    
    # Max 2 positions per sector
    return sector_count < 2


# === EVOLUTION: Self-improvement loading ===
EVOLUTION_DIR = "/Users/amandaai/.openclaw/workspace/trading_bot"

def _load_evolution_file(name):
    """Load an evolution JSON file (symbol_confidence, position_sizing, etc)."""
    path = Path(EVOLUTION_DIR) / name
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return None

def get_symbol_confidence_threshold(symbol):
    """Get evolution-tuned confidence threshold for a symbol.
    Returns None if no tuning exists (use default).
    """
    data = _load_evolution_file("symbol_confidence.json")
    if not data:
        return None
    entry = data.get(symbol)
    if entry and isinstance(entry, dict):
        return entry.get("threshold")
    return None

def get_symbol_position_multiplier(symbol):
    """Get evolution-tuned position size multiplier for a symbol.
    Returns 1.0 if no tuning exists.

    Blacklist always wins: a blacklisted symbol gets 0.0 regardless of what
    the evolution table says. Otherwise the two tables can disagree and a
    blacklisted name can still be sized (the Nov-2026 table had 13 symbols
    at 0.0 — including QQQ, the best symbol in the book — purely because
    the evolution sample was poisoned by $0 cleanup rows).
    """
    if symbol in SYMBOL_BLACKLIST:
        return 0.0
    try:
        if symbol in get_auto_blacklist():
            return 0.0
    except Exception:
        pass
    data = _load_evolution_file("symbol_position.json")
    if not data:
        return 1.0
    entry = data.get(symbol)
    if entry and isinstance(entry, dict):
        return entry.get("multiplier", 1.0)
    return 1.0

def is_hour_blocked(est_hour):
    """Check if a given hour is blocked by evolution learning."""
    data = _load_evolution_file("time_blocklist.json")
    if not data:
        return False
    blocked = data.get("blocked_hours", [])
    return est_hour in blocked

def is_feature_disabled(reason):
    """Check if a 'reason' (feature tag) has been disabled by evolution."""
    data = _load_evolution_file("disabled_features.json")
    if not data:
        return False
    disabled = data.get("disabled", [])
    return reason in disabled


_DISABLED_FEATURES_CACHE = {"ts": 0.0, "features": set()}


def disabled_features(max_age=60.0):
    """Return the currently disabled evolution feature names (cached briefly)."""
    now = time.time()
    cached = _DISABLED_FEATURES_CACHE
    if now - cached.get("ts", 0) < max_age:
        return set(cached.get("features", set()))
    data = _load_evolution_file("disabled_features.json") or {}
    values = data.get("disabled", data.get("disabled_features", []))
    features = {str(value) for value in values if value}
    cached["ts"] = now
    cached["features"] = features
    return features


def resolve_optimal_confidence(regime_boost=1.0):
    """Resolve the adaptive BUY threshold without ever raising.

    The old fallback referenced ``s`` before the signal loop assigned it.
    A failure in the learning module therefore crashed the entire trading
    cycle, including protective exits.  This pure wrapper fails closed to a
    conservative threshold instead.
    """
    try:
        value = get_optimal_confidence()
        value = int(value * regime_boost)
    except Exception as exc:
        print(f"   ⚠️ Adaptive confidence unavailable ({exc}); "
              f"using safe threshold {MIN_CONFIDENCE_BUY}")
        return MIN_CONFIDENCE_BUY
    return max(25, min(value, 50))


def fetch_account_state():
    """Fetch account state safely.

    Returns (account, portfolio, error).  On failure ``account`` is empty and
    ``portfolio`` is zero.  The caller must not invent equity or buying power.
    """
    try:
        response = requests.get(f"{BASE_URL}/v2/account",
                                headers=HEADERS, timeout=15)
        if response.status_code != 200:
            return {}, 0.0, f"account HTTP {response.status_code}"
        account = response.json()
        if not isinstance(account, dict):
            return {}, 0.0, "account response was not an object"
        portfolio = float(account.get("portfolio_value", 0) or 0)
        if portfolio <= 0:
            return {}, 0.0, "account returned non-positive portfolio_value"
        return account, portfolio, None
    except Exception as exc:
        return {}, 0.0, str(exc)


def execute_mean_reversion_entries(mr_signals, positions, account, portfolio,
                                   time_info, auto_blacklist, trades):
    """Evaluate mean-reversion entries once per cycle with normal risk gates."""
    if not mr_signals:
        return
    if not account:
        print("   → MR entries skipped: account state unavailable")
        return
    if not time_info.get("entries_allowed", True):
        print("   → MR entries skipped: authoritative market clock unavailable")
        return

    for symbol, _mr_conf in mr_signals.items():
        if symbol in positions:
            continue
        if len(positions) >= MAX_POSITIONS:
            print(f"   → {symbol} MR: max positions reached")
            break
        if time_info.get("is_danger_zone", False):
            print(f"   → {symbol} MR: danger zone")
            continue
        if time_info.get("is_kill_zone", False):
            print(f"   → {symbol} MR: kill zone")
            continue
        # === EVENT GUARD: CPI/PPI/FOMC + earnings ===
        _guard = time_info.get('event_guard') or {}
        if _guard.get('block_entries'):
            print(f"   → {symbol} MR: blocked by event guard ({_guard.get('reason')})")
            continue
        if symbol and str(symbol).upper() in (_guard.get('block_symbols') or set()):
            print(f"   → {symbol} MR: earnings today — blocked")
            continue
        if symbol in auto_blacklist:
            print(f"   → {symbol} MR: auto-blacklisted")
            continue
        if symbol in SYMBOL_BLACKLIST:
            print(f"   → {symbol} MR: blacklisted")
            continue
        if SYMBOL_WHITELIST and symbol not in SYMBOL_WHITELIST:
            print(f"   → {symbol} MR: not in whitelist")
            continue
        can_buy, cooldown_msg = check_sector_cooldown(symbol)
        if not can_buy:
            print(f"   → {symbol} MR: {cooldown_msg}")
            continue
        if not check_correlation_limit(symbol, positions):
            print(f"   → {symbol} MR: correlation limit")
            continue

        data = get_bars(symbol, timeframe="5Min", limit=10)
        if not data:
            continue
        price = data["close"][-1]
        available_bp = float(account.get("buying_power", portfolio * 0.5))
        qty = int((available_bp * 0.03) / price)
        if qty <= 0:
            print(f"   → {symbol} MR: computed quantity is zero")
            continue

        print(f"   📉 MR Buying {symbol}: {qty} shares")
        if place_order(symbol, qty, "buy"):
            trades.append(symbol)
            positions[symbol] = {
                "qty": qty,
                "avg_price": price,
                "peak_price": price,
                "is_mr": True,
            }
            print(f"   ✅ Bought {symbol} (Mean Reversion)")


def check_mean_reversion_signal(symbol):
    """Check for mean reversion opportunity (for ETFs)"""
    if not MEAN_REVERSION_ENABLED or symbol not in ETF_LIST:
        return None, 0
    
    data = get_bars(symbol, timeframe="1Hour", limit=100)
    if not data:
        return None, 0
    
    close = np.array(data['close'])
    high = np.array(data['high'])
    low = np.array(data['low'])
    volume = np.array(data['volume'])
    
    current_price = close[-1]
    
    # Calculate indicators
    rsi = Indicators.RSI(close, RSI_PERIOD)
    vwap = Indicators.VWAP(high, low, close, volume)
    
    # Mean reversion conditions
    # vwap is a scalar (last value), so use it directly
    price_vs_vwap = (current_price - vwap) / vwap if vwap != 0 else 0
    
    # Buy signal: price below VWAP + RSI oversold
    # rsi is a scalar (float), so use it directly
    if rsi < MR_RSI_OVERSOLD and price_vs_vwap < -MR_PRICE_VWAP_DISCOUNT:
        confidence = 40 + (MR_RSI_OVERSOLD - rsi)  # More oversold = higher confidence
        return "BUY", min(confidence, 60)
    
    return None, 0


def get_bars(symbol, timeframe="5Min", limit=100):
    """Get stock bars — with data quality validation (Tier 1.1 idle-bug fix)."""
    url = f"{DATA_URL}/v2/stocks/{symbol}/bars"
    params = {"timeframe": timeframe, "limit": limit}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if r.status_code != 200:
            print(f"   ⚠️ get_bars({symbol}) HTTP {r.status_code}")
            return None
        data = r.json()
        # Alpaca returns {"bars": null} for symbols with no recent data (e.g. delisted,
        # halted, or no trades today). Guard against len(None) crash.
        bars = data.get("bars") if isinstance(data, dict) else None
        if bars and len(bars) > 0:
            close, high, low, volume = [], [], [], []
            for b in data["bars"]:
                c, h, l, v = b.get("c"), b.get("h"), b.get("l"), b.get("v")
                if c is None or h is None or l is None or v is None:
                    continue
                if c <= 0 or h <= 0 or l <= 0 or v < 0:
                    continue
                close.append(float(c))
                high.append(float(h))
                low.append(float(l))
                volume.append(float(v))
            if len(close) < 30:
                return None
            return {'close': close, 'high': high, 'low': low, 'volume': volume}
    except Exception as e:
        print(f"   ⚠️ get_bars({symbol}) error: {e}")
    return None


def get_positions():
    """Get current positions"""
    url = f"{BASE_URL}/v2/positions"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if not isinstance(data, list):
                return {}
            positions = {}
            for p in data:
                if not isinstance(p, dict):
                    continue
                symbol = p.get("symbol")
                if not symbol:
                    continue
                try:
                    positions[symbol] = {
                        "qty": int(p.get("qty", 0)),
                        "avg_price": float(p.get("avg_entry_price", 0)),
                        "current_price": float(p.get("current_price", 0)),
                        # Preserve side so P&L math can be direction-aware
                        "side": str(p.get("side", "long")).lower(),
                    }
                except (ValueError, TypeError):
                    continue
            return positions
    except Exception as e:
        print(f"Position error: {e}")
    return {}


class Indicators:
    """Optimized Technical Indicators"""

    @staticmethod
    def RSI(prices, period=10):
        if len(prices) < period:
            return 50.0
        # Tier 1.1: Validate inputs — drop NaN, reject flat lines
        prices = np.asarray(prices, dtype=float)
        prices = prices[~np.isnan(prices)]
        if len(prices) < period:
            return 50.0
        if np.std(prices) == 0:
            return 50.0
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def EMA(prices, period):
        if len(prices) < period:
            return prices[-1]
        df = pd.Series(prices)
        return df.ewm(span=period, adjust=False).mean().iloc[-1]

    @staticmethod
    def MACD(prices, fast=12, slow=26, signal=9):
        if len(prices) < slow:
            return {"macd": 0, "signal": 0, "histogram": 0, "crossover": None}

        ema_fast = Indicators.EMA(prices, fast)
        ema_slow = Indicators.EMA(prices, slow)
        macd = ema_fast - ema_slow

        # Signal line
        macd_vals = [macd] * signal
        sig = Indicators.EMA(macd_vals, signal)

        # Previous values for crossover detection
        prev_macd = macd
        prev_sig = sig

        histogram = macd - sig

        # Detect crossover
        crossover = None
        if prev_macd <= prev_sig and macd > sig:
            crossover = "BULLISH"
        elif prev_macd >= prev_sig and macd < sig:
            crossover = "BEARISH"

        return {"macd": macd, "signal": sig, "histogram": histogram, "crossover": crossover}

    @staticmethod
    def ATR(high, low, close, period=14):
        """Average True Range - volatility measure"""
        if len(high) < period + 1:
            return 0

        tr = []
        for i in range(1, len(close)):
            h_l = high[i] - low[i]
            h_c = abs(high[i] - close[i-1])
            l_c = abs(low[i] - close[i-1])
            tr.append(max(h_l, h_c, l_c))

        return np.mean(tr[-period:])

    @staticmethod
    def StochasticRSI(prices, rsi_period=10, stoch_period=14):
        """Stochastic RSI - more sensitive"""
        rsi = Indicators.RSI(prices, rsi_period)

        if len(prices) < stoch_period:
            return 50, "NEUTRAL"

        # Get RSI values over stoch_period
        rsi_values = []
        for i in range(len(prices) - stoch_period + 1):
            subset = prices[i:i+stoch_period]
            rsi_values.append(Indicators.RSI(subset, rsi_period))

        if len(rsi_values) < 2:
            return 50, "NEUTRAL"

        stoch_rsi = 100 * (rsi - min(rsi_values)) / (max(rsi_values) - min(rsi_values) + 0.001)

        zone = "NEUTRAL"
        if stoch_rsi < 20:
            zone = "OVERSOLD"
        elif stoch_rsi > 80:
            zone = "OVERBOUGHT"

        return stoch_rsi, zone

    @staticmethod
    def VWAP(high, low, close, volume):
        """Volume Weighted Average Price - good for intraday"""
        if len(high) < 1:
            return close[-1]

        typical_price = [(h + l + c) / 3 for h, l, c in zip(high, low, close)]
        vwap = np.cumsum([tp * v for tp, v in zip(typical_price, volume)]) / np.cumsum(volume)
        return vwap[-1] if len(vwap) > 0 else typical_price[-1]

    @staticmethod
    def BollingerBands(prices, period=20, std_dev=2):
        """Bollinger Bands - volatility bands"""
        if len(prices) < period:
            return prices[-1], prices[-1], prices[-1]

        sma = np.mean(prices[-period:])
        std = np.std(prices[-period:])

        upper = sma + (std_dev * std)
        lower = sma - (std_dev * std)

        return upper, sma, lower

    @staticmethod
    def VolumeConfirmation(volume, period=20):
        """Check if volume is above average, using COMPLETED bars only.

        Fix (2026-11-02): Alpaca's last 5-min bar is still forming, so its
        volume is a fraction of a real bar and always fails a ratio>=1.0
        gate — which silently killed every single signal (analyze_symbol
        returned None for all 33 symbols, 0 scan lines per cycle).

        Compare the last COMPLETED bar (volume[-2]) against the average of
        the 20 completed bars before it.
        """
        if len(volume) < period + 2:
            return 1.0

        completed = volume[-(period + 2):-2]   # 20 completed bars
        current_volume = volume[-2]              # last completed bar

        avg_volume = np.mean(completed)
        if avg_volume <= 0:
            return 1.0
        return current_volume / avg_volume


def get_auto_blacklist():
    """Compute dynamic blacklist from historical stats.
    Symbols with enough trades AND poor performance get auto-blacklisted.
    Returns a set of symbols to skip.
    """
    if not AUTO_BLACKLIST_ENABLED:
        return set()

    try:
        stats = load_stats()
        symbol_stats = stats.get("symbol_stats", {})
    except Exception:
        return set()

    blacklist = set()
    for symbol, data in symbol_stats.items():
        if not isinstance(data, dict):
            continue
        total = data.get("total", 0)
        wins = data.get("wins", 0)
        pnl = data.get("pnl", 0)
        if total < AUTO_BLACKLIST_MIN_TRADES:
            continue
        win_rate = wins / total if total > 0 else 0

        # Standard rule: WR <50% AND P&L < -$100
        standard_blacklist = (win_rate < AUTO_BLACKLIST_MAX_WIN_RATE
                             and pnl < AUTO_BLACKLIST_MAX_PNL)

        # Loss-outlier rule (Tier 1.2 enhancement): if any single loss > 3x avg win,
        # the symbol has dangerous tail risk even with high WR. Blacklist regardless of WR.
        # Catches PFE-type patterns (80% WR but one $437 loss wipes all $28 wins).
        journal = load_journal()
        sym_trades = [t for t in journal if t.get("symbol") == symbol and t.get("closed")]
        sym_losses = [abs(t.get("pnl", 0)) for t in sym_trades if t.get("pnl", 0) < 0]
        sym_wins = [t.get("pnl", 0) for t in sym_trades if t.get("pnl", 0) > 0]
        outlier_blacklist = False
        if sym_losses and sym_wins:
            avg_win = sum(sym_wins) / len(sym_wins)
            max_loss = max(sym_losses) if sym_losses else 0
            if avg_win > 0 and max_loss > 3 * avg_win:
                outlier_blacklist = True

        if standard_blacklist or outlier_blacklist:
            blacklist.add(symbol)

    # Persist for inspection
    try:
        import json as _json
        with open(AUTO_BLACKLIST_FILE, "w") as f:
            _json.dump({"blacklist": sorted(list(blacklist)),
                        "generated": __import__("datetime").datetime.now().isoformat()},
                       f, indent=2)
    except Exception:
        pass

    return blacklist


def analyze_symbol(symbol):
    """Advanced analysis with optimized strategy"""
    data = get_bars(symbol)
    if not data or len(data['close']) < 30:
        return None

    close = data['close']
    high = data['high']
    low = data['low']
    volume = data['volume']

    # Calculate all indicators
    rsi = Indicators.RSI(close[-20:], RSI_PERIOD)
    rsi_stoch, stoch_zone = Indicators.StochasticRSI(close[-20:])
    macd = Indicators.MACD(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    ema20 = Indicators.EMA(close, 20)
    ema50 = Indicators.EMA(close, 50)
    atr = Indicators.ATR(high, low, close)
    vwap = Indicators.VWAP(high, low, close, volume)
    bb_upper, bb_middle, bb_lower = Indicators.BollingerBands(close)
    volume_ratio = Indicators.VolumeConfirmation(volume)

    current_price = close[-1]

    # ATR-based stop loss (adaptive)
    atr_pct = (atr / current_price) * 100

    # === IMPROVEMENT 6: Skip high-volatility entries ===
    # ATR > 4% = too noisy for reliable management
    if atr_pct > MAX_ENTRY_ATR_PCT:
        return None

    # === IMPROVEMENT 3: Required volume confirmation ===
    # No entries on low-volume days (price action unreliable)
    if REQUIRE_VOLUME_CONFIRMATION and volume_ratio < MIN_VOLUME_RATIO:
        return None

    # Trend
    trend = "SIDEWAYS"
    if current_price > ema20 > ema50:
        trend = "BULL"
    elif current_price < ema20 < ema50:
        trend = "BEAR"
    elif current_price > ema20:
        trend = "UP"
    elif current_price < ema20:
        trend = "DOWN"

    # === IMPROVEMENT 4: Trend strength filter ===
    # Require meaningful distance from EMA20, not just "above"
    if REQUIRE_TREND_STRENGTH:
        ema_distance_pct = abs((current_price - ema20) / ema20 * 100)
        if ema_distance_pct < MIN_TREND_MARGIN_PCT:
            # Price too close to EMA = weak trend, skip
            if trend in ["UP", "DOWN"]:
                trend = "SIDEWAYS"

    # === SIGNAL GENERATION ===
    buy_score = 0
    sell_score = 0
    reasons = []

    # === TREND FILTER: Only buy in uptrend or sideways ===
    if trend in ["BULL", "UP"]:
        reasons.append(f"Trend: {trend}")
    elif trend in ["BEAR", "DOWN"]:
        # In downtrend, only sell signals
        sell_score += 20
        reasons.append(f"Trend filter: {trend}")

    # RSI - primary signal
    if rsi < RSI_OVERSOLD:
        buy_score += 30
        reasons.append(f"RSI oversold")
    elif rsi < 40:
        buy_score += 15
        reasons.append(f"RSI low")

    if rsi > RSI_OVERBOUGHT:
        sell_score += 30
        reasons.append(f"RSI overbought")
    elif rsi > 60:
        sell_score += 15

    # MACD - momentum confirmation
    if macd["crossover"] == "BULLISH":
        buy_score += 25
        reasons.append("MACD cross")
    elif macd["crossover"] == "BEARISH":
        sell_score += 25
        reasons.append("MACD cross")
    elif macd["histogram"] > 0:
        buy_score += 10
    elif macd["histogram"] < 0:
        sell_score += 10

    # Stochastic RSI
    if stoch_zone == "OVERSOLD":
        buy_score += 15
    elif stoch_zone == "OVERBOUGHT":
        sell_score += 15

    # Trend boost — SIDEWAYS gets baseline (was: 0 points)
    if trend in ["BULL", "UP"]:
        buy_score += 10
    elif trend == "SIDEWAYS":
        buy_score += 5  # Phase 1 fix: support for range-bound entries
        reasons.append("Trend: SIDEWAYS (mean-reversion baseline)")
    elif trend in ["BEAR", "DOWN"]:
        sell_score += 10

    # RSI crossing 50
    if rsi > 50 and close[-1] > ema20:
        buy_score += 10
    elif rsi < 50 and close[-1] < ema20:
        sell_score += 10

    # Volume confirmation — count regardless of trend (price action, not trend)
    if volume_ratio > 1.5:
        if current_price > ema20:
            buy_score += 10
        elif current_price < ema20:
            sell_score += 10
        reasons.append(f"High volume: {volume_ratio:.1f}x")

    # VWAP confirmation — count regardless of trend (price action)
    if current_price > vwap:
        buy_score += 10
        reasons.append("Above VWAP")
    elif current_price < vwap:
        sell_score += 10
        reasons.append("Below VWAP")

    # Bollinger Bands - mean reversion
    if current_price < bb_lower:
        buy_score += 15
        reasons.append("At lower BB")
    elif current_price > bb_upper:
        sell_score += 15
        reasons.append("At upper BB")

    # Final signal - require trend alignment
    signal = "HOLD"
    confidence = 0

    # === EVOLUTION: Apply per-symbol confidence threshold ===
    symbol_threshold = get_symbol_confidence_threshold(symbol)
    if symbol_threshold is None:
        symbol_threshold = 35  # default

    # For BUY: require uptrend or sideways
    if buy_score >= symbol_threshold and trend in ["BULL", "UP", "SIDEWAYS"]:
        signal = "BUY"
        confidence = buy_score
    # For SELL: require downtrend or sideways
    elif sell_score >= 35:
        signal = "SELL"
        confidence = sell_score

    return {
        "symbol": symbol,
        "price": current_price,
        "rsi": rsi,
        "stoch_rsi": rsi_stoch,
        "macd": macd,
        "trend": trend,
        "atr": atr,
        "atr_pct": atr_pct,
        "vwap": vwap,
        "volume_ratio": volume_ratio,
        "signal": signal,
        "confidence": confidence,
        "reasons": reasons,
        "buy_score": buy_score,
        "sell_score": sell_score
    }


# === Per-position state persistence (Fix 8 — 2026-10-23) ===
# positions = get_positions() rebuilds the dict from the API every cycle, so
# in-memory flags (partial_taken, peak_price) were lost between cycles.
# That let a partial-take fire repeatedly (liquidating the position in
# ~10 cycles instead of taking half then trailing the rest).
POSITION_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "position_state.json")

def load_position_state(positions):
    """Merge persisted per-position flags into this cycle's positions dict."""
    try:
        with open(POSITION_STATE_FILE) as f:
            saved = json.load(f)
    except Exception:
        saved = {}
    if isinstance(saved, dict):
        for sym, pos in positions.items():
            st = saved.get(sym)
            if isinstance(st, dict):
                for k in ("partial_taken", "peak_price"):
                    if k in st and st[k] is not None:
                        pos[k] = st[k]
    return positions

def save_position_state(positions):
    """Persist the per-position flags we need to survive a cycle boundary."""
    try:
        out = {}
        for sym, pos in positions.items():
            if not isinstance(pos, dict):
                continue
            st = {}
            if pos.get("partial_taken"):
                st["partial_taken"] = True
            if pos.get("peak_price") is not None:
                st["peak_price"] = pos["peak_price"]
            if st:
                out[sym] = st
        with open(POSITION_STATE_FILE, "w") as f:
            json.dump(out, f, indent=2)
    except Exception as e:
        print(f"   ⚠️ position state save failed: {e}")

def check_exit(symbol, position):
    """Smart exit with ATR-based stops + trailing stop + profit alerts"""
    data = get_bars(symbol)
    close = None
    if data:
        close = data['close']
        current_price = close[-1]
    else:
        # Bars unavailable (transient API failure, thin volume, or a closed
        # session). Previously this returned (None, None) and the position's
        # stop was silently skipped — the same class of bug as the
        # analyze_symbol gate. The positions endpoint already carries a
        # current_price for held positions, so use it: price-based exits
        # (partial take / hard stop / take profit / profit alert / trailing)
        # still work. Indicator-based exits (RSI/MACD) need a close series
        # and are skipped when there is none.
        current_price = position.get("current_price")
        if not current_price:
            return None, None
    entry_price = position["avg_price"]

    # Direction-aware P&L: a SHORT gains when price falls.
    # Previously long-formula only, which inverted every short position's P&L.
    _qty = position.get("qty", 0) or 0
    _side = str(position.get("side", "long")).lower()
    is_short = (_side == "short") or (_qty < 0)
    if is_short:
        pl_pct = (entry_price - current_price) / entry_price * 100
    else:
        pl_pct = (current_price - entry_price) / entry_price * 100

    # === PARTIAL TAKE PROFIT at 5% ===
    # First: Sell 50% to lock in partial profit
    if PARTIAL_TAKE > 0 and pl_pct >= PARTIAL_TAKE * 100:
        if position.get("partial_taken", False) == False:
            position["partial_taken"] = True
            return "PARTIAL_TAKE_50%", f"+{pl_pct:.1f}% - Selling 50%"

    # Tier 1.2: RE-ENABLED hard stop at -2% (was disabled 2026-04-29 because
    # 0% WR on its own, but unlimited losses are worse than tight stops.
    # RSI_EXIT can fire deep in the red — we need a hard ceiling to cap damage.)
    if pl_pct <= -(STOP_LOSS_PCT * 100):
        _stop_detail = (f"{pl_pct:.1f}% (hard cut @ -{STOP_LOSS_PCT*100:.0f}%)"
                        if is_short else
                        f"-{abs(pl_pct):.1f}% (hard cut @ -{STOP_LOSS_PCT*100:.0f}%)")
        return "HARD_STOP", _stop_detail

    # === TAKE PROFIT at 8% (remaining 50%) ===
    # After partial take at 5%, sell remaining at 8%
    if pl_pct >= TAKE_PROFIT_PCT * 100:
        return "TAKE_PROFIT", f"+{pl_pct:.1f}% - Selling remaining"

    # === PROFIT ALERTS (8%, 10%, 15%) ===
    # Only alert if we still have position after partial take
    if ENABLE_PROFIT_ALERTS and position.get("partial_taken", False):
        if pl_pct >= PROFIT_ALERT_3 * 100 and not position.get("alert_15", False):
            position["alert_15"] = True
            return "PROFIT_ALERT_15", f"+{pl_pct:.1f}%"
        elif pl_pct >= PROFIT_ALERT_2 * 100 and not position.get("alert_10", False):
            position["alert_10"] = True
            return "PROFIT_ALERT_10", f"+{pl_pct:.1f}%"
        elif pl_pct >= PROFIT_ALERT_1 * 100 and not position.get("alert_8", False):
            position["alert_8"] = True
            return "PROFIT_ALERT_8", f"+{pl_pct:.1f}%"

    # === TRAILING STOP after 8% ===
    if TRAILING_STOP and pl_pct >= TRAILING_START * 100:
        if is_short:
            # For a short the best price is the LOWEST seen; trail up from there.
            peak_price = position.get("peak_price", entry_price)
            if current_price < peak_price:
                position["peak_price"] = current_price
                peak_price = current_price
            trail_price = peak_price * (1 + TRAILING_DISTANCE)
            if current_price >= trail_price:
                return "TRAILING_STOP", f"+{pl_pct:.1f}%"
        else:
            peak_price = position.get("peak_price", entry_price)
            if current_price > peak_price:
                position["peak_price"] = current_price
                peak_price = current_price
            trail_price = peak_price * (1 - TRAILING_DISTANCE)
            if current_price <= trail_price:
                return "TRAILING_STOP", f"+{pl_pct:.1f}%"

    # === RSI EXIT === (needs a close series — skipped when bars unavailable)
    if close:
        rsi = Indicators.RSI(close[-20:])
        if rsi > 85:  # Changed from 75 to 85 (less aggressive exit)
            return "RSI_EXIT", f"RSI={rsi:.0f}"

        # === MACD EXIT ===
        macd = Indicators.MACD(close)
        if macd["crossover"] == "BEARISH":
            return "MACD_EXIT", "Bearish cross"

    return None, None


def place_order_chunked(symbol, qty, side, price, reduce_only=False, max_attempts=8):
    """Place an exit order in chunks sized to available buying power.

    Fix (2026-11-02): covering the SLV short needed $278,922 but buying
    power was only $224,403 — a single order is rejected with 403
    "insufficient buying_power" no matter how the fill is verified. Split
    the close into affordable chunks and keep going until covered.
    """
    remaining = int(qty)
    if remaining <= 0:
        return False

    covered = 0
    for _ in range(max_attempts):
        if remaining <= 0:
            break

        chunk = remaining
        try:
            r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=10)
            if r.status_code == 200:
                bp = float(r.json().get("buying_power", 0))
                if bp > 0 and price > 0:
                    affordable = int((bp * 0.95) / price)
                    if affordable < 1:
                        print(f"   ⚠️ Insufficient buying power (${bp:,.2f}) for even 1 {symbol} — skipping")
                        break
                    chunk = min(remaining, affordable)
        except Exception:
            pass

        if chunk < remaining:
            print(f"   ℹ️ Splitting close: {remaining} → {chunk} sh (buying-power limited)")

        if place_order(symbol, chunk, side, reduce_only=reduce_only):
            covered += chunk
            remaining -= chunk
            time.sleep(0.5)
        else:
            print(f"   ⚠️ Chunk of {chunk} failed — {covered}/{qty} covered")
            break

    if covered:
        print(f"   ✅ Covered {covered}/{qty} shares")
    return covered > 0


def place_order(symbol, qty, side, reduce_only=False):
    """Place order — waits for a terminal fill status.

    Fix (2026-11-02): Alpaca returns 200 for BOTH "filled" and "accepted"
    orders, and a market order for a large position is usually "accepted"
    (not instantly filled). Reading filled_qty from the immediate POST
    response therefore reported a false failure and CANCELLED the order —
    which is why the SLV short (6,094 sh) could never be closed: 11
    consecutive "accepted but not filled" cancellations in one session.

    Now we poll GET /v2/orders/{id} until the order reaches a terminal
    status before deciding. Partial fills count as fills.
    """
    if not symbol or not qty or qty <= 0:
        return False

    url = f"{BASE_URL}/v2/orders"
    data = {
        "symbol": symbol,
        "qty": str(int(qty)),
        "side": str(side).lower(),
        "type": "market",
        "time_in_force": "day"
    }
    if reduce_only:
        # Close-only semantics: Alpaca rejects the order rather than letting
        # it flip the position past zero. Safety net for exit orders.
        data["reduce_only"] = True

    TERMINAL_FILLED = {"filled"}
    TERMINAL_DEAD = {"canceled", "expired", "rejected", "pending_cancel"}

    try:
        r = requests.post(url, headers=HEADERS, json=data, timeout=30)
        if r.status_code not in [200, 201]:
            print(f"   Order failed: {r.status_code} - {r.text[:100]}")
            return False

        order = r.json()
        order_id = order.get("id")
        status = order.get("status", "unknown")
        filled_qty = float(order.get("filled_qty", 0))

        # Not instantly filled -> poll until terminal status.
        if order_id and status not in TERMINAL_FILLED and status not in TERMINAL_DEAD:
            print(f"   ⏳ Order {status} — polling for fill...")
            deadline = time.time() + 60
            while time.time() < deadline:
                time.sleep(2)
                try:
                    chk = requests.get(f"{BASE_URL}/v2/orders/{order_id}", headers=HEADERS, timeout=10)
                    if chk.status_code != 200:
                        continue
                    order = chk.json()
                    status = order.get("status", "unknown")
                    filled_qty = float(order.get("filled_qty", 0))
                    if status in TERMINAL_FILLED or status in TERMINAL_DEAD:
                        break
                except Exception:
                    continue

        if filled_qty > 0 and status == "filled":
            print(f"   ✅ Filled: {int(filled_qty)} sh @ {order.get('filled_avg_price', '?')}")
            return True

        if filled_qty > 0:
            # Partial fill — some qty executed, so the exit did happen.
            print(f"   ⚠️ Partial fill: {int(filled_qty)}/{int(qty)} sh @ {order.get('filled_avg_price', '?')}")
            return True

        print(f"   ⚠️ Order not filled (status={status}). Cancelling.")
        if order_id:
            try:
                requests.delete(f"{BASE_URL}/v2/orders/{order_id}", headers=HEADERS, timeout=10)
            except Exception:
                pass
        return False
    except Exception as e:
        print(f"   Order error: {e}")
    return False


def run_trading():
    """Main trading loop - runs continuously every LOOP_INTERVAL_MINUTES"""
    print("🚀 Starting V9 Trading Loop")
    print(f"   Checking every {LOOP_INTERVAL_MINUTES} minutes during market hours")
    
    while True:
        time_info = get_current_time_info()
        
        if time_info['is_market_open']:
            print(f"\n{'='*60}")
            print(f"🔄 Cycle at {time_info['time_str']} - Market OPEN")
            print(f"{'='*60}")
            try:
                do_trading_cycle()
            except Exception:
                # A transient API/data error must not kill the long-running
                # process and leave open positions unmanaged until the next
                # scheduled launch.
                print("⚠️ Trading cycle failed; process will continue:")
                traceback.print_exc()
        else:
            print(f"\n⏰ {time_info['time_str']} - Market CLOSED")
            print("   Sleeping until next market open check...")

            # === EVOLUTION: Daily self-improvement pass ===
            # Run once per day at first market-closed check after market close
            _last_evo_key = "/tmp/nova_evolution_last_run.txt"
            from pathlib import Path as _P
            _today = datetime.now().strftime("%Y-%m-%d")
            try:
                _last_run = _P(_last_evo_key).read_text().strip() if _P(_last_evo_key).exists() else ""
            except Exception:
                _last_run = ""
            if _last_run != _today:
                # Only run if market just closed (within 2h of close)
                if time_info['est_hour'] >= 16 and time_info['est_hour'] <= 20:
                    print("\n🧬 Running daily evolution pass...")
                    try:
                        import evolution as _evo
                        _evo.run_evolution(verbose=True)
                    except Exception as _e:
                        print(f"   ⚠️ Evolution pass failed: {_e}")
                    try:
                        _P(_last_evo_key).write_text(_today)
                    except Exception:
                        pass

        print(f"\n💤 Sleeping {LOOP_INTERVAL_MINUTES} minutes until next check...")

        # === Equity curve snapshot (Fix 2 — 2026-09-06) ===
        # Persist portfolio + open-position count per cycle. Fixes stale equity_curve_v6.json
        # (was last updated 2026-06-19; current code never wrote to it).
        try:
            _eq_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "equity_curve_v6.json")
            _eq_hist = []
            if os.path.exists(_eq_path):
                try:
                    with open(_eq_path) as _eqf:
                        _eq_hist = json.load(_eqf)
                except Exception:
                    _eq_hist = []
            # Use live account equity if available, else fall back to portfolio var
            _eq_portfolio = portfolio if 'portfolio' in dir() else 0
            _eq_today_pl_pct = 0.0
            try:
                _acct_r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=10)
                if _acct_r.status_code == 200:
                    _a = _acct_r.json()
                    _eq_portfolio = float(_a.get("portfolio_value", _eq_portfolio))
                    _eq_today_pl = float(_a.get("equity", 0)) - float(_a.get("last_equity", 0))
                    _eq_today_pl_pct = (_eq_today_pl / _eq_portfolio * 100) if _eq_portfolio > 0 else 0.0
            except Exception:
                pass
            _eq_hist.append({
                "timestamp": datetime.now().isoformat(),
                "portfolio": _eq_portfolio,
                "open_positions": len(get_positions()),
                "daily_pl_pct": round(_eq_today_pl_pct, 4),
            })
            _eq_hist = _eq_hist[-1000:]  # keep last 1000 entries
            with open(_eq_path, "w") as _eqf:
                json.dump(_eq_hist, _eqf, indent=2)
        except Exception as _eq_e:
            print(f"   ⚠️ Equity curve write failed: {_eq_e}")

        time.sleep(LOOP_INTERVAL_MINUTES * 60)


def do_trading_cycle():
    """Single trading cycle - analyze and trade once"""
    print("\n" + "="*60)
    print("🤖 Nova's Trading Bot V9 - Self-Learning")
    print("="*60)

    # Check time first
    time_info = get_current_time_info()
    print(f"⏰ Time: {time_info['time_str']}")

    # === EVENT GUARD: compute ONCE per cycle ===
    # The guard governs NEW ENTRIES only. Protective exits are never gated by
    # it: a stop-loss that cannot fire because CPI is tomorrow would be far
    # worse than a missed trade.
    if EVENT_GUARD_ENABLED and event_guard_state is not None:
        try:
            time_info['event_guard'] = event_guard_state()
            _g = time_info['event_guard']
            if _g.get('block_entries'):
                print(f"🗓️  Event guard: BLOCKING new entries — {_g.get('reason')}")
            elif _g.get('soft_events'):
                _names = ", ".join(e.get('name', '?') for e in _g['soft_events'])
                print(f"🗓️  Event guard: half size — {_names} approaching")
            for _note in (_g.get('notes') or []):
                print(f"⚠️  Event guard: {_note}")
        except Exception as _ge:
            print(f"⚠️ Event guard unavailable ({_ge}) — entries unaffected")
            time_info['event_guard'] = {}
    else:
        time_info['event_guard'] = {}

    if not time_info['is_market_open']:
        print("⚠️ Market closed - skipping")
        return

    if time_info['is_danger_zone'] and (AVOID_OPEN_30MIN or AVOID_CLOSE_30MIN):
        print("⚠️ Danger zone (first/last 30 min) - skipping entries")

    # === EVOLUTION: Check if this hour is blocked by learning ===
    if is_hour_blocked(time_info['est_hour']):
        print(f"⛔ Hour {time_info['est_hour']} blocked by evolution learning (historical losses)")
        return

    # === NEW: MARKET REGIME DETECTION ===
    try:
        regime, regime_boost = detect_market_regime()
        print(f"📈 Market regime: {regime} (boost: {regime_boost}x)")
    except:
        regime = "SIDEWAYS"
        regime_boost = 1.0

    # Get adaptive confidence from learning.  This helper never raises.
    optimal_conf = resolve_optimal_confidence(regime_boost)
    print(f"🧠 Adaptive confidence: {optimal_conf} (regime-adjusted)")

    # Get account.  Never fabricate equity: a failed account request blocks
    # new entries and leaves the daily-loss circuit breaker in an explicit
    # unknown state.  Protective exits are still evaluated below.
    account, portfolio, account_error = fetch_account_state()
    account_ok = account_error is None
    if not account_ok:
        print(f"🛑 Account state unavailable ({account_error}) — "
              f"new BUY entries blocked; protective exits will still be evaluated")
    positions = get_positions()
    # Restore persisted per-position flags (partial_taken / peak_price)
    load_position_state(positions)

    # === NEW: DAILY LOSS CHECK ===
    # Get today's P&L from account
    if account_ok:
        try:
            today_pl = float(account.get("profit_loss", 0))
            today_pl_pct = today_pl / portfolio if portfolio > 0 else 0
            print(f"📊 Today's P&L: ${today_pl:,.2f} ({today_pl_pct*100:.2f}%)")

            if today_pl_pct <= -MAX_DAILY_LOSS:
                print(f"🛑 DAILY LOSS LIMIT REACHED ({today_pl_pct*100:.1f}%) - Stopping trading")
                return
        except Exception:
            # An account payload without a usable P&L cannot prove the loss
            # limit is safe.  Block entries, but continue protective exits.
            print("🛑 Account P&L unavailable — new BUY entries blocked")
            account_ok = False

    if not account_ok:
        print("Portfolio: unavailable")
    else:
        print(f"Portfolio: ${portfolio:,.2f}")
    print(f"Positions: {list(positions.keys())} ({len(positions)}/{MAX_POSITIONS})")

    # === NEW: CHECK MAX POSITIONS ===
    if len(positions) >= MAX_POSITIONS:
        print(f"⚠️ Max positions ({MAX_POSITIONS}) reached - skipping new entries")
    
    trades = []
    exits = []

    print("\n📊 Analyzing...")

    # === NEW: ANALYZE MEAN REVERSION SIGNALS FIRST ===
    mr_signals = {}
    if MEAN_REVERSION_ENABLED and regime != "BEAR":
        print("\n📊 Checking mean reversion (ETFs)...")
        for symbol in ETF_LIST:
            mr_signal, mr_conf = check_mean_reversion_signal(symbol)
            if mr_signal == "BUY":
                mr_signals[symbol] = mr_conf
                print(f"  📉 {symbol}: Mean Reversion BUY ({mr_conf}%)")

    # === IMPROVEMENT 2: Compute auto-blacklist from history ===
    auto_blacklist = get_auto_blacklist()
    if auto_blacklist:
        print(f"\n🚫 Auto-blacklisted (poor history): {sorted(auto_blacklist)}")

    for symbol in ALL_SYMBOLS:
        # Skip auto-blacklisted symbols
        if symbol in auto_blacklist:
            continue
        analysis = analyze_symbol(symbol)
        # Defect fix: exits were gated behind ENTRY analysis succeeding, so a
        # held position whose entry analysis failed (e.g. volume-confirmation
        # gate) was never stop-checked. Exit logic must run for held positions
        # regardless of entry-analysis outcome.
        if not analysis and symbol not in positions:
            continue

        # Bind s/c up front. A held symbol whose ENTRY analysis failed still
        # needs its exit evaluated, but there is no entry signal for it.
        s = None
        c = None

        # Display
        if analysis:
            s = analysis['signal']
            c = analysis['confidence']
            emoji = "🟢" if s == "BUY" else "🔴" if s == "SELL" else "⚪"

            print(f"{emoji} {symbol}: ${analysis['price']:.2f} | RSI:{analysis['rsi']:.0f} Stoch:{analysis['stoch_rsi']:.0f} | {analysis['trend'][:4]} | {s}({c}%)")

        # Check exits
        if symbol in positions:
            exit_reason, exit_details = check_exit(symbol, positions[symbol])
            if exit_reason:
                # Handle profit alerts (notify only, don't sell)
                if "PROFIT_ALERT" in exit_reason:
                    print(f"   📢 {exit_reason}: {exit_details}")
                    try:
                        alert_msg = f"📢 *Profit Alert - {symbol}*\n\nProfit: {exit_details}\nAction: Review and decide whether to sell!"
                        send_telegram(alert_msg)
                    except:
                        pass
                else:
                    # Actual exit - sell
                    print(f"   → EXIT: {exit_reason} {exit_details}")
                    
                    # Calculate P&L (direction-aware: a SHORT gains as price falls)
                    pos = positions[symbol]
                    entry_price = pos.get("avg_price") or pos.get("entry_price") or 0
                    pos_qty = pos.get("qty", 0) or 0
                    is_short_pos = (str(pos.get("side", "long")).lower() == "short") or (pos_qty < 0)
                    abs_qty = abs(pos_qty)
                    ref_price = pos.get("current_price") or (analysis['price'] if analysis else entry_price)
                    
                    # For partial take: close half
                    close_qty = abs_qty
                    if "PARTIAL_TAKE" in exit_reason:
                        close_qty = max(1, abs_qty // 2)
                    
                    if is_short_pos:
                        pnl = (entry_price - ref_price) * close_qty
                    else:
                        pnl = (ref_price - entry_price) * close_qty

                    # Capture the exit facts before a full close removes the
                    # position from ``positions``.  The old code looked up the
                    # already-deleted row and then fell back to an unrelated
                    # BUY quantity, which could be unbound on an exit-first
                    # cycle and silently dropped realized P&L from learning.
                    rec_qty = close_qty
                    rec_price = ref_price
                    rec_conf = positions.get(symbol, {}).get("confidence", 50)
                    
                    # Order qty must be positive; place_order rejects qty<=0.
                    #
                    # CRITICAL (2026-10-31): the side must match the
                    # position direction. Selling to close a SHORT ADDS to the
                    # short — which is exactly what happened: repeated "sell
                    # SLV" exits grew the position from -1,022 to -6,094 shares
                    # (market value -$271,792 on a $147,579 portfolio).
                    # Closing a short requires BUYING.
                    exit_side = "buy" if is_short_pos else "sell"
                    if place_order_chunked(symbol, close_qty, exit_side, ref_price, reduce_only=True):
                        if "PARTIAL_TAKE" in exit_reason:
                            # Keep remaining position (qty must move TOWARD zero)
                            if is_short_pos:
                                positions[symbol]["qty"] = pos_qty + close_qty
                            else:
                                positions[symbol]["qty"] = pos_qty - close_qty
                            positions[symbol]["partial_taken"] = True
                            print(f"   ✅ Sold {close_qty} shares, keeping {positions[symbol]['qty']}")
                        else:
                            # Trigger sector cooldown on a stop-out.
                            # Defect fix: this checked for "STOP_LOSS", but
                            # check_exit() returns "HARD_STOP" — so the
                            # cooldown never fired on a real stop.
                            if "STOP_LOSS" in exit_reason or "HARD_STOP" in exit_reason:
                                set_sector_cooldown(symbol)
                            
                            exits.append(symbol)
                            del positions[symbol]
                            print(f"   ✅ Sold {symbol}")
                        
                        # Record trade for learning with P&L
                        try:
                            record_trade("SELL", symbol, rec_qty, rec_price, 
                                        exit_reason, rec_conf, 
                                        [exit_reason], pnl)
                            print(f"   📚 Learning recorded")
                        except Exception as e:
                            print(f"   ⚠️ Learning record failed: {e}")

        # Entry signals — log rejected BUY signals for debugging
        elif s == "BUY" and c >= optimal_conf:
            if symbol in positions:
                print(f"   → Already owned")
                continue

            # Account state and an authoritative market clock are mandatory
            # for new exposure.  Protective exits above remain available.
            if not account_ok:
                print(f"   → Skipped: account state unavailable")
                continue
            if not time_info.get("entries_allowed", True):
                print(f"   → Skipped: authoritative market clock unavailable")
                continue

            # No entry analysis for this symbol (e.g. volume-gate fail). The exit
            # check above already ran; there is no entry signal to evaluate.
            if not analysis:
                continue

            # Skip if max positions reached
            if len(positions) >= MAX_POSITIONS:
                print(f"   → Skipped: max positions ({MAX_POSITIONS})")
                continue
            
            # Skip if in danger zone (first/last 30 min)
            if time_info.get('is_danger_zone', False):
                print(f"   → Skipped: danger zone")
                continue
            
            # NEW: Check correlation limit
            if not check_correlation_limit(symbol, positions):
                print(f"   → Skipped: correlation limit (sector)")
                continue

            # Evolution may disable a signal reason (for example
            # "Trend: UP").  Enforce that trail for BUY entries; exits are
            # deliberately unaffected.
            disabled_reasons = [reason for reason in analysis.get("reasons", [])
                                if reason in disabled_features()]
            if disabled_reasons:
                print(f"   → Skipped: disabled by evolution "
                      f"({', '.join(disabled_reasons)})")
                continue
            
            # NEW: RSI Entry Filter - Don't buy if RSI so high it will
            # immediately trigger RSI_EXIT. RSI_EXIT fires at RSI > 85
            # (see check_exit), so this threshold was rejecting good entries
            # at RSI 50-85 for no reason — it killed SLV (RSI 62) and
            # QQQ (RSI 57) on Nov 9, both of which had positive edge.
            if analysis['rsi'] > 85:
                print(f"   → Skipped: RSI too high ({analysis['rsi']:.0f} > 85)")
                continue
            
            # NEW: Sector cooldown check (after STOP_LOSS)
            can_buy, cooldown_msg = check_sector_cooldown(symbol)
            if not can_buy:
                print(f"   → Skipped: {cooldown_msg}")
                continue
            
            # NEW: Symbol blacklist check
            if symbol in SYMBOL_BLACKLIST:
                print(f"   → Skipped: {symbol} in blacklist")
                continue
            
            # NEW: Kill zone check (14:00-16:00 EST - high volatility, bad BUY entries)
            if time_info.get('is_kill_zone', False):
                print(f"   → Skipped: kill zone (14-16h EST)")
                continue

            # === EVENT GUARD (CPI/PPI/FOMC) ===
            # Blocks or halves NEW entries around high-impact macro
            # releases. Exits are NOT gated here — protective exits must
            # always be able to fire.
            _guard = time_info.get('event_guard') or {}
            if _guard.get('block_entries'):
                print(f"   → Skipped: event guard — {_guard.get('reason')}")
                continue
            if symbol and str(symbol).upper() in (_guard.get('block_symbols') or set()):
                print(f"   → Skipped: {symbol} reports earnings today")
                continue
            event_mult = 1.0
            if entry_size_multiplier is not None:
                try:
                    event_mult = entry_size_multiplier(symbol, state=_guard)
                except Exception:
                    event_mult = 1.0
            if event_mult < 1.0:
                _sn = ", ".join(e.get('name', '?') for e in (_guard.get('soft_events') or []))
                print(f"   → Event guard: half size ({_sn} approaching)")
            
            # NEW: Whitelist check (if whitelist not empty, symbol must be in it)
            if SYMBOL_WHITELIST and symbol not in SYMBOL_WHITELIST:
                print(f"   → Skipped: {symbol} not in whitelist")
                continue
            
            # Signal-based position sizing: double good signals, halve BUY
            signal_mult = 1.0
            if analysis.get('signal') in ['RSI_EXIT', 'PARTIAL_TAKE_50%']:
                signal_mult = 2.0  # Double up on proven signals
            elif analysis.get('signal') == 'BUY':
                signal_mult = 0.5  # Halve BUY size (weak signal)
            
            # Volatility-based position sizing - use buying power conservatively
            # Cap overall position at 5% to avoid margin issues
            vol_adj = get_volatility_adjustment(symbol)

            # === EVOLUTION: Apply symbol-specific position multiplier ===
            # Bot has learned this symbol performs X% of average
            symbol_mult = get_symbol_position_multiplier(symbol)
            if symbol_mult == 0.0:
                print(f"   ⛔ {symbol}: blocked by evolution (multiplier=0)")
                continue

            # === TIER 2.1: Apply static tier multiplier (SLV/BAC/QQQ get1.3x, etc.) ===
            tier_mult = TIER_MULTIPLIER.get(get_symbol_tier(symbol), 1.0)
            tier_letter = get_symbol_tier(symbol)
            print(f"   📐 {symbol}: tier={tier_letter}  tier_mult={tier_mult}x  vol={vol_adj:.2f}  sym={symbol_mult:.2f}  sig={signal_mult:.2f}")

            actual_position = min(kelly_position_pct(symbol) * vol_adj * signal_mult * symbol_mult * tier_mult * event_mult, 0.05)  # Cap at 5%
            
            # Calculate qty on EQUITY, not buying power. On a paper account
            # buying_power is 4x equity, so a "5% cap" was silently a
            # 20%-of-equity position, and 15 positions could reach 300% gross.
            available_bp = float(account.get('portfolio_value', portfolio))

            # Enforce MAX_PORTFOLIO_RISK as a gross-exposure ceiling.
            # It was referenced exactly once in the whole repo (its own
            # definition) — the guardrail did not exist.
            gross_exposure = sum(
                abs(float(p.get('qty', 0) or 0)) * float(p.get('current_price', 0) or 0)
                for p in positions.values()
            )
            new_position_value = available_bp * actual_position
            if gross_exposure + new_position_value > portfolio * MAX_PORTFOLIO_RISK:
                print(f"   → Skipped: gross exposure would be "
                      f"{(gross_exposure + new_position_value) / max(portfolio, 1) * 100:.1f}% "
                      f"> MAX_PORTFOLIO_RISK {MAX_PORTFOLIO_RISK * 100:.0f}%")
                continue

            qty = int((available_bp * actual_position) / analysis['price'])
            if qty <= 0:
                print(f"   → Skipped: computed quantity is zero")
                continue
            if qty > 0:
                vol_note = f" (vol adj: {vol_adj:.1f}x)" if vol_adj != 1.0 else ""
                print(f"   → Buying {qty} shares...{vol_note}")
                if place_order(symbol, qty, "buy"):
                    trades.append(symbol)
                    positions[symbol] = {"qty": qty, "avg_price": analysis['price'], "peak_price": analysis['price']}
                    print(f"   ✅ Bought {symbol}")
                    
                    # Record trade for learning
                    try:
                        record_trade("BUY", symbol, qty, analysis['price'], s, c, analysis.get('reasons', []))
                        print(f"   📚 Learning recorded")
                    except Exception as e:
                        print(f"   ⚠️ Learning record failed: {e}")

        elif s == "BUY" and c < optimal_conf:
            # Log filtered BUY signals (was previously silent — could not see why signals weren't firing)
            print(f"   🔇 {symbol} BUY filtered: conf={c} < threshold={optimal_conf} (regime={regime})")
            continue

    # Mean-reversion entries are evaluated once per cycle, after the normal
    # symbol scan.  The old block lived inside that scan and was therefore
    # re-evaluated for every symbol, bypassing several risk gates.
    execute_mean_reversion_entries(mr_signals, positions, account, portfolio,
                                   time_info, auto_blacklist, trades)
    # Persist per-position flags at the end of the symbol loop, while
    # `positions` is still in scope, so partial_taken / peak_price
    # survive the cycle boundary.
    save_position_state(positions)


    # Summary
    print("\n" + "="*30)
    if trades:
        print(f"🎉 BOUGHT: {', '.join(trades)}")
    if exits:
        print(f"👋 SOLD: {', '.join(exits)}")
    if not trades and not exits:
        print("💤 No signals")
    
    # Show learning insights
    try:
        performance = analyze_performance()
        if performance.get("status") == "analyzed":
            print("\n🧠 Learning Insights:")
            print(f"   Win Rate: {performance.get('win_rate', 0):.1f}%")
            print(f"   Total P&L: ${performance.get('total_pnl', 0):.2f}")
            if performance.get("suggestions"):
                for suggestion in performance["suggestions"][:2]:
                    print(f"   💡 {suggestion['message']}")
    except Exception as e:
        pass

    print("✅ Done!")

    # Log
    try:
        with open("/Users/amandaai/.openclaw/workspace/trading_bot/v7_journal.json", "a") as f:
            f.write(json.dumps({
                "time": datetime.now().isoformat(),
                "trades": trades,
                "exits": exits
            }) + "\n")
    except:
        pass


# === Single-instance guard (Fix 9 — 2026-10-23) ===
# ai.nova.trading-bot and ai.nova.realtime-trading BOTH exec this file.
# Two concurrent instances corrupt trades.log (duplicate/interleaved cycle
# stamps) and can double-place orders. An advisory lock makes a second
# instance exit cleanly instead of racing the first.
#
# NOTE: the guard must only run when this file is executed as a script.
# At module level it would kill any `import auto_trade` (walk_forward.py
# imports it) with SystemExit(0) whenever the bot is running.
def _acquire_single_instance_lock():
    """Return True if we may proceed (we are the only instance)."""
    try:
        import fcntl as _fcntl
    except ImportError:
        return True  # non-POSIX: skip guard rather than fail
    lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto_trade.lock")
    fh = open(lock_path, "w")
    try:
        _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except (OSError, IOError):
        fh.close()
        print("🔴 Another auto_trade.py instance is already running (lock held). Exiting cleanly — "
              "two instances must not trade the same account.")
        raise SystemExit(0)
    fh.write(str(os.getpid()))
    fh.flush()
    globals()['_LOCK_FH'] = fh  # keep alive for process lifetime
    return True


if __name__ == "__main__":
    _acquire_single_instance_lock()
    run_trading()
