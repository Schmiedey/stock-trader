# Standard Operating Procedure (SOP): 0DTE Momentum & Mean-Reversion Scalper

**Strategy Classification:** High-Velocity Intraday Scalping  
**Primary Assets:** SPY (SPDR S&P 500 ETF) & QQQ (Invesco QQQ Trust)  
**Contract Expiration:** 0DTE (Same-Day Expiration)  
**Target Holding Time:** 3 to 25 minutes  
**Execution Environment:** `zero_dte_scalper/`

---

## 1. The Physics of 0DTE Options

0DTE (Zero Days to Expiration) contracts behave unlike any other financial instrument:
- **Extreme Gamma ($\Gamma$):** At-the-money options experience massive sensitivity to underlying price movement. A $1.00 move in SPY can alter contract value by 30% to 70% in seconds.
- **Hyper-Accelerated Theta ($\Theta$):** Extrinsic value decays exponentially as the 4:00 PM EST closing bell approaches. Time decay peaks after 1:00 PM EST. If the underlying consolidates sideways, long contracts bleed rapidly.
- **Delta ($\Delta$) Realities:** Target 0.40 to 0.50 Delta (ATM or first strike OTM). Avoid deep OTM (lotto tickets with low probability and extreme theta crush) and deep ITM (excessive capital exposure with diminished gamma leverage).
- **Execution Mandate:** Scalping 0DTE is an asymmetric momentum game. You trade fast price impulses, take rapid profits, and cut losers ruthlessly.

---

## 2. The 5 Golden Rules (Non-Negotiable)

1. **Rule 1: Hard Stop-Loss at -15% Premium**  
   Never let a 0DTE trade exceed -15% to -20% unrealized loss. Hoping a 0DTE contract bounces is account suicide. If the thesis breaks, exit immediately.
2. **Rule 2: Daily Circuit Breaker ($600 / 2% Equity)**  
   If cumulative realized + unrealized loss reaches the daily maximum loss threshold, trading is immediately terminated for the day. Close all positions and shut the terminal. No revenge trading.
3. **Rule 3: Respect the Midday Chop Zone (11:30 AM - 1:30 PM EST)**  
   Institutional liquidity drops, volume contracts, and price action enters mean-reverting chop. Theta decay accelerates. New entries are strictly prohibited during this window.
4. **Rule 4: Mandatory Flat by 3:50 PM EST**  
   Every position must be flat before 3:50 PM EST. Never hold a 0DTE contract into the 4:00 PM close. Broker auto-liquidation, spread blowout, and pin risk can wipe out capital.
5. **Rule 5: Maximum 3 to 4 High-Conviction Trades Per Day**  
   Overtrading is the #1 killer of scalpers. Only enter when clear technical alignment exists across multiple indicators (VWAP, EMA ribbon, volume, and key levels).

---

## 3. Time-of-Day Execution Windows

All times in **Eastern Standard Time (EST)**:

| Window | Time (EST) | Status | Operational Directive |
| :--- | :--- | :--- | :--- |
| **Market Open Volatility** | 09:30 - 09:35 | **OBSERVE ONLY** | Wild bid-ask spreads and initial order-book balancing. Let the opening bar establish range. |
| **Prime Morning Momentum** | 09:35 - 11:30 | **ACTIVE TRADING** | Best volume, clearest directional trends, highest win-rate window for ORB and VWAP pullbacks. |
| **Midday Chop Zone** | 11:30 - 13:30 | **STRICT NO-TRADE** | Low volume, erratic false breakouts, theta bleed. Do not take fresh entries. |
| **Afternoon Power Trend** | 13:30 - 15:45 | **ACTIVE TRADING** | Institutional rebalancing, late-day breakouts, high gamma momentum. Tighten stops to -12%. |
| **Mandatory Flush Window** | 15:45 - 15:50 | **EXIT ONLY** | Liquidate all open positions at market bid. No open contracts allowed past 15:50. |
| **Market Close** | 16:00 | **MARKET CLOSED** | Generate daily trade journal and performance audit. |

---

## 4. The Scalp Playbook (4 Core Setups)

### Setup 1: Opening Range Breakout (ORB - 5m / 15m)
- **Concept:** Captures the institutional morning momentum expansion.
- **Trigger (CALLS):** 5-minute or 15-minute candle closes *above* the Opening High with volume $> 1.3\times$ 20-period average volume and RSI $> 55$.
- **Trigger (PUTS):** Candle closes *below* the Opening Low with volume surge and RSI $< 45$.
- **Underlying Invalidation:** Retracement back inside the Opening Range midpoint.
- **Option Contract Stop:** -15% from fill price.

### Setup 2: VWAP Trend Bounce & Rejection
- **Concept:** VWAP represents institutional fair value. When trend is established, VWAP acts as dynamic support/resistance.
- **Trigger (CALLS):** Price is in an uptrend (above VWAP & EMA 21), pulls back to test VWAP within $\pm 0.08\%$, prints a bullish hammer or green engulfing bar, and closes back above EMA 9.
- **Trigger (PUTS):** Price in downtrend (below VWAP & EMA 21), rallies into VWAP from underneath, forms a rejection wick/bearish bar, and turns back down.
- **Option Contract Stop:** -15% or underlying cross through VWAP by $> 0.15\%$.

### Setup 3: 9/21 EMA Dynamic Ribbon Cross & Continuation
- **Concept:** Fast moving averages catch micro-trends on the 1-minute and 5-minute timeframes.
- **Trigger (CALLS):** 9 EMA crosses above 21 EMA with rising slope, RSI between 52 and 70.
- **Trigger (PUTS):** 9 EMA crosses below 21 EMA with declining slope, RSI between 30 and 48.
- **Option Contract Stop:** -15% or candle close reversing the EMA cross.

### Setup 4: Key Level Liquidity Sweeps (PDH / PDL / HOD / LOD)
- **Concept:** Market makers sweep previous day highs/lows for liquidity before reversing.
- **Trigger (CALL Reversal):** Price dips below Previous Day Low (PDL) or Morning Low of Day (LOD), quickly reclaims the level within 1-2 candles with high volume (failed breakdown).
- **Trigger (PUT Reversal):** Price pushes above Previous Day High (PDH) or High of Day (HOD), immediately fails and re-enters the range with heavy selling volume (failed breakout).

---

## 5. Strike & Contract Selection Protocol

To maximize gamma while avoiding slippage:
1. **Underlying Focus:** `SPY` (primary) and `QQQ`.
2. **Delta Target:** 0.35 to 0.55 (Sweet spot: ~0.45 Delta).
   - If bullish: Nearest OTM Call (1 strike OTM) or ATM Call.
   - If bearish: Nearest OTM Put (1 strike OTM) or ATM Put.
3. **Liquidity Criteria:**
   - Bid-Ask spread must be $\le \$0.05$ (typically $\$0.01 - \$0.02$ on SPY ATM contracts).
   - Minimum volume: $\ge 1,000$ contracts.
   - Minimum Open Interest: $\ge 500$ contracts.
4. **Never trade contracts with bid-ask spreads exceeding $\$0.08$.** The immediate slippage creates an insurmountable negative expectancy.

---

## 6. Sizing, Profit Taking & Risk Architecture

### Position Sizing Calculation
$$\text{Max Risk Budget} = \$300.00 \quad (\text{or } 1.0\% - 1.5\% \text{ of account equity})$$
$$\text{Contract Stop Loss} = 15\% \times \text{Contract Price} \times 100$$
$$\text{Number of Contracts} = \min\left(\left\lfloor \frac{\text{Max Risk Budget}}{\text{Contract Stop Loss}} \right\rfloor, 10\right)$$

*Example:* A \$2.00 option has a 15% stop loss (\$0.30 per share = \$30 per contract). With a \$300 risk budget, size is $\lfloor 300 / 30 \rfloor = 10$ contracts.

### Scaling & Profit Targets (The 2-Tranche Exit)
- **Target 1 (+20% Option Gain):** Close **50% of position**. Immediately move stop-loss on remaining tranche to **Breakeven** ($0.00$ risk).
- **Target 2 (+40% Option Gain):** Close **remaining 50%**. If strong momentum persists, trail remaining runner using EMA 9 on 1m chart.
- **Stop Loss (-15% Option Loss):** Hard market exit on all remaining tranches. No exceptions.

---

## 7. Daily Routine Checklist

- [ ] **09:15 EST:** Run `python3 main.py rules` to review psychological parameters and market rules.
- [ ] **09:30 EST:** Market open. Do NOT trade the first 5 minutes. Observe the opening candle range.
- [ ] **09:35 EST:** Run `python3 main.py scan` to evaluate ORB levels, VWAP position, and active signal conditions.
- [ ] **During Trade:** Run `python3 main.py status` to monitor active position P&L and distance to target/stop.
- [ ] **11:30 - 13:30 EST:** Midday chop. No new positions. Keep terminal idle.
- [ ] **13:30 - 15:45 EST:** Afternoon session. Look for high-probability trend continuation or liquidity sweep reversals.
- [ ] **15:50 EST:** MANDATORY LIQUIDATION. Run `python3 main.py exit --all` if any position remains open.
- [ ] **16:05 EST:** Run `python3 main.py history` to log and review daily performance statistics.
