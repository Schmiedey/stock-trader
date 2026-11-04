# ⚡ 0DTE Intraday Momentum & Mean-Reversion Scalper

A desk-ready operational suite and CLI for scalping **0DTE (Zero Days to Expiration)** options contracts on **SPY** (S&P 500) and **QQQ** (Nasdaq-100).

---

## Quick Start (Operational Routine)

### 1. Review Rules & Risk Parameters
Before the market opens, review the core scalping rules and psychology checklist:
```bash
python3 main.py rules
```

### 2. Scan SPY & QQQ for Scalp Setups
Scan intraday price action, technical indicators (VWAP, EMA 9/21, RSI, ORB), and 0DTE options chains:
```bash
# Scan SPY (default)
python3 main.py scan --symbol SPY

# Scan QQQ
python3 main.py scan --symbol QQQ
```
When a setup aligns, the scanner prints:
- Technical indicators and session phase.
- Primary trigger and conviction level.
- Optimal 0DTE strike recommendation (Delta ~0.45, penny spreads).
- Exact contract sizing based on your risk budget ($300 default).
- Pre-calculated Stop-Loss (-15%) and Take-Profit targets (+20% / +40%).

### 3. Inspect Live 0DTE Options Board
Inspect live strikes around the current underlying spot price, with real-time Delta, Theta per hour, and bid-ask spreads:
```bash
python3 main.py chain --symbol SPY --range 3.0
```

### 4. Enter a Scalp Trade
Open and log a trade into the active position tracker:
```bash
# Auto-entry using the scanner's active setup & strike recommendation:
python3 main.py enter --symbol SPY --auto

# Or manually specify direction and contracts:
python3 main.py enter --symbol SPY --action CALL --contracts 5
```

### 5. Monitor Live Position P&L and Alerts
Track live price updates, distance to stop-loss, distance to profit targets, and liquidation alerts:
```bash
python3 main.py status
```

### 6. Exit / Realize Profits
Close an active position or flush the entire desk:
```bash
# Close a specific position by ID:
python3 main.py exit --id <POSITION_ID>

# Take partial profits (e.g., scale out 5 contracts):
python3 main.py exit --id <POSITION_ID> --contracts 5 --reason TP1

# Mandatory end-of-day liquidation (flush all before 15:50 EST):
python3 main.py exit --all --reason END_OF_DAY_FLAT
```

### 7. Trade History & Performance Journal
Review cumulative win rate, profit factor, average winner vs. loser, and historical trade logs:
```bash
python3 main.py history
```

### 8. Historical 0DTE Backtester (Last 3 Months)
Run full empirical backtests across 60 trading days of intraday data with analytical Black-Scholes Greeks, theta decay, and slippage:
```bash
# Disciplined Macro Trend-Filtered Morning Scalper (Recommended):
python3 backtest.py --symbol SPY --mode trend-filtered
python3 backtest.py --symbol QQQ --mode trend-filtered

# High-Frequency Unfiltered Baseline (Evaluates all intraday triggers):
python3 backtest.py --symbol SPY --mode standard --wick-eval
```

---

## 4 Core Scalp Setups

1. **Opening Range Breakout (ORB 5m / 15m):**
   Captures directional morning expansion when price breaks out of the initial 15-minute range with surging volume and RSI momentum.
2. **VWAP Trend Bounce & Rejection:**
   Identifies trend continuation where price in an established uptrend pulls back to institutional fair value (VWAP) and bounces, or rejects VWAP from underneath in a downtrend.
3. **9 / 21 EMA Dynamic Ribbon Cross:**
   Fast momentum scalp catching micro-trend shifts when the 9 EMA crosses the 21 EMA on the 5-minute chart with RSI confirmation.
4. **Key Level Liquidity Sweeps:**
   Fades false breakouts or breakdowns at Previous Day High (PDH), Previous Day Low (PDL), High of Day (HOD), or Low of Day (LOD).

---

## Strict 0DTE Risk Management

- **Hard Stop-Loss (-15%):** Automatically enforced. 0DTE contracts experience violent theta decay; never hold a losing contract hoping for a turnaround.
- **Daily Circuit Breaker (-$600 / 2% Account):** If daily loss hits -$600, all trading is immediately locked for the remainder of the session to eliminate revenge trading.
- **Midday Chop Exclusion (11:30 AM - 1:30 PM EST):** Low volume and choppy price action erode option premium. No new entries during this window.
- **Mandatory Flat (15:50 EST):** All open contracts must be closed before 3:50 PM EST to prevent pin risk, settlement surprises, and closing bell volatility.
- **Daily Trade Limit:** Maximum 3 to 4 high-conviction scalps per day.

---

## File Structure

```
zero_dte_scalper/
├── config.json              # Risk parameters, trading hours, and asset config
├── SOP.md                   # Full Standard Operating Procedure & trading manual
├── README.md                # Quick start guide and CLI documentation
├── main.py                  # Main CLI interface (scan, chain, enter, status, exit, history)
├── backtest.py              # Historical 0DTE backtester (3 months, Black-Scholes Greeks, theta decay)
├── src/
│   ├── __init__.py
│   ├── config.py            # Config manager & NY session phase evaluator
│   ├── greeks.py            # Black-Scholes analytical formulas (Delta, Gamma, Theta, Vega)
│   ├── market_data.py       # Live intraday OHLCV, VWAP, EMA, RSI, & 0DTE chain fetcher
│   ├── signals.py           # Technical setup engine (ORB, VWAP, EMA ribbon, sweeps)
│   ├── options_selector.py  # 0DTE strike selector & risk-budget sizing engine
│   ├── risk_manager.py      # Circuit breaker & trading window permissions
│   └── tracker.py           # Active position ledger & historical trade performance tracker
└── data/
    ├── active_positions.json # Open 0DTE positions
    └── trade_history.json    # Closed trades & realized P&L journal
```

---

## Configuration (`config.json`)

Customize parameters to fit your account and risk tolerance:
```json
{
  "underlyings": ["SPY", "QQQ"],
  "primary_underlying": "SPY",
  "account_equity": 25000.0,
  "max_risk_per_trade_usd": 300.0,
  "max_daily_loss_usd": 600.0,
  "max_trades_per_day": 4,
  "target_delta_min": 0.35,
  "target_delta_max": 0.55,
  "optimal_target_delta": 0.45,
  "max_bid_ask_spread_usd": 0.05,
  "stop_loss_pct": 0.15,
  "take_profit_1_pct": 0.20,
  "take_profit_2_pct": 0.40,
  "trading_hours": {
    "timezone": "America/New_York",
    "market_open": "09:30",
    "prime_morning_start": "09:35",
    "prime_morning_end": "11:30",
    "midday_chop_start": "11:30",
    "midday_chop_end": "13:30",
    "afternoon_window_start": "13:30",
    "afternoon_window_end": "15:45",
    "mandatory_exit_time": "15:50",
    "market_close": "16:00"
  }
}
```
