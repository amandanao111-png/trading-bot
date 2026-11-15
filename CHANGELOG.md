# Changelog

## [V9.1] — 2026-11-14 — True P&L from the fill ledger

### The problem
`pnl_authoritative.py` and `pnl_truth_final.py` disagreed by ~$135K on
lifetime P&L, and the "implied start $241,119.81" reconciliation was an
artifact of an empty activities fetch. Three Alpaca API parsing bugs:

1. `side=sell` on `/v2/orders` **excludes `sell_short`** — 3,207 SLV shares
   sold went missing; cash basis read **−$101,796.88** instead of **+$39,332.51**
2. Filtering on `status == 'filled'` **drops cancelled orders with a partial
   fill** (`filled_qty>0`) — 67 XLK shares bought went missing
3. `/v2/account/activities` **rejects `page_size=1000`** (max 100 → HTTP 422)
   and rejects `activity_types=ACH`; FEE rows carry **`net_amount`, not `qty`**
   — so fees read $0.00

### The fix
- ✅ New `pnl_reconciled.py`: uses the FILL activity ledger as the execution
  ledger, cross-checks every symbol against the orders endpoint, and
  **hard-fails when the flat-account share balance test does not reconcile to zero**
- ✅ **Verified: 1,855 FILL fills / 1,300 orders with any fill; all 30 symbols
  reconcile to zero; fees −$9.58 (82 FEE rows); implied start $99,990.36;
  net vs $100K paper start +$39,322.87 (+39.32%)**
- ✅ `morning-planning` cron payload now runs `pnl_reconciled.py` and ignores
  `pnl_authoritative.py` / `pnl_truth_final.py`
- ✅ README rewritten — it still described a V2 codebase with files that no
  longer exist (`trading_bot_v2.py`, `learnings.json`) and a stale signal table

### Still open
- ⚠️ The bot's own journal covers **75 closed trades = 6% of 1,295 Alpaca fills**,
  and journal prices differ from exchange fills (QQQ buy $687.83 vs $689.40), so
  `learned_stats`, Kelly sizing and the auto-blacklist still learn from prices
  the exchange never saw
- ⚠️ **Effective tradable universe is 7 names, not 15** — NVDA and XLE are
  auto-blacklisted, and XLE was blacklisted off a 3-trade sample in the same
  week energy was the best sector (+5.25%)
- ⚠️ `auto_config.json` is a stale optimiser snapshot (`last_run` 2026-05-07);
  the live blacklist is `auto_blacklist.json`
- ⚠️ 26 `*.bak.*` files in the repo root are unreferenced clutter

## [V9] — 2026-11-12 — Self-learning loop, live under launchd

- ✅ `auto_trade.py` runs as a launchd service (`ai.nova.trading-bot`)
- ✅ Overnight optimiser pass adjusts confidence thresholds and the auto-blacklist
- ✅ Trading digest treats a successful **empty live book as authoritative**, so a
  flat account can no longer be turned into phantom holdings
- ✅ Daily digest / safety test suite: `test_safety.py` 19/19 pass

## [V6] — 2026-03-29 — Risk-First Professional Edition

### Analysis Findings (V1–V5 History)
- **16 trading days, 53 BUY entries, 30 STOP_LOSS exits**
- **Average loss on STOP_LOSS: -$358.56 | Average gain on RSI_EXIT: +$54.18**
- Only 7/28 traded symbols were net profitable
- **Max consecutive loss streak: 7 trades**
- Confidence 35 → 22% win rate; Confidence 50 → 50% win rate
- 14 entries were made with RSI overbought (>70) — all lost
- Position sizing was arbitrary; BAC had 366 shares ($17K) in a $100K portfolio
- **Mar 6 alone lost -$1,740.99** due to too many open positions in a sell-off

### Risk Management (CRITICAL — New)
- ✅ **Max 5 open positions** (was: unlimited — caused catastrophic drawdowns)
- ✅ **Max 10% portfolio risk per trade** with proper position sizing formula
- ✅ **Proper position sizing:** `shares = (portfolio × 10%) / (entry × stopLoss%)`
- ✅ **Mandatory stop loss:** `max(3%, ATR × 1.5)` — whichever is tighter
- ✅ **No new trades if portfolio down >5%** that day (daily circuit breaker)

### Entry Quality (Major Upgrade)
- ✅ **RSI < 40 required for longs** (was: any RSI, including >70)
- ✅ **2+ confirmations required** (was: "Trend: BULL" alone triggered entry)
- ✅ **Min confidence threshold raised to 45** (was: 35 — 22% win rate)
- ✅ **Bear market filter:** no new entries (except index ETFs) if SPY in downtrend
- ✅ **Sector concentration cap:** max 2 positions per sector

### Exit Strategy (Rewritten)
- ✅ **Take profit at 2:1 reward-to-risk** (was: vague RSI_EXIT at RSI>65)
- ✅ **Scale out:** 50% at 1:1, remainder at 2:1 (was: hold until RSI_EXIT)
- ✅ **Hard stop at -3%** regardless of signal (was: no consistent hard stop)
- ✅ **RSI_EXIT only if RSI > 65 AND position is profitable**
  (was: exit at RSI>65 even in small losses)

### Trade Filtering (New)
- ✅ **48h cooldown** after STOP_LOSS on same symbol (was: re-entered immediately)
- ✅ **Avoid earnings weeks** (Mon–Wed as heuristic; real earnings calendar in v7)
- ✅ **Max 1 trade per symbol per calendar week**

### Logging & Journal (Enhanced)
- ✅ Enhanced journal (`trade_journal_v6.json`) with:
  - `rsi_entry`, `atr_entry`, `atr_pct`
  - `hold_minutes`
  - `stop_price` and `tp_price` at entry time
  - Full P&L on close
- ✅ **Daily equity curve log** (`equity_curve_v6.json`)
- ✅ Separate journal file from v5 to preserve clean history

### Self-Learning (Improved)
- ✅ **Adaptive confidence threshold:** dynamically adjusts based on recent win rate
  - Win rate < 35% → threshold rises to 55
  - Win rate > 55% → threshold drops to 40
- ✅ **Exponential moving average** of win rate (α=0.3) for smoother adaptation
- ✅ Weekly performance review printout at end of each run

### Other Improvements
- ✅ `check_exit()` refactored to return `(reason, qty, detail)` tuple
- ✅ `analyze_symbol()` now clearly separates confirmations count from confidence score
- ✅ ATR-based stops prevent getting stopped out in volatile stocks too early
- ✅ Buying power check before every order
- ✅ Partial take updates position qty rather than deleting it

---

## [V5] — trading_bot_v5.py
- Simple RSI+MACD strategy
- 2% stop loss, 4% take profit
- Max 10% position per trade
- Paper trading via Alpaca

## [auto_trade.py / V9] — Self-Learning Edition
- Self-learning from trade history
- Trailing stops, partial takes
- Sector cooldown after stop loss
- Mean reversion signals for ETFs
- But: still bought RSI overbought, no max positions, arbitrary sizing
