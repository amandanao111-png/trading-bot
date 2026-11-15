# 🤖 Nova's Trading Bot (V9 / self-learning)

AI-powered autonomous trading with self-learning capabilities.

> **Status: live, paper account, currently 100% cash.** Last equity $139,322.93
> with 0 open positions (as of 2026-11-14). The V2-era docs below were replaced
> on 2026-11-15 — they named files that no longer exist and showed a stale
> signal table.

## 🚀 Features

| Feature | Description |
|---------|-------------|
| **Paper Trading** | Alpaca paper account ($100,000 starting balance) |
| **Risk Management** | 10% portfolio risk per trade, hard stop at −3% |
| **Self-Learning** | Overnight optimiser rewrites confidence thresholds and the auto-blacklist from realised outcomes |
| **Technical Analysis** | RSI, ATR, moving averages, confirmation counts |
| **Auto-Trading** | Trades only inside market hours; sleeps otherwise |

## 📦 Files

```
auto_trade.py             # Live bot (V9 self-learning loop) — main entry point
auto_optimizer.py         # Overnight self-optimisation pass
technical_analysis.py     # RSI / ATR / indicator calculations
signal_generator.py       # Signal generation
pnl_reconciled.py         # AUTHORITATIVE P&L (reconciles Alpaca fills, hard-fails on imbalance)
pnl_reconciled_last.json  # Last reconciliation result
auto_config.json          # Optimiser configuration snapshot
auto_blacklist.json       # Symbols the optimiser has blacklisted
disabled_features.json    # Feature kill-switches
trade_journal.json        # Bot's own trade journal (75 closed trades)
evolution.json            # Self-learning state
position_state.json       # Open positions ({} = fully in cash)
docs/                     # Polymarket feasibility, practical guide, quick reference
README.md                 # This file
CHANGELOG.md              # Version history
```

**P&L source of truth:** `pnl_reconciled.py` only. The older `pnl_authoritative.py`,
`pnl_truth_final.py`, `pnl_truth*.py`, `pnl_cash.py`, `pnl_daily.py`,
`pnl_final.py`, `pnl_periods.py` and `pnl_weekly.py` are **superseded** — they
mis-read Alpaca's API (`side=sell` misses `sell_short`, `status=='filled'` drops
partially-filled cancellations, and `/v2/account/activities` rejects
`page_size=1000`). They remain in the tree for reference only; do not quote
their numbers.

## 🎯 Quick Start

```bash
# Set API keys (required!)
export ALPACA_API_KEY="your_key_here"
export ALPACA_API_SECRET="your_secret_here"

# Check signals
python3 technical_analysis.py

# Run trading bot
python3 auto_trade.py

# Reconcile true P&L against Alpaca fills
python3 pnl_reconciled.py
```

> ⚠️ **Security Note**: API keys are loaded from environment variables. Never
> hardcode secrets, and never paste keys into source files.

## 📊 Current State (2026-11-14)

| Item | Value |
|------|-------|
| Equity | $139,322.93 |
| Positions | 0 open (100% cash) |
| Last trade | 2026-11-13 15:40 — SELL QQQ 3 @ 673.66 (HARD_STOP, −$47.22) |
| Tradable universe | 7 names after the auto-blacklist: SLV, AMD, QQQ, XLK, AAPL, BAC, ABBV |
| Lifetime P&L | +$39,322.87 (+39.32% vs a $100K paper start), reconciled from 1,855 fills |
| Bot's own journal | 79 trades, 32W/43L, 42.7% win rate, −$6,071.47 |

> The old "Current Signals (V2)" table (MSFT / GOOGL / NVDA BUY) was stale and
> partly contradicted the bot's own blacklist — GOOGL and MSFT are blacklisted
> by the optimiser, and NVDA was blacklisted on 2026-11-14. Signals are now
> reported live in the dashboard digest, not in this README.

## 🛡️ Risk Management

| Rule | Value |
|------|-------|
| Max Position | 10% of portfolio per trade |
| Max Loss/Trade | max(3%, ATR × 1.5) — whichever is tighter |
| Max Daily Loss | 5% circuit breaker (no new entries) |
| Max Open Positions | 5 |
| Risk:Reward | 2:1 minimum (50% out at 1:1) |
| Entry Filter | RSI < 40 for longs, 2+ confirmations, confidence ≥ 45 |
| Sector Cap | Max 2 positions per sector |
| Cooldown | 48h after a stop-loss on the same symbol |

## 🔧 Setup / Switching Accounts

1. Get Alpaca API keys: https://app.alpaca.markets/
2. Export `ALPACA_API_KEY` and `ALPACA_API_SECRET` in the environment
3. Paper vs live is selected by the launchd plists
   (`ai.nova.trading-bot.plist`, `ai.nova.realtime-trading.plist`), not by
   editing source. Never hardcode a key or a paper flag in code.

## 🤖 Autonomous Trading

When market opens (9:30 AM ET):
1. Bot checks market hours
2. Scans for high-confidence signals
3. Places trades with stop losses
4. Monitors and learns

## 📚 Self-Learning

The bot learns from:
- Trade outcomes (profit/loss)
- Pattern recognition
- Error recovery

Lessons saved to: `learnings.json`

## ⚠️ Disclaimer

This bot is for **educational purposes only**.
- Not financial advice
- Past performance ≠ future results
- Always use paper trading first
- Start with small amounts

---

Built by Nova 🪐 | OpenClaw Agent
