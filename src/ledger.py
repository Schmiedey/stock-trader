"""
Position Tracker and Portfolio Ledger System.
Tracks up to 5 slots, enforces the 5-day holding calendar, maintains trade history,
and monitors dynamic equity and cash balances.
"""

import os
import json
import datetime
from typing import List, Dict, Optional, Any
from src.calendar_util import count_trading_days_held, get_mandatory_exit_date, is_trading_day
from src.universe import get_canonical_symbol


class Ledger:
    def __init__(
        self,
        portfolio_path: str = "data/portfolio.json",
        history_path: str = "data/history.json",
        config_path: str = "config.json"
    ):
        self.portfolio_path = portfolio_path
        self.history_path = history_path
        self.config_path = config_path

        self.account_equity: float = 100000.0
        self.available_cash: float = 100000.0
        self.max_positions: int = 5
        self.slot_equity_pct: float = 0.20
        self.profit_target_pct: float = 0.03
        self.max_holding_days: int = 5
        self.positions: Dict[int, Dict[str, Any]] = {}  # slot_id (1-5) -> position dict
        self.cooldowns: Dict[str, str] = {}  # ticker -> exit_date_str

        self.load()

    def load(self):
        """Loads configuration, active portfolio, and trade history from disk."""
        # Load config
        if os.path.exists(self.config_path):
            with open(self.config_path, "r") as f:
                cfg = json.load(f)
                self.account_equity = float(cfg.get("account_equity", 100000.0))
                self.available_cash = float(cfg.get("available_cash", 100000.0))
                self.max_positions = int(cfg.get("max_positions", 5))
                self.slot_equity_pct = float(cfg.get("slot_equity_pct", 0.20))
                self.profit_target_pct = float(cfg.get("profit_target_pct", 0.03))
                self.max_holding_days = int(cfg.get("max_holding_days", 5))

        # Load active portfolio
        if os.path.exists(self.portfolio_path):
            try:
                with open(self.portfolio_path, "r") as f:
                    data = json.load(f)
                    self.account_equity = float(data.get("account_equity", self.account_equity))
                    self.available_cash = float(data.get("available_cash", self.available_cash))
                    self.cooldowns = data.get("cooldowns", {})
                    pos_data = data.get("positions", {})
                    self.positions = {int(k): v for k, v in pos_data.items()}
            except Exception:
                self.positions = {}
        else:
            self.positions = {}

    def save(self):
        """Persists the portfolio ledger and updated balances to disk."""
        os.makedirs(os.path.dirname(self.portfolio_path), exist_ok=True)
        data = {
            "account_equity": round(self.account_equity, 2),
            "available_cash": round(self.available_cash, 2),
            "cooldowns": self.cooldowns,
            "positions": {str(k): v for k, v in self.positions.items()},
            "updated_at": datetime.datetime.now().isoformat(),
        }
        with open(self.portfolio_path, "w") as f:
            json.dump(data, f, indent=2)

        # Update config cash / equity
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    cfg = json.load(f)
                cfg["account_equity"] = round(self.account_equity, 2)
                cfg["available_cash"] = round(self.available_cash, 2)
                with open(self.config_path, "w") as f:
                    json.dump(cfg, f, indent=2)
            except Exception:
                pass

    def get_open_slots(self) -> List[int]:
        """Returns list of unused slot IDs (between 1 and max_positions)."""
        used = set(self.positions.keys())
        all_slots = set(range(1, self.max_positions + 1))
        return sorted(list(all_slots - used))

    def get_active_tickers(self) -> List[str]:
        """Returns list of currently held ticker symbols."""
        return [p["ticker"] for p in self.positions.values()]

    def is_in_cooldown(self, ticker: str, as_of_date: datetime.date) -> bool:
        """
        Edge Case 1: Tickers flushed on Day 5 mandatory exit cannot be re-bought
        on the exact same closing session. Force a 1-day cooldown.
        """
        canonical = get_canonical_symbol(ticker)
        exit_date_str = self.cooldowns.get(canonical) or self.cooldowns.get(ticker)
        if not exit_date_str:
            return False
        exit_date = datetime.date.fromisoformat(exit_date_str)
        # If exit date matches today, it's on 1-day cooldown
        return exit_date == as_of_date

    def clean_expired_cooldowns(self, as_of_date: datetime.date):
        """Cleans cooldown entries from previous trading sessions."""
        active_cooldowns = {}
        for ticker, date_str in self.cooldowns.items():
            if datetime.date.fromisoformat(date_str) == as_of_date:
                active_cooldowns[ticker] = date_str
        self.cooldowns = active_cooldowns

    def add_position(
        self,
        slot: int,
        ticker: str,
        shares: int,
        entry_price: float,
        buy_date: datetime.date
    ):
        """Logs a new stock purchase into an open slot."""
        cost = round(shares * entry_price, 2)
        target_price = round(entry_price * (1.0 + self.profit_target_pct), 2)
        mandatory_exit = get_mandatory_exit_date(buy_date, self.max_holding_days)

        pos = {
            "slot": slot,
            "ticker": ticker.upper(),
            "shares": shares,
            "entry_price": round(entry_price, 2),
            "target_price": target_price,
            "buy_date": buy_date.isoformat(),
            "mandatory_exit_date": mandatory_exit.isoformat(),
            "cost_basis": cost,
        }
        self.positions[slot] = pos
        self.available_cash = max(0.0, round(self.available_cash - cost, 2))
        self.save()

    def close_position(
        self,
        slot: int,
        exit_price: float,
        exit_date: datetime.date,
        exit_reason: str
    ) -> Dict[str, Any]:
        """
        Closes an active position, returns cash, records to history,
        and adds Day 5 exits to the 1-day cooldown tracker.
        """
        pos = self.positions.pop(slot, None)
        if not pos:
            raise ValueError(f"No active position in slot {slot}")

        ticker = pos["ticker"]
        shares = pos["shares"]
        entry_price = pos["entry_price"]
        proceeds = round(shares * exit_price, 2)
        pnl = round(proceeds - pos["cost_basis"], 2)
        pnl_pct = round(((exit_price - entry_price) / entry_price) * 100, 2)

        buy_date = datetime.date.fromisoformat(pos["buy_date"])
        days_held = count_trading_days_held(buy_date, exit_date)

        record = {
            "slot": slot,
            "ticker": ticker,
            "shares": shares,
            "buy_date": pos["buy_date"],
            "entry_price": entry_price,
            "exit_date": exit_date.isoformat(),
            "exit_price": round(exit_price, 2),
            "target_price": pos["target_price"],
            "proceeds": proceeds,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "days_held": days_held,
            "exit_reason": exit_reason,
        }

        # Update cash
        self.available_cash = round(self.available_cash + proceeds, 2)

        # Edge Case 1: If Day 5 time exit, add to cooldown
        if exit_reason.startswith("DAY_5") or exit_reason.startswith("TIME_EXIT"):
            self.cooldowns[get_canonical_symbol(ticker)] = exit_date.isoformat()

        self.save()

        # Append to history file
        os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
        history = []
        if os.path.exists(self.history_path):
            try:
                with open(self.history_path, "r") as f:
                    history = json.load(f)
            except Exception:
                history = []
        history.append(record)
        with open(self.history_path, "w") as f:
            json.dump(history, f, indent=2)

        return record

    def update_portfolio_valuation(self, current_prices: Dict[str, float], as_of_date: datetime.date):
        """Calculates current market value and updates total account equity dynamically."""
        total_market_val = 0.0
        for slot, pos in self.positions.items():
            sym = pos["ticker"]
            price = current_prices.get(sym, pos["entry_price"])
            pos["current_price"] = round(price, 2)
            pos["market_value"] = round(pos["shares"] * price, 2)
            pos["unrealized_pnl"] = round(pos["market_value"] - pos["cost_basis"], 2)
            pos["unrealized_pnl_pct"] = round(((price - pos["entry_price"]) / pos["entry_price"]) * 100, 2)
            
            buy_date = datetime.date.fromisoformat(pos["buy_date"])
            pos["days_held"] = count_trading_days_held(buy_date, as_of_date)
            total_market_val += pos["market_value"]

        self.account_equity = round(self.available_cash + total_market_val, 2)
        self.save()
