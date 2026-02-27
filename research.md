# Trading Bot Research Summary

## Sources Used

### Broker APIs
1. **Alpaca Markets** - Best for algorithmic trading (BrokerChooser 2026)
   - Free paper trading
   - Python SDK (alpaca-py)
   - Commission-free

2. **Interactive Brokers** - Professional grade
3. **TD Ameritrade** - Good for options

### Technical Indicators (Research-backed)
1. **RSI (Relative Strength Index)** - Momentum
   - Oversold < 30, Overbought > 70
   
2. **MACD** - Trend momentum
   - Bullish: MACD crosses above signal
   - Bearish: MACD crosses below
   
3. **Bollinger Bands** - Volatility
   - Price near lower band = oversold
   - Price near upper band = overbought
   
4. **Moving Averages** - Trend
   - SMA/EMA for trend direction
   - Golden cross / death cross

### Backtesting Libraries
1. **Backtrader** - Full-featured
2. **Backtesting.py** - Simple, intuitive
3. **QuantConnect** - Cloud-based
4. **Zipline** - Quantopian origin

### Best Practices
- Risk/Reward ratio: 2:1 minimum
- Max position: 10% of portfolio
- Max loss per trade: 2%
- Stop loss always required
- Paper trade before live

## Bot Versions
- V1: Basic trading
- V2: Risk management, learnings
- V3: Technical analysis
- V4: Professional indicators (RSI, MACD, BB)

## Current Status
- Connected to Alpaca Paper Trading
- $100,000 paper balance
- Market: CLOSED (opens 9:30 AM ET)
- 0 open positions
