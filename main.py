#!/usr/bin/env python3
"""
Desk-Ready Operational CLI for 'spinach/three-day-drop-bounce' Pure Clone.
Executes the strategy daily between 2:50 PM and 3:00 PM CST.
"""

import sys
import os
import argparse
import datetime
from src.engine import PureCloneEngine
from src.ledger import Ledger
from src.screener import DropBounceScreener
from src.universe import UniverseManager


def format_currency(val: float) -> str:
    return f"${val:,.2f}"


def format_pct(val: float) -> str:
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.2f}%"


def cmd_status(args):
    """Displays the live desk status of the 5-slot portfolio."""
    ledger = Ledger()
    screener = DropBounceScreener()

    held_tickers = ledger.get_active_tickers()
    prices = screener.get_current_prices(held_tickers) if held_tickers else {}
    ledger.update_portfolio_valuation(prices, datetime.date.today())

    print("\n" + "=" * 80)
    print("  SPINACH THREE-DAY DROP-BOUNCE | 5-SLOT PORTFOLIO STATUS (PURE CLONE)")
    print("=" * 80)
    print(f" Total Account Equity: {format_currency(ledger.account_equity):<15} Available Cash: {format_currency(ledger.available_cash)}")
    print(f" Max Positions: 5 Slots (20% equity/slot)   Profit Target: +3.0%   Max Hold: 5 Trading Days")
    print("-" * 80)

    header = f"{'Slot':<6}{'Ticker':<8}{'Buy Date':<12}{'Entry':<10}{'Current':<10}{'Target (+3%)':<14}{'Days':<8}{'Exit Date':<12}{'P&L (%)':<10}"
    print(header)
    print("-" * 80)

    for slot_id in range(1, 6):
        pos = ledger.positions.get(slot_id)
        if not pos:
            print(f"Slot {slot_id:<2} [EMPTY / AVAILABLE]")
            continue

        sym = pos["ticker"]
        bdate = pos["buy_date"]
        entry = format_currency(pos["entry_price"])
        curr = format_currency(pos.get("current_price", pos["entry_price"]))
        target = format_currency(pos["target_price"])
        days = f"{pos.get('days_held', 0)}/5"
        m_exit = pos["mandatory_exit_date"]
        pnl = pos.get("unrealized_pnl_pct", 0.0)
        pnl_str = format_pct(pnl)

        print(f"Slot {slot_id:<2} {sym:<8}{bdate:<12}{entry:<10}{curr:<10}{target:<14}{days:<8}{m_exit:<12}{pnl_str:<10}")

    print("=" * 80 + "\n")


def cmd_scan(args):
    """Runs a standalone screener scan of S&P 500 + Nasdaq-100 universe."""
    print("\n[+] Scanning S&P 500 + Nasdaq-100 universe for 3-Day Drop Losers...")
    ledger = Ledger()
    screener = DropBounceScreener()

    candidates = screener.scan_3day_drops(
        held_tickers=ledger.get_active_tickers(),
        limit=args.limit
    )

    print("\n" + "=" * 85)
    print(f"  TOP {len(candidates)} WORST 3-DAY LOSERS ACROSS S&P 500 + NASDAQ-100")
    print("=" * 85)
    print(f"{'Rank':<6}{'Ticker':<8}{'Company Name':<30}{'Current Px':<12}{'3D Ago Px':<12}{'3D Return %':<12}{'Index':<10}")
    print("-" * 85)

    for i, c in enumerate(candidates, 1):
        idx_str = "/".join(c.get("indices", []))
        print(f"#{i:<5}{c['ticker']:<8}{c['name'][:28]:<30}{format_currency(c['current_price']):<12}{format_currency(c['price_3d_ago']):<12}{format_pct(c['return_3d_pct']):<12}{idx_str:<10}")

    print("=" * 85 + "\n")


def cmd_run(args):
    """Executes the daily 2:50 PM - 3:00 PM CST afternoon SOP."""
    print("\n" + "#" * 80)
    print("  SPINACH THREE-DAY DROP-BOUNCE | AFTERNOON 2:55 PM CST EXECUTION PROTOCOL")
    print("#" * 80)

    engine = PureCloneEngine()
    audit = engine.run_daily_audit()

    # Step 1 Display: Audits & Sells
    print(f"\n[STEP 1: AUDIT CURRENT HOLDINGS]")
    print("-" * 80)
    sells = audit["sell_orders"]
    if sells:
        print(f" [!] {len(sells)} POSITION(S) TRIGGERED EXIT CRITERIA (SELL AT 2:58 PM CST):")
        for s in sells:
            print(f"   * Slot {s['slot']}: SELL {s['shares']} shares of {s['ticker']} @ ~{format_currency(s['est_price'])}")
            print(f"     Reason: {s['reason']} | Est. Proceeds: {format_currency(s['est_proceeds'])}")
    else:
        print(" [*] Zero exit triggers hit. All current positions are held.")

    # Step 2 Display: Open Slots & Sizing
    open_count = audit["open_slot_count"]
    print(f"\n[STEP 2: OPEN SLOTS & REPLACEMENTS]")
    print("-" * 80)
    print(f" Open Slots Available Today: {open_count} slot(s) -> {audit['open_slots']}")

    if open_count == 0:
        print(" [✓] 0 Open Slots. Portfolio is 100% full. Zero replacement trades needed today. Close your laptop!\n")
        return

    # Step 3 Display: Buy Orders
    buys = audit["buy_orders"]
    print(f"\n[STEP 3: 20% EQUITY SIZING & REPLACEMENT BUYS (FIRE AT 2:58 PM CST)]")
    print("-" * 80)
    if buys:
        for b in buys:
            print(f"   * Assign to Slot {b['slot']}: BUY {b['shares']} shares of {b['ticker']} ({b['name']})")
            print(f"     Est. Price: {format_currency(b['est_price'])} | 3-Day Drop: {format_pct(b['return_3d_pct'])}")
            print(f"     Allocated Budget: {format_currency(b['allocation_budget'])} | Est. Total Cost: {format_currency(b['est_cost'])}\n")
    else:
        print(" [!] No eligible candidates met filtering requirements.")

    # Confirmation
    if args.yes:
        confirm = "y"
    else:
        print("-" * 80)
        confirm = input("Would you like to execute and commit these orders to your ledger now? (y/n): ").strip().lower()

    if confirm == "y":
        res = engine.execute_plan(audit)
        print("\n[✓] Orders successfully executed and committed to data/portfolio.json!")
        print(f"    Available Cash: {format_currency(res['final_cash'])}")
        print(f"    Total Account Equity: {format_currency(res['final_equity'])}\n")
    else:
        print("\n[-] Orders not committed. Run again when ready to log fills.\n")


def cmd_set_account(args):
    """Sets account equity and cash in configuration."""
    ledger = Ledger()
    if args.equity is not None:
        ledger.account_equity = float(args.equity)
    if args.cash is not None:
        ledger.available_cash = float(args.cash)
    ledger.save()
    print(f"\n[✓] Account updated: Equity = {format_currency(ledger.account_equity)}, Cash = {format_currency(ledger.available_cash)}\n")


def cmd_history(args):
    """Displays completed trade history and strategy performance statistics."""
    ledger = Ledger()
    if not os.path.exists(ledger.history_path):
        print("\nNo completed trades recorded yet.\n")
        return

    import json
    with open(ledger.history_path, "r") as f:
        history = json.load(f)

    if not history:
        print("\nNo completed trades recorded yet.\n")
        return

    print("\n" + "=" * 95)
    print("  COMPLETED TRADES JOURNAL (SPINACH PURE CLONE)")
    print("=" * 95)
    header = f"{'Ticker':<8}{'Buy Date':<12}{'Exit Date':<12}{'Entry Px':<10}{'Exit Px':<10}{'Shares':<8}{'P&L ($)':<12}{'P&L (%)':<10}{'Days':<6}{'Exit Reason':<20}"
    print(header)
    print("-" * 95)

    total_pnl = 0.0
    wins = 0
    for h in history:
        pnl = h["pnl"]
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        print(f"{h['ticker']:<8}{h['buy_date']:<12}{h['exit_date']:<12}{format_currency(h['entry_price']):<10}{format_currency(h['exit_price']):<10}{h['shares']:<8}{format_currency(pnl):<12}{format_pct(h['pnl_pct']):<10}{h['days_held']:<6}{h['exit_reason'][:20]:<20}")

    win_rate = (wins / len(history)) * 100.0 if history else 0.0
    print("-" * 95)
    print(f"Total Closed Trades: {len(history)} | Win Rate: {win_rate:.1f}% | Total Realized P&L: {format_currency(total_pnl)}")
    print("=" * 95 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Spinach Three-Day Drop-Bounce Pure Clone Engine")
    subparsers = parser.add_subparsers(dest="command")

    # run command
    p_run = subparsers.add_parser("run", help="Run daily 2:55 PM CST afternoon routine")
    p_run.add_argument("--yes", "-y", action="store_true", help="Auto-confirm execution without interactive prompt")

    # status command
    subparsers.add_parser("status", help="Display current 5-slot portfolio ledger status")

    # scan command
    p_scan = subparsers.add_parser("scan", help="Scan S&P 500 + Nasdaq-100 universe for 3-day drop losers")
    p_scan.add_argument("--limit", type=int, default=10, help="Number of worst losers to display")

    # history command
    subparsers.add_parser("history", help="Show closed trades journal and P&L statistics")

    # set-account command
    p_acc = subparsers.add_parser("set-account", help="Update initial total account equity and cash")
    p_acc.add_argument("--equity", type=float, help="Total account equity in USD")
    p_acc.add_argument("--cash", type=float, help="Available cash in USD")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "scan":
        cmd_scan(args)
    elif args.command == "history":
        cmd_history(args)
    elif args.command == "set-account":
        cmd_set_account(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
