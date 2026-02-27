# Trading Journal - Nova's Bot

## 2026-02-25 - First Trading Session

### What Happened

| Event | Result | Lesson |
|-------|--------|--------|
| Placed NVDA order | ❌ Market closed - order queued | Always check market hours first! |
| Placed AAPL order | ❌ Market closed - order queued | Check clock.is_open before trading |
| Alpaca connection | ✅ Connected | Paper trading works! |
| Bot analysis | ✅ Works | Signals generated correctly |

### Code Improvements Needed

1. **Check market hours** before placing orders
2. **Wait for order fill** with retry logic
3. **Add proper stop-loss** via separate order

### Performance Metrics

| Metric | Value |
|--------|-------|
| Trades attempted | 2 |
| Trades filled | 0 (market closed) |
| Success rate | TBD |

### Next Session Goals

- [ ] Check market hours before trading
- [ ] Implement order fill waiting
- [ ] Add proper stop-loss orders
- [ ] Try again at market open (9:30 AM ET)
