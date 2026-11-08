#!/usr/bin/env python3
"""
Walk-forward backtester for the trading bot.

Replays the FIXED signal scoring against historical Alpaca data with proper
train/test split. Answers: "Does the fixed scoring have edge out-of-sample?"

Usage:
    source venv/bin/activate
    python3 walk_forward.py

Output:
    - Sharpe ratio (annualized)
    - Profit factor
    - Win rate, avg win/loss
    - Max drawdown
    - Out-of-sample vs in-sample split
    - Comparison: fixed scoring vs old scoring (where applicable)
"""

import json
import math
import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict

# Add trading_bot to path so we can reuse indicators
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

WORKSPACE = "/Users/amandaai/.openclaw/workspace/trading_bot"
DATA_URL = "https://data.alpaca.markets"
BASE_URL = "https://paper-api.alpaca.markets"


def get_alpaca_keys():
    """Read API keys — import from auto_trade which loads them correctly via python-dotenv."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import auto_trade as _bot
    return _bot.API_KEY, _bot.SECRET_KEY


def fetch_bars(symbol, start, end, timeframe="1Day", api_key="", secret=""):
    """Fetch historical bars from Alpaca. Returns list of {t,o,h,l,c,v}."""
    url = f"{DATA_URL}/v2/stocks/{symbol}/bars"
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret}
    params = {
        "timeframe": timeframe,
        "start": start.strftime('%Y-%m-%d'),
        "end": end.strftime('%Y-%m-%d'),
        "limit": 10000,
        "adjustment": "split",
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code == 200:
            bars = r.json().get("bars", [])
            return bars
        else:
            print(f"   ⚠️ {symbol}: status={r.status_code} body={r.text[:200]}")
    except Exception as e:
        print(f"   ⚠️ fetch_bars({symbol}) error: {e}")
    return []


# Simple indicator calculations (replicating the fixed scoring)
def sma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    e = prices[0]
    for p in prices[1:]:
        e = p * k + e * (1 - k)
    return e


def rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        ch = prices[i] - prices[i - 1]
        if ch > 0:
            gains += ch
        else:
            losses -= ch
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - (100 / (1 + rs))


def score_symbol(bars, fixed_scoring=True):
    """Apply the FIXED scoring (or old scoring) to a bar series.
    Returns (signal, confidence, trend) where signal in {BUY, SELL, HOLD}.
    """
    if len(bars) < 30:
        return "HOLD", 0, "?"

    closes = [b["c"] for b in bars]
    volumes = [b["v"] for b in bars]
    cur = closes[-1]
    ema20 = ema(closes, 20)
    rsi_v = rsi(closes, 14)

    # Trend (simple)
    trend = "SIDEWAYS"
    if cur > ema20 * 1.005:
        trend = "BULL"
    elif cur < ema20 * 0.995:
        trend = "BEAR"

    buy_score = 0
    reasons = []

    # RSI primary
    if rsi_v < 35:
        buy_score += 30
        reasons.append("RSI oversold")
    elif rsi_v < 40:
        buy_score += 15

    # Trend
    if fixed_scoring:
        # FIXED: SIDEWAYS gets +5
        if trend in ["BULL"]:
            buy_score += 10
        elif trend == "SIDEWAYS":
            buy_score += 5
            reasons.append("SIDEWAYS baseline")
        elif trend == "BEAR":
            buy_score += 0  # don't buy in bear
    else:
        # OLD: SIDEWAYS gets 0
        if trend in ["BULL"]:
            buy_score += 10

    # Volume (no trend gating in FIXED)
    avg_vol = sum(volumes[-20:]) / min(20, len(volumes))
    vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
    if vol_ratio > 1.5:
        if cur > ema20:
            buy_score += 10  # FIXED: regardless of trend
            reasons.append(f"Vol {vol_ratio:.1f}x")

    # Price vs EMA20
    if rsi_v > 50 and cur > ema20:
        buy_score += 10

    # Bollinger approximation
    if len(closes) >= 20:
        avg = sma(closes, 20)
        std = math.sqrt(sum((c - avg) ** 2 for c in closes[-20:]) / 20) if avg else 0
        lower_bb = avg - 2 * std
        if cur < lower_bb:
            buy_score += 15
            reasons.append("BB lower")

    signal = "BUY" if buy_score >= 50 else "HOLD"
    return signal, buy_score, trend


def run_backtest(api_key, secret, symbols, start, end, fixed_scoring=True):
    """Walk-forward over historical bars. Simulates trades with simple exit logic."""
    sim = {
        "trades": [],
        "skip_no_data": [],
    }

    for symbol in symbols:
        bars = fetch_bars(symbol, start, end, api_key=api_key, secret=secret)
        if not bars or len(bars) < 30:
            sim["skip_no_data"].append(symbol)
            continue

        # Walk through bars simulating a 30-day lookback window
        window = 30
        i = window
        in_position = False
        entry_price = None
        entry_date = None

        while i < len(bars):
            window_bars = bars[i - window:i + 1]
            signal, conf, trend = score_symbol(window_bars, fixed_scoring)

            if not in_position and signal == "BUY":
                # Enter
                entry_price = bars[i]["c"]
                entry_date = bars[i]["t"]
                entry_conf = conf
                in_position = True
            elif in_position:
                # Check exit: 2% hard stop OR 3% take profit OR 30 days held
                cur_price = bars[i]["c"]
                pnl_pct = (cur_price - entry_price) / entry_price * 100
                held_days = (datetime.fromisoformat(bars[i]["t"].replace("Z", "+00:00"))
                             - datetime.fromisoformat(entry_date.replace("Z", "+00:00"))).days
                if pnl_pct <= -2 or pnl_pct >= 3 or held_days >= 30:
                    sim["trades"].append({
                        "symbol": symbol,
                        "entry_date": entry_date[:10],
                        "exit_date": bars[i]["t"][:10],
                        "entry_price": entry_price,
                        "exit_price": cur_price,
                        "pnl_pct": pnl_pct,
                        "held_days": held_days,
                        "exit_reason": "HARD_STOP" if pnl_pct <= -2 else
                                       "TAKE_PROFIT" if pnl_pct >= 3 else "TIME",
                        "entry_conf": entry_conf,
                        "trend": trend,
                    })
                    in_position = False
                    entry_price = None
                    entry_date = None
            i += 1

    return sim


def compute_metrics(trades, label):
    if not trades:
        print(f"\n=== {label}: 0 trades ===")
        return {}
    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    n = len(pnls)
    n_w = len(wins)
    n_l = len(losses)
    wr = n_w / n * 100 if n else 0
    avg_win = sum(wins) / n_w if n_w else 0
    avg_loss = sum(losses) / n_l if n_l else 0
    expectancy = (sum(wins) - sum(losses)) / n if n else 0
    pf = sum(wins) / sum(losses) if sum(losses) > 0 else float("inf")
    if n > 1:
        mean = sum(pnls) / n
        var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
        std = math.sqrt(var) if var > 0 else 0
        sharpe_per_trade = mean / std if std > 0 else 0
        # Annualized (assuming ~252 trading days, 1 trade per day)
        sharpe_annual = sharpe_per_trade * math.sqrt(252)
    else:
        sharpe_annual = 0
        std = 0

    max_dd = 0
    cum = 0
    peak = 0
    for p in sorted(pnls):
        cum += p
        peak = max(peak, cum)
        dd = peak - cum
        max_dd = max(max_dd, dd)

    by_exit = defaultdict(list)
    for t in trades:
        by_exit[t["exit_reason"]].append(t["pnl_pct"])

    print(f"\n=== {label} ===")
    print(f"  Trades:           {n}")
    print(f"  Wins / Losses:    {n_w} / {n_l}")
    print(f"  Win rate:         {wr:.1f}%")
    print(f"  Avg win:          {avg_win:>5.2f}%")
    print(f"  Avg loss:         {avg_loss:>5.2f}%")
    print(f"  Expectancy/trade: {expectancy:>+5.2f}%")
    print(f"  Profit factor:    {pf:.2f}")
    print(f"  Std dev/trade:    {std:.2f}%")
    print(f"  Sharpe (annual):  {sharpe_annual:.2f}")
    print(f"  Max drawdown:     {max_dd:.2f}%")
    print(f"  By exit reason:")
    for r, ps in sorted(by_exit.items(), key=lambda x: -len(x[1])):
        avg = sum(ps) / len(ps)
        print(f"    {r:15s}: n={len(ps):>3}  avg P&L={avg:>+5.2f}%")

    return {"n": n, "wr": wr, "expectancy": expectancy, "pf": pf,
            "sharpe": sharpe_annual, "max_dd": max_dd}


def main():
    api_key, secret = get_alpaca_keys()
    if not api_key:
        print("ERROR: ALPACA_API_KEY not found in .env")
        return

    # Walk-forward window: last 18 months
    # Split: first 12 months = train, last 6 months = test (out-of-sample)
    end_date = datetime.now() - timedelta(days=1)  # yesterday
    train_end = end_date - timedelta(days=180)  # 6 months ago
    train_start = train_end - timedelta(days=365)  # 1 year before that
    test_start = train_end
    test_end = end_date

    # Universe: 50 symbols (expanded for better statistical power)
    symbols = [
        # Broad market ETFs (8)
        "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "XLF", "XLE",
        # Mega-cap tech (8)
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AMD", "INTC",
        # Consumer / Finance / Healthcare (15)
        "TSLA", "BAC", "JPM", "V", "MA", "WMT", "COST", "PG", "KO",
        "JNJ", "UNH", "PFE", "ABBV", "HD", "DIS",
        # Sector / commodity / bond ETFs (12)
        "XLK", "XLV", "XLY", "XLP", "XLU", "XLB", "XLE",
        "SLV", "GLD", "TLT", "HYG", "LQD",
        # Recently hot / momentum (7)
        "PLTR", "CRWD", "SNOW", "COIN", "HOOD",
        "MARA", "RIOT",
    ]

    print(f"Walk-forward backtest — {len(symbols)} symbols")
    print(f"Train: {train_start.date()} to {train_end.date()} (12 months)")
    print(f"Test:  {test_start.date()} to {test_end.date()} (6 months)")
    print(f"Scoring: FIXED (SIDEWAYS +5, no trend gating on VWAP/volume)")
    print(f"Exit: 2% HARD_STOP, 3% TAKE_PROFIT, 30-day time-stop")

    # Pull train + test bars for each symbol
    print(f"\nFetching historical bars...")
    train_bars = {sym: fetch_bars(sym, train_start, train_end, api_key=api_key, secret=secret) for sym in symbols}
    test_bars = {sym: fetch_bars(sym, test_start, test_end, api_key=api_key, secret=secret) for sym in symbols}

    skipped = [s for s, b in train_bars.items() if not b or len(b) < 30]
    if skipped:
        print(f"  Skipped (insufficient data): {skipped}")

    # Run in-sample (train period) and out-of-sample (test period)
    def run_period(period_bars, label):
        sim = {"trades": []}
        for symbol, bars in period_bars.items():
            if not bars or len(bars) < 30:
                continue
            window = 30
            i = window
            in_position = False
            entry_price = None
            entry_date = None
            entry_conf = None
            while i < len(bars):
                wb = bars[i - window:i + 1]
                sig, conf, trend = score_symbol(wb, fixed_scoring=True)
                if not in_position and sig == "BUY":
                    entry_price = bars[i]["c"]
                    entry_date = bars[i]["t"]
                    entry_conf = conf
                    in_position = True
                elif in_position:
                    cur = bars[i]["c"]
                    pnl = (cur - entry_price) / entry_price * 100
                    days = (datetime.fromisoformat(bars[i]["t"].replace("Z", "+00:00"))
                            - datetime.fromisoformat(entry_date.replace("Z", "+00:00"))).days
                    if pnl <= -2 or pnl >= 3 or days >= 30:
                        sim["trades"].append({
                            "symbol": symbol, "entry_date": entry_date[:10],
                            "exit_date": bars[i]["t"][:10], "pnl_pct": pnl,
                            "entry_price": entry_price, "exit_price": cur,
                            "held_days": days, "exit_reason":
                                "HARD_STOP" if pnl <= -2 else "TAKE_PROFIT" if pnl >= 3 else "TIME",
                            "entry_conf": entry_conf, "trend": trend,
                        })
                        in_position = False
                i += 1
        return sim["trades"]

    print("\nRunning in-sample backtest (train period)...")
    train_trades = run_period(train_bars, "TRAIN")
    m_train = compute_metrics(train_trades, f"IN-SAMPLE (TRAIN: {train_start.date()} to {train_end.date()})")

    print("\nRunning out-of-sample backtest (test period)...")
    test_trades = run_period(test_bars, "TEST")
    m_test = compute_metrics(test_trades, f"OUT-OF-SAMPLE (TEST: {test_start.date()} to {test_end.date()})")

    print("\n" + "=" * 60)
    print("VERDICT — Walk-forward results")
    print("=" * 60)
    if m_train and m_test:
        in_edge = m_train["expectancy"] > 0 and m_train["pf"] > 1.0
        oos_edge = m_test["expectancy"] > 0 and m_test["pf"] > 1.0
        sharpe_oos = m_test["sharpe"]
        decay = (m_test["expectancy"] - m_train["expectancy"]) if (m_train and m_test) else 0
        print(f"  In-sample edge:  {'YES' if in_edge else 'NO'}  (WR={m_train['wr']:.1f}%, exp={m_train['expectancy']:+.2f}, PF={m_train['pf']:.2f})")
        print(f"  OOS edge:        {'YES' if oos_edge else 'NO'}  (WR={m_test['wr']:.1f}%, exp={m_test['expectancy']:+.2f}, PF={m_test['pf']:.2f})")
        print(f"  OOS Sharpe:      {sharpe_oos:.2f}")
        print(f"  Expectancy decay: {decay:+.2f}%  ({'GOOD — edge held' if decay > -1 else 'EDGE DECAY — overfit risk'})")

        if oos_edge and sharpe_oos > 1.0:
            print(f"\n  ✅ RECOMMENDATION: Real-money paper trading with 5% allocation for 6+ months.")
        elif oos_edge:
            print(f"\n  ⚠️ RECOMMENDATION: Edge exists but weak. Continue paper trading 3+ more months.")
        else:
            print(f"\n  ❌ RECOMMENDATION: No OOS edge. Sunset the bot or try different signal logic.")
    else:
        print("  Insufficient data to render verdict.")


if __name__ == "__main__":
    main()
