# Operational Trading Blueprint: `spinach/three-day-drop-bounce` (Pure Clone)

Desk-Ready Standard Operating Procedure (SOP) for executing the strategy manually or semi-systematically every afternoon.

---

## 1. Strategy Core Specifications (Mode A: Pure Clone)

| Parameter | Exact Rule Specification | Operational Rule |
| :--- | :--- | :--- |
| **Universe** | S&P 500 + Nasdaq-100 (~515 stocks) | Large-cap index components only; zero small-caps or non-index names |
| **Max Positions** | 5 concurrent stocks | Portfolio is divided into 5 equal slots (Slots 1–5) |
| **Position Sizing** | 20% of total account equity per slot | Dynamic allocation: `min(20% Equity, Cash / Open Slots)` |
| **Entry Signal** | Lowest 3-Day Return ($R_{3\text{D}}$) | Ranked from worst 3-day % loser upward across the universe |
| **Profit Exit** | +3.0% or higher at the close | Evaluated strictly at 2:55 PM CST against entry price |
| **Time Exit** | 5 trading days held | Evaluated at the closing bell on Day 5 regardless of P&L |
| **Stop-Loss** | None | Downside is capped strictly by the 20% slot limit and 5-day time exit |
| **Execution Window** | 2:55 PM – 3:00 PM CST (3:55 – 4:00 PM EST) | Executed once daily right into the closing bell |
| **Mode** | **Mode A (Pure Clone)** | No intraday limit orders; evaluated once daily into the close |

---

## 2. Daily Execution Timeline (2:50 PM – 3:00 PM CST)

### 2:50 PM CST — Step 1: Audit Current Holdings & Count Open Slots
Run `python3 main.py run` or check your holdings against the two exit triggers:

* **Trigger A (The +3% Bounce Check):** Is the stock up $\ge +3.0\%$ from entry?
  * *If YES:* Mark for immediate Market-on-Close / Market Sell at 2:58 PM CST.
* **Trigger B (The 5-Day Time Check):** Has this stock completed 5 trading sessions?
  * *If YES:* Mark for immediate Market-on-Close / Market Sell at 2:58 PM CST, even if red.
* *If NEITHER trigger is hit:* Do nothing. It stays in its slot for tomorrow.

### 2:55 PM CST — Step 2: Scan for Replacements
Count open slots:
$$\text{Open Slots} = 5 - (\text{Current Holdings} - \text{Today's Sells})$$

* If $\text{Open Slots} = 0$: Close your laptop. Zero trades today.
* If $\text{Open Slots} > 0$: The engine screens the bottom 3-day losers across S&P 500 + Nasdaq-100 and applies all edge-case filters.

### 2:58 PM CST — Step 3: Calculate Share Size & Fire Orders
1. Execute **Sell Orders** first so proceeds credit to buying power.
2. Calculate dollar allocation per open slot:
   $$\text{Slot Budget} = \min(\text{Total Account Equity} \times 0.20, \frac{\text{Available Cash}}{\text{Open Slots}})$$
3. Compute exact share quantity:
   $$\text{Shares to Buy} = \left\lfloor \frac{\text{Slot Budget}}{\text{Current Share Price}} \right\rfloor$$
4. Submit **Buy Market Orders** between 2:58 PM and 2:59:30 PM CST.

---

## 3. The 5-Day Holding Matrix

Weekends and NYSE holidays do **not** count:

| Buy Date (Close) | Day 1 | Day 2 | Day 3 | Day 4 | Day 5 Mandatory Exit Date (Close) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Monday Close** | Tue | Wed | Thu | Fri | **Next Monday Close** |
| **Tuesday Close** | Wed | Thu | Fri | Mon | **Next Tuesday Close** |
| **Wednesday Close** | Thu | Fri | Mon | Tue | **Next Wednesday Close** |
| **Thursday Close** | Fri | Mon | Tue | Wed | **Next Thursday Close** |
| **Friday Close** | Mon | Tue | Wed | Thu | **Next Friday Close** |

---

## 4. Edge Cases & Tie-Breaker Rules (Enforced by Engine)

1. **1-Day Cooldown on Flushed Stocks**: A stock sold today on its Day 5 time exit cannot be re-bought today, even if it ranks #1. Skip to the next worst loser.
2. **No Doubling Down**: Never hold two slots of the same company (max 20% exposure). Skip any ticker already in Slots 1–5.
3. **Dual-Class Twins**: Treat dual shares (`GOOG`/`GOOGL`, `FOX`/`FOXA`, `NWSA`/`NWS`) as one company. Buy the more liquid share class (`GOOGL`) and skip the twin.
4. **Cash Drift Sizing**: Never trim existing winners/losers to rebalance. Deploy `min(20% Equity, Cash / Open Slots)` for opened slots.
