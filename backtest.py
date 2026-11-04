"""
Historical Backtester for Spinach Three-Day Drop-Bounce (Pure Clone Mode A).
Simulates daily 2:55 PM CST closing executions, 5-slot allocation, +3% profit exits,
5-day time exits, 1-day cooldowns, and dual-class share rules.
"""

import math
import argparse
import datetime
from typing import List, Dict, Any, Set
import pandas as pd
import yfinance as yf
from src.universe import UniverseManager, get_canonical_symbol


def run_backtest(
    start_date: str = "2024-01-01",
    end_date: str = "2026-11-01",
    initial_capital: float = 100000.0,
    profit_target: float = 0.03,
    max_hold_days: int = 5,
    max_slots: int = 5,
    slot_equity_pct: float = 0.20,
):
    print("\n" + "=" * 80)
    print(f"  BACKTESTING SPINACH THREE-DAY DROP-BOUNCE (PURE CLONE)")
    print(f"  Period: {start_date} to {end_date} | Starting Equity: ${initial_capital:,.2f}")
    print(f"  Parameters: Target=+{profit_target*100:.1f}%, Max Hold={max_hold_days} Days, Slots={max_slots}")
    print("=" * 80)

    mgr = UniverseManager()
    tickers = mgr.get_ticker_list()
    print(f"Downloading historical daily data for {len(tickers)} universe tickers + SPY benchmark...")

    # Download SPY + tickers
    all_symbols = tickers + ["SPY"]
    # Download with buffer for 3-day lookback
    start_dt = datetime.date.fromisoformat(start_date) - datetime.timedelta(days=20)
    df = yf.download(all_symbols, start=start_dt.isoformat(), end=end_date, interval="1d", progress=False, group_by="ticker", threads=True)

    # Extract Close prices
    close_dict = {}
    for sym in all_symbols:
        try:
            sub = df[sym]
            closes = sub["Close"].dropna()
            if not closes.empty:
                close_dict[sym] = closes
        except Exception:
            pass

    price_df = pd.DataFrame(close_dict).dropna(how="all")
    # Align dates
    sim_dates = [d for d in price_df.index if d.date() >= datetime.date.fromisoformat(start_date)]
    if len(sim_dates) < 5:
        print("Error: Insufficient trading days downloaded.")
        return

    # Simulation state
    cash = initial_capital
    portfolio: Dict[int, Dict[str, Any]] = {} # slot -> position
    history: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []
    cooldowns: Dict[str, datetime.date] = {} # canonical ticker -> exit_date

    for day_idx, current_ts in enumerate(sim_dates):
        current_date = current_ts.date()

        # Clean expired cooldowns (cooldown is only for the same day)
        cooldowns = {k: v for k, v in cooldowns.items() if v == current_date}

        # 1. Evaluate Exits for Open Slots
        sell_orders = []
        for slot in range(1, max_slots + 1):
            if slot not in portfolio:
                continue

            pos = portfolio[slot]
            sym = pos["ticker"]
            if sym not in price_df.columns:
                continue

            series = price_df[sym].loc[:current_ts].dropna()
            if series.empty:
                continue

            current_price = float(series.iloc[-1])
            entry_price = pos["entry_price"]
            days_held = pos["days_held"] + 1 # 1 more trading session completed
            pos["days_held"] = days_held

            pnl_pct = (current_price - entry_price) / entry_price

            # Check Trigger A: +3.0% at close
            if pnl_pct >= profit_target:
                sell_orders.append({
                    "slot": slot,
                    "price": current_price,
                    "reason": "PROFIT_TARGET_HIT",
                    "pnl_pct": pnl_pct,
                })
            # Check Trigger B: 5 trading days held
            elif days_held >= max_hold_days:
                sell_orders.append({
                    "slot": slot,
                    "price": current_price,
                    "reason": "DAY_5_TIME_EXIT",
                    "pnl_pct": pnl_pct,
                })

        # Execute Sells
        for sell in sell_orders:
            slot = sell["slot"]
            pos = portfolio.pop(slot)
            exit_price = sell["price"]
            shares = pos["shares"]
            proceeds = shares * exit_price
            pnl = proceeds - pos["cost_basis"]
            cash += proceeds

            history.append({
                "ticker": pos["ticker"],
                "buy_date": pos["buy_date"],
                "exit_date": current_date.isoformat(),
                "entry_price": pos["entry_price"],
                "exit_price": exit_price,
                "shares": shares,
                "pnl": pnl,
                "pnl_pct": sell["pnl_pct"] * 100.0,
                "days_held": pos["days_held"],
                "exit_reason": sell["reason"],
            })

            if sell["reason"] == "DAY_5_TIME_EXIT":
                cooldowns[get_canonical_symbol(pos["ticker"])] = current_date

        # 2. Check Open Slots
        used_slots = set(portfolio.keys())
        open_slots = sorted([s for s in range(1, max_slots + 1) if s not in used_slots])

        # 3. Calculate 3-Day Return & Screen Replacements if slots open
        if open_slots:
            candidates = []
            held_canonical = {get_canonical_symbol(p["ticker"]) for p in portfolio.values()}

            for sym in tickers:
                if sym not in price_df.columns:
                    continue
                series = price_df[sym].loc[:current_ts].dropna()
                if len(series) >= 4:
                    p_now = float(series.iloc[-1])
                    p_3d_ago = float(series.iloc[-4])
                    if p_3d_ago > 0:
                        ret_3d = ((p_now - p_3d_ago) / p_3d_ago) * 100.0
                        candidates.append({"ticker": sym, "price": p_now, "ret_3d": ret_3d})

            candidates.sort(key=lambda x: x["ret_3d"])

            # Filter candidates applying edge cases
            eligible_buys = []
            seen_canonical: Set[str] = set(held_canonical)

            for cand in candidates:
                sym = cand["ticker"]
                canon = get_canonical_symbol(sym)

                # Edge Case 1: 1-day cooldown on Day 5 exits
                if canon in cooldowns:
                    continue
                # Edge Case 2: No double down
                if canon in held_canonical:
                    continue
                # Edge Case 3: Dual-class twin
                if canon in seen_canonical:
                    continue

                seen_canonical.add(canon)
                eligible_buys.append(cand)
                if len(eligible_buys) >= len(open_slots):
                    break

            # 4. Sizing & Buying
            # Current equity
            portfolio_val = sum(
                p["shares"] * float(price_df[p["ticker"]].loc[:current_ts].dropna().iloc[-1])
                for p in portfolio.values()
                if p["ticker"] in price_df.columns and not price_df[p["ticker"]].loc[:current_ts].dropna().empty
            )
            total_equity = cash + portfolio_val

            allocation_per_slot = min(total_equity * slot_equity_pct, cash / len(open_slots))

            for idx, slot in enumerate(open_slots):
                if idx < len(eligible_buys):
                    cand = eligible_buys[idx]
                    price = cand["price"]
                    if price > 0:
                        shares = math.floor(allocation_per_slot / price)
                        if shares > 0 and (shares * price) <= cash:
                            cost = shares * price
                            cash -= cost
                            portfolio[slot] = {
                                "slot": slot,
                                "ticker": cand["ticker"],
                                "shares": shares,
                                "entry_price": price,
                                "buy_date": current_date.isoformat(),
                                "cost_basis": cost,
                                "days_held": 0,
                            }

        # Track daily equity
        curr_port_val = sum(
            p["shares"] * float(price_df[p["ticker"]].loc[:current_ts].dropna().iloc[-1])
            for p in portfolio.values()
            if p["ticker"] in price_df.columns and not price_df[p["ticker"]].loc[:current_ts].dropna().empty
        )
        total_equity_today = cash + curr_port_val
        spy_close = float(price_df["SPY"].loc[:current_ts].dropna().iloc[-1]) if "SPY" in price_df.columns else 1.0

        equity_curve.append({
            "date": current_date.isoformat(),
            "equity": total_equity_today,
            "cash": cash,
            "positions_held": len(portfolio),
            "spy_close": spy_close
        })

    # Summary Statistics
    final_equity = equity_curve[-1]["equity"]
    total_ret = ((final_equity - initial_capital) / initial_capital) * 100.0

    spy_start = equity_curve[0]["spy_close"]
    spy_end = equity_curve[-1]["spy_close"]
    spy_ret = ((spy_end - spy_start) / spy_start) * 100.0

    wins = [t for t in history if t["pnl"] > 0]
    losses = [t for t in history if t["pnl"] <= 0]
    win_rate = (len(wins) / len(history) * 100.0) if history else 0.0

    target_exits = len([t for t in history if t["exit_reason"] == "PROFIT_TARGET_HIT"])
    time_exits = len([t for t in history if t["exit_reason"] == "DAY_5_TIME_EXIT"])

    avg_pnl_pct = (sum(t["pnl_pct"] for t in history) / len(history)) if history else 0.0
    avg_hold_days = (sum(t["days_held"] for t in history) / len(history)) if history else 0.0

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # Max Drawdown
    peak = initial_capital
    max_dd = 0.0
    for pt in equity_curve:
        eq = pt["equity"]
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100.0
        if dd > max_dd:
            max_dd = dd

    # Advanced Risk & Return Metrics
    eq_series = pd.Series([pt["equity"] for pt in equity_curve])
    daily_returns = eq_series.pct_change().dropna()
    num_days = len(equity_curve)
    cagr = ((final_equity / initial_capital) ** (252.0 / num_days) - 1.0) * 100.0 if num_days > 0 else 0.0
    
    # Sharpe (assumed rf=4%)
    rf_daily = 0.04 / 252.0
    excess_daily = daily_returns - rf_daily
    sharpe = (excess_daily.mean() / daily_returns.std() * (252 ** 0.5)) if daily_returns.std() > 0 else 0.0

    # Sortino (downside std)
    downside = daily_returns[daily_returns < 0]
    sortino = (excess_daily.mean() / downside.std() * (252 ** 0.5)) if len(downside) > 0 and downside.std() > 0 else 0.0

    # Trade stats
    avg_win_pct = (sum(t["pnl_pct"] for t in wins) / len(wins)) if wins else 0.0
    avg_loss_pct = (sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else 0.0

    sorted_trades = sorted(history, key=lambda x: x["pnl_pct"])
    worst_trade = sorted_trades[0] if sorted_trades else None
    best_trade = sorted_trades[-1] if sorted_trades else None

    # Time exit win rate: how many Day 5 exits were profitable anyway
    day5_trades = [t for t in history if t["exit_reason"] == "DAY_5_TIME_EXIT"]
    day5_wins = [t for t in day5_trades if t["pnl"] > 0]
    day5_win_rate = (len(day5_wins) / len(day5_trades) * 100.0) if day5_trades else 0.0

    print("\n" + "=" * 80)
    print("  BACKTEST PERFORMANCE METRICS")
    print("=" * 80)
    print(f" Starting Equity:        ${initial_capital:,.2f}")
    print(f" Final Account Equity:   ${final_equity:,.2f}")
    print(f" Strategy Total Return:  {total_ret:+.2f}%")
    print(f" Annualized Return (CAGR): {cagr:+.2f}%")
    print(f" Benchmark (SPY) Return: {spy_ret:+.2f}%")
    print(f" Alpha vs Benchmark:     {total_ret - spy_ret:+.2f}%")
    print("-" * 80)
    print(f" Total Trades Executed:  {len(history)}")
    print(f" Win Rate:               {win_rate:.1f}% ({len(wins)} wins / {len(losses)} losses)")
    print(f" Profit Factor:          {profit_factor:.2f}")
    print(f" Sharpe Ratio (rf=4%):   {sharpe:.2f}")
    print(f" Sortino Ratio:          {sortino:.2f}")
    print(f" Max Portfolio Drawdown: -{max_dd:.2f}%")
    print("-" * 80)
    print(f" Average Trade Return:   {avg_pnl_pct:+.2f}%")
    print(f" Average Winner:         {avg_win_pct:+.2f}%")
    print(f" Average Loser:          {avg_loss_pct:+.2f}%")
    print(f" Average Hold Duration:  {avg_hold_days:.1f} trading days")
    print(f" Target Hits (+3%):      {target_exits} ({target_exits / len(history) * 100:.1f}%)")
    print(f" Day 5 Time Exits:       {time_exits} ({time_exits / len(history) * 100:.1f}%) [Win Rate: {day5_win_rate:.1f}%]")
    if best_trade:
        print(f" Best Trade:             {best_trade['ticker']} ({best_trade['buy_date']}) -> {best_trade['pnl_pct']:+.2f}% (${best_trade['pnl']:+,.2f})")
    if worst_trade:
        print(f" Worst Trade:            {worst_trade['ticker']} ({worst_trade['buy_date']}) -> {worst_trade['pnl_pct']:+.2f}% (${worst_trade['pnl']:+,.2f})")
    print("=" * 80 + "\n")

    # Save detailed output for analysis
    import json
    with open("data/backtest_results.json", "w") as f:
        json.dump({
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": initial_capital,
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_ret, 2),
            "cagr_pct": round(cagr, 2),
            "spy_return_pct": round(spy_ret, 2),
            "alpha_pct": round(total_ret - spy_ret, 2),
            "trades_count": len(history),
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "avg_trade_pct": round(avg_pnl_pct, 2),
            "avg_win_pct": round(avg_win_pct, 2),
            "avg_loss_pct": round(avg_loss_pct, 2),
            "avg_hold_days": round(avg_hold_days, 1),
            "target_hits": target_exits,
            "day5_exits": time_exits,
            "day5_win_rate_pct": round(day5_win_rate, 2),
            "best_trade": best_trade,
            "worst_trade": worst_trade,
            "equity_curve": equity_curve[::3] + [equity_curve[-1]], # last 10 points
        }, f, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest Spinach Three-Day Drop-Bounce Pure Clone")
    parser.add_argument("--start", default="2025-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default="2026-11-01", help="End date YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=100000.0, help="Initial capital in USD")
    args = parser.parse_args()

    run_backtest(start_date=args.start, end_date=args.end, initial_capital=args.capital)
