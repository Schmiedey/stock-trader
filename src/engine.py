"""
Pure Clone Execution Engine (spinach/three-day-drop-bounce).
Executes the exact 3-step afternoon protocol (2:50 PM - 3:00 PM CST):
1. Audits current holdings for +3% profit target or Day 5 mandatory time exit.
2. Scans S&P 500 + Nasdaq-100 universe for top 3-day drop replacements with edge cases.
3. Sizes positions using min(20% equity, available_cash / open_slots) and generates order sheet.
"""

import math
import datetime
from typing import Dict, List, Any, Optional
from src.ledger import Ledger
from src.screener import DropBounceScreener
from src.calendar_util import count_trading_days_held, is_trading_day


def get_cst_today() -> datetime.date:
    try:
        import pytz
        return datetime.datetime.now(pytz.timezone("America/Chicago")).date()
    except Exception:
        return datetime.date.today()


class PureCloneEngine:
    def __init__(self, ledger: Optional[Ledger] = None, screener: Optional[DropBounceScreener] = None):
        self.ledger = ledger or Ledger()
        self.screener = screener or DropBounceScreener()

    def run_daily_audit(self, as_of_date: Optional[datetime.date] = None) -> Dict[str, Any]:
        """
        Executes Step 1 and Step 2 of the afternoon SOP:
        - Fetches latest prices for active positions.
        - Evaluates Trigger A (+3% bounce) and Trigger B (Day 5 time exit).
        - Computes open slots and scans for replacements.
        - Computes 20% equity position sizing.
        """
        as_of_date = as_of_date or get_cst_today()
        self.ledger.clean_expired_cooldowns(as_of_date)

        # 1. Fetch current prices for active holdings
        held_tickers = self.ledger.get_active_tickers()
        current_prices = self.screener.get_current_prices(held_tickers) if held_tickers else {}
        self.ledger.update_portfolio_valuation(current_prices, as_of_date)

        # 2. Step 1: Audit Current Holdings
        audit_results = []
        sell_orders = []
        retained_positions = []
        projected_sell_proceeds = 0.0

        for slot_id in range(1, self.ledger.max_positions + 1):
            pos = self.ledger.positions.get(slot_id)
            if not pos:
                audit_results.append({
                    "slot": slot_id,
                    "status": "EMPTY",
                    "ticker": "-",
                    "action": "OPEN_FOR_REPLACEMENT"
                })
                continue

            ticker = pos["ticker"]
            shares = pos["shares"]
            entry_price = pos["entry_price"]
            curr_price = current_prices.get(ticker, entry_price)
            buy_date = datetime.date.fromisoformat(pos["buy_date"])
            days_held = count_trading_days_held(buy_date, as_of_date)
            pnl_pct = ((curr_price - entry_price) / entry_price) * 100.0

            pos_audit = {
                "slot": slot_id,
                "status": "ACTIVE",
                "ticker": ticker,
                "shares": shares,
                "entry_price": entry_price,
                "current_price": curr_price,
                "target_price": pos["target_price"],
                "pnl_pct": round(pnl_pct, 2),
                "days_held": days_held,
                "max_days": self.ledger.max_holding_days,
                "mandatory_exit_date": pos["mandatory_exit_date"],
            }

            # Check Trigger A: +3.0% or higher at the close
            if pnl_pct >= (self.ledger.profit_target_pct * 100.0):
                pos_audit["action"] = "SELL"
                pos_audit["exit_reason"] = "PROFIT_TARGET_HIT (+3% Rule)"
                sell_orders.append({
                    "slot": slot_id,
                    "action": "SELL",
                    "ticker": ticker,
                    "shares": shares,
                    "est_price": curr_price,
                    "est_proceeds": round(shares * curr_price, 2),
                    "reason": pos_audit["exit_reason"]
                })
                projected_sell_proceeds += round(shares * curr_price, 2)

            # Check Trigger B: 5 trading days held
            elif days_held >= self.ledger.max_holding_days:
                pos_audit["action"] = "SELL"
                pos_audit["exit_reason"] = f"DAY_5_TIME_EXIT ({days_held} Days Held)"
                sell_orders.append({
                    "slot": slot_id,
                    "action": "SELL",
                    "ticker": ticker,
                    "shares": shares,
                    "est_price": curr_price,
                    "est_proceeds": round(shares * curr_price, 2),
                    "reason": pos_audit["exit_reason"]
                })
                projected_sell_proceeds += round(shares * curr_price, 2)

            else:
                pos_audit["action"] = "HOLD"
                pos_audit["exit_reason"] = f"Hold (Day {days_held}/5, {pnl_pct:+.2f}%)"
                retained_positions.append(pos)

            audit_results.append(pos_audit)

        # 3. Step 2: Open Slots & Scan for Replacements
        today_sell_slots = {s["slot"] for s in sell_orders}
        active_remaining_slots = [
            slot for slot in self.ledger.positions.keys()
            if slot not in today_sell_slots
        ]
        available_open_slots = sorted(
            [s for s in range(1, self.ledger.max_positions + 1) if s not in active_remaining_slots]
        )
        open_slot_count = len(available_open_slots)

        buy_orders = []
        screened_candidates = []

        if open_slot_count > 0:
            # Tickers that will still be held after today's sells
            remaining_held_tickers = [
                self.ledger.positions[slot]["ticker"] for slot in active_remaining_slots
            ]
            # Tickers sold today on Day 5 time exit (Edge Case 1 cooldown)
            day5_sells = [
                s["ticker"] for s in sell_orders if s["reason"].startswith("DAY_5")
            ]

            # Scan the universe
            candidates = self.screener.scan_3day_drops(
                held_tickers=remaining_held_tickers,
                cooldown_tickers=day5_sells,
                limit=15
            )
            screened_candidates = candidates

            # 4. Step 3: Position Sizing (Edge Case 4)
            # Projected cash available after sells execute first
            est_available_cash = self.ledger.available_cash + projected_sell_proceeds
            # Remaining portfolio value
            retained_market_val = sum(
                p["shares"] * current_prices.get(p["ticker"], p["entry_price"])
                for p in retained_positions
            )
            est_total_equity = est_available_cash + retained_market_val

            # Allocation formula: min(20% of Total Equity, Available Cash / Open Slots)
            slot_allocation = min(
                est_total_equity * self.ledger.slot_equity_pct,
                est_available_cash / open_slot_count
            )

            # Assign candidates to open slots
            for idx, slot_num in enumerate(available_open_slots):
                if idx < len(candidates):
                    cand = candidates[idx]
                    price = cand["current_price"]
                    if price > 0:
                        shares = math.floor(slot_allocation / price)
                        if shares > 0:
                            buy_orders.append({
                                "slot": slot_num,
                                "action": "BUY",
                                "ticker": cand["ticker"],
                                "name": cand["name"],
                                "shares": shares,
                                "est_price": price,
                                "est_cost": round(shares * price, 2),
                                "return_3d_pct": cand["return_3d_pct"],
                                "allocation_budget": round(slot_allocation, 2),
                            })

        return {
            "as_of_date": as_of_date.isoformat(),
            "account_equity": self.ledger.account_equity,
            "available_cash": self.ledger.available_cash,
            "audit_results": audit_results,
            "sell_orders": sell_orders,
            "open_slots": available_open_slots,
            "open_slot_count": open_slot_count,
            "buy_orders": buy_orders,
            "screened_candidates": screened_candidates,
            "projected_sell_proceeds": round(projected_sell_proceeds, 2),
        }

    def execute_plan(
        self,
        audit_data: Dict[str, Any],
        actual_sell_fills: Optional[Dict[str, float]] = None,
        actual_buy_fills: Optional[Dict[str, float]] = None,
        execution_date: Optional[datetime.date] = None
    ) -> Dict[str, Any]:
        """
        Executes and records confirmed orders into the ledger:
        1. Closes sold positions.
        2. Logs new buy fills into their assigned slots.
        3. Persists changes to disk.
        """
        execution_date = execution_date or get_cst_today()
        actual_sell_fills = actual_sell_fills or {}
        actual_buy_fills = actual_buy_fills or {}

        executed_sells = []
        executed_buys = []

        # 1. Execute Sells First (frees up cash)
        for sell in audit_data.get("sell_orders", []):
            slot = sell["slot"]
            ticker = sell["ticker"]
            fill_price = actual_sell_fills.get(ticker, sell["est_price"])
            exit_reason = sell["reason"]
            record = self.ledger.close_position(
                slot=slot,
                exit_price=fill_price,
                exit_date=execution_date,
                exit_reason=exit_reason
            )
            executed_sells.append(record)

        # 2. Execute Buys
        for buy in audit_data.get("buy_orders", []):
            slot = buy["slot"]
            ticker = buy["ticker"]
            shares = buy["shares"]
            fill_price = actual_buy_fills.get(ticker, buy["est_price"])
            self.ledger.add_position(
                slot=slot,
                ticker=ticker,
                shares=shares,
                entry_price=fill_price,
                buy_date=execution_date
            )
            executed_buys.append({
                "slot": slot,
                "ticker": ticker,
                "shares": shares,
                "fill_price": fill_price,
                "cost": round(shares * fill_price, 2)
            })

        return {
            "execution_date": execution_date.isoformat(),
            "sells_executed": executed_sells,
            "buys_executed": executed_buys,
            "final_cash": self.ledger.available_cash,
            "final_equity": self.ledger.account_equity,
        }
