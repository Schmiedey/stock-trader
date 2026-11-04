# Spinach Three-Day Drop-Bounce (Pure Clone Engine)

A desk-ready operational trading suite and CLI for executing the **Pure Clone (Mode A)** of `@spinach`'s `three-day-drop-bounce` strategy.

---

## Quick Start (Daily 3-Minute Routine)

### 1. Set Your Account Balance (First Time Only)
Set your account's total equity and available starting cash:
```bash
python3 main.py set-account --equity 100000 --cash 100000
```

### 2. View Live Portfolio Status Anytime
Check all 5 slots, entry prices, days held, targets (+3%), and current P&L:
```bash
python3 main.py status
```

### 3. Run the Daily 2:55 PM CST Execution Protocol
Between 2:50 PM and 2:58 PM CST, run:
```bash
python3 main.py run
```
This automated routine:
1. **Audits** active holdings for the +3.0% bounce exit or Day 5 mandatory time exit.
2. **Counts** open slots ($5 - \text{held}$).
3. **Screens** the S&P 500 + Nasdaq-100 universe for the worst 3-day drop losers.
4. **Applies Edge Cases**: excludes current holdings, 1-day cooldowns on Day 5 flushes, and dual-class twins.
5. **Calculates Sizing**: applies `min(20% Equity, Cash / Open Slots)` and prints exact share quantities.
6. Prompts you to confirm and commit the fills to `data/portfolio.json`.

*(To execute and commit non-interactively without prompt, add `--yes` / `-y`)*

---

## Additional Commands

### Standalone Universe Screener
Scan the top 3-day drop losers across S&P 500 + Nasdaq-100 at any time:
```bash
python3 main.py scan --limit 10
```

### Closed Trades History & Statistics
View win rate, total realized P&L, and trade breakdown:
```bash
python3 main.py history
```

### Historical Backtester
Backtest the Pure Clone strategy on historical market data:
```bash
python3 backtest.py --start 2025-01-01 --end 2026-11-01 --capital 100000
```

---

## File Structure

- `main.py`: Command-line interface for daily desk execution, status, and screener.
- `config.json`: Account equity, cash, and strategy parameter configuration.
- `SOP.md`: Complete operational trading blueprint & rule reference.
- `backtest.py`: Historical backtest engine for Pure Clone Mode A.
- `data/portfolio.json`: Active holdings, slot mapping, entry prices, and Day 5 exit dates.
- `data/history.json`: Permanent journal of completed trades and realized P&L.
- `data/universe.json`: Cached S&P 500 + Nasdaq-100 constituent tickers.
- `src/engine.py`: Daily audit, replacement scanner, and 20% equity sizing engine.
- `src/screener.py`: Multithreaded 3-day drop performance calculation.
- `src/ledger.py`: 5-slot portfolio manager, dynamic valuation, and cooldown tracker.
- `src/universe.py`: Scrapes and caches index constituents with dual-class twin deduplication.
- `src/calendar_util.py`: Accurate NYSE calendar with holiday observance and trading day counting.
