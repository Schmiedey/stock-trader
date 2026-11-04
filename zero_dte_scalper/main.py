#!/usr/bin/env python3
"""
0DTE Options Scalping Desk CLI
High-velocity momentum and mean-reversion engine for SPY and QQQ 0DTE contracts.
"""

import sys
import argparse
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

# Local imports
from src.config import load_config, get_market_session_state, get_current_ny_time
from src.market_data import fetch_0dte_chain, get_0dte_expiration
from src.signals import evaluate_scalp_signals
from src.options_selector import select_best_contract
from src.risk_manager import check_trading_permissions
from src.tracker import (
    load_active_positions,
    open_position,
    close_position,
    update_active_positions_quotes,
    get_today_stats,
    calculate_performance_metrics,
    load_trade_history
)

console = Console()


def print_header():
    """Prints strategy branding banner."""
    header_text = Text()
    header_text.append("⚡ 0DTE INTRADAY MOMENTUM SCALPER ⚡\n", style="bold yellow")
    header_text.append("Precision SPY & QQQ Same-Day Expiration Execution Engine", style="italic cyan")
    console.print(Panel(header_text, border_style="yellow", expand=False))


def cmd_rules(args):
    """Display 0DTE core trading rules and checklist."""
    print_header()
    rules_text = (
        "[bold red]RULE 1: HARD STOP-LOSS AT -15% PREMIUM[/bold red]\n"
        "  • 0DTE contracts suffer brutal theta decay. Never average down or hold losers.\n\n"
        "[bold yellow]RULE 2: DAILY CIRCUIT BREAKER (-$600 / 2% ACCOUNT)[/bold yellow]\n"
        "  • If daily loss hits $600, trading is TERMINATED for the day immediately.\n\n"
        "[bold cyan]RULE 3: MIDDAY CHOP EXCLUSION (11:30 - 13:30 EST)[/bold cyan]\n"
        "  • Avoid midday chop. Theta will bleed long premiums while price goes sideways.\n\n"
        "[bold magenta]RULE 4: MANDATORY FLAT BY 15:50 EST[/bold magenta]\n"
        "  • Liquidate all open contracts before 3:50 PM. Never take 0DTE into the 4:00 PM close.\n\n"
        "[bold green]RULE 5: 2-TRANCHE PROFIT TAKING[/bold green]\n"
        "  • Target 1 (+20%): Scale out 50%, move stop to Breakeven.\n"
        "  • Target 2 (+40%): Close remaining 50% or trail with 1m EMA9."
    )
    console.print(Panel(rules_text, title="[bold]Institutional 0DTE Scalp Rules[/bold]", border_style="green"))


def cmd_scan(args):
    """Scan intraday price action and 0DTE chains for active setups."""
    symbol = args.symbol.upper()
    console.print(f"\n[cyan]🔍 Scanning intraday technicals & 0DTE options for [bold]{symbol}[/bold]...[/cyan]")

    # Check session
    config = load_config()
    session = get_market_session_state(config)
    
    session_color = "green" if session["trading_allowed"] else "yellow"
    session_info = f"Phase: [bold {session_color}]{session['session_phase']}[/bold {session_color}] | NY Time: {session['current_time_str']} | Allowed: {session['trading_allowed']}"
    console.print(Panel(session_info, title="Market Session State", border_style=session_color))

    # Evaluate signals
    try:
        sig = evaluate_scalp_signals(symbol=symbol, interval="5m")
    except Exception as e:
        console.print(f"[red]Error fetching technicals for {symbol}: {e}[/red]")
        return

    # Technical Overview Table
    tech_table = Table(title=f"{symbol} Intraday Technical Status", border_style="cyan")
    tech_table.add_column("Current Price", justify="right", style="bold")
    tech_table.add_column("VWAP", justify="right")
    tech_table.add_column("EMA 9", justify="right")
    tech_table.add_column("EMA 21", justify="right")
    tech_table.add_column("RSI (14)", justify="right")
    tech_table.add_column("15m ORB High", justify="right")
    tech_table.add_column("15m ORB Low", justify="right")
    tech_table.add_column("PDH / PDL", justify="center")

    levels = sig["levels"]
    tech_table.add_row(
        f"${sig['current_price']:.2f}",
        f"${sig['vwap']:.2f}",
        f"${sig['ema9']:.2f}",
        f"${sig['ema21']:.2f}",
        f"{sig['rsi']:.1f}",
        f"${levels['orb_15m_high']:.2f}",
        f"${levels['orb_15m_low']:.2f}",
        f"${levels['pdh']:.2f} / ${levels['pdl']:.2f}"
    )
    console.print(tech_table)

    # Signal Verdict Panel
    sig_color = "green" if sig["signal"] == "CALL" else ("red" if sig["signal"] == "PUT" else "dim")
    signal_summary = (
        f"Verdict: [bold {sig_color}]{sig['signal']}[/bold {sig_color}] (Conviction: [bold]{sig['conviction']}[/bold])\n"
        f"Primary Setup: {sig['primary_setup']}\n"
        f"Active Triggers: {', '.join(sig['active_triggers']) if sig['active_triggers'] else 'None'}\n"
        f"Analysis: {sig['reason']}\n"
        f"Underlying Invalidation Level: [red]${sig['underlying_stop']:.2f}[/red] | Target Level: [green]${sig['underlying_target']:.2f}[/green]"
    )
    console.print(Panel(signal_summary, title="[bold]Scalp Signal Engine[/bold]", border_style=sig_color))

    # Recommended 0DTE Option Contract
    if sig["signal"] in ["CALL", "PUT"]:
        console.print("\n[yellow]🎯 Optimal 0DTE Strike Recommendation:[/yellow]")
        recommendation = select_best_contract(symbol=symbol, action=sig["signal"], config=config)
        if recommendation:
            c = recommendation["contract"]
            s = recommendation["sizing"]
            t = recommendation["targets"]

            opt_table = Table(border_style="yellow")
            opt_table.add_column("Contract", style="bold cyan")
            opt_table.add_column("Strike", justify="right")
            opt_table.add_column("Bid / Ask", justify="center")
            opt_table.add_column("Spread", justify="center")
            opt_table.add_column("Delta", justify="right")
            opt_table.add_column("Theta/Hr", justify="right", style="red")
            opt_table.add_column("Suggested Qty", justify="center", style="bold green")
            opt_table.add_column("Stop Price (-15%)", justify="right", style="bold red")
            opt_table.add_column("Target 1 (+20%)", justify="right", style="bold green")
            opt_table.add_column("Target 2 (+40%)", justify="right", style="bold green")

            spread_style = "green" if c["spread"] <= 0.03 else ("yellow" if c["spread"] <= 0.05 else "red")
            opt_table.add_row(
                c["contract_symbol"],
                f"${c['strike']:.1f}",
                f"${c['bid']:.2f} / ${c['ask']:.2f}",
                f"[{spread_style}]${c['spread']:.2f}[/{spread_style}]",
                f"{c['delta']:.2f}",
                f"${c['theta_hour']:.2f}",
                f"{s['suggested_contracts']} contracts (${s['total_outlay']:.0f} outlay)",
                f"${t['stop_loss_price']:.2f}",
                f"${t['tp1_price']:.2f}",
                f"${t['tp2_price']:.2f}"
            )
            console.print(opt_table)
            console.print(f"[dim]Risk Budget: ${s['max_risk_budget']:.0f} | Max Projected Loss: -${s['projected_max_loss']:.2f}[/dim]")
            console.print(f"\n[bold green]To execute this trade, run:[/bold green] [white]python3 main.py enter --symbol {symbol} --action {sig['signal']} --auto[/white]")
    else:
        console.print("[dim]No directional setup currently triggered. Waiting for high-probability alignment.[/dim]")


def cmd_chain(args):
    """View 0DTE options chain around current spot price."""
    symbol = args.symbol.upper()
    console.print(f"\n[cyan]📊 Fetching 0DTE option chain for [bold]{symbol}[/bold]...[/cyan]")
    chain_data = fetch_0dte_chain(symbol)
    spot = chain_data["spot_price"]
    exp = chain_data["expiration"]

    console.print(f"Spot Price: [bold green]${spot:.2f}[/bold green] | Expiration: [bold yellow]{exp}[/bold yellow] | Hours to Close: [bold]{chain_data['tte_hours']:.1f}h[/bold]\n")

    # Filter strikes within range of spot
    strike_range = args.range
    calls = [c for c in chain_data["calls"] if abs(c["strike"] - spot) <= strike_range]
    puts = [p for p in chain_data["puts"] if abs(p["strike"] - spot) <= strike_range]

    # Map by strike
    strikes = sorted(list(set([c["strike"] for c in calls] + [p["strike"] for p in puts])))
    call_map = {c["strike"]: c for c in calls}
    put_map = {p["strike"]: p for p in puts}

    chain_table = Table(title=f"0DTE Board ({exp}) - Strikes $\\pm${strike_range}", border_style="cyan")
    chain_table.add_column("Call Bid/Ask", justify="center")
    chain_table.add_column("Call Spread", justify="center")
    chain_table.add_column("Call Delta", justify="right", style="green")
    chain_table.add_column("STRIKE", justify="center", style="bold yellow")
    chain_table.add_column("Put Delta", justify="left", style="red")
    chain_table.add_column("Put Spread", justify="center")
    chain_table.add_column("Put Bid/Ask", justify="center")

    for s in strikes:
        c = call_map.get(s, {})
        p = put_map.get(s, {})

        c_quote = f"${c.get('bid', 0):.2f} / ${c.get('ask', 0):.2f}" if c else "-"
        p_quote = f"${p.get('bid', 0):.2f} / ${p.get('ask', 0):.2f}" if p else "-"
        c_spread = f"${c.get('spread', 0):.2f}" if c else "-"
        p_spread = f"${p.get('spread', 0):.2f}" if p else "-"
        c_delta = f"{c.get('delta', 0):.2f}" if c else "-"
        p_delta = f"{p.get('delta', 0):.2f}" if p else "-"

        strike_display = f"${s:.1f}"
        if abs(s - spot) <= 0.6:
            strike_display = f"👉 {strike_display} (ATM)"

        chain_table.add_row(c_quote, c_spread, c_delta, strike_display, p_delta, p_spread, p_quote)

    console.print(chain_table)


def cmd_enter(args):
    """Execute a 0DTE scalp entry and record into active positions."""
    symbol = args.symbol.upper()
    config = load_config()

    # Risk manager check
    today_stats = get_today_stats()
    allowed, reason = check_trading_permissions(
        daily_realized_pnl=today_stats["realized_pnl_usd"],
        daily_trades_count=today_stats["trades_count"],
        config=config,
        bypass_time_filter=args.force
    )

    if not allowed and not args.force:
        console.print(f"\n[bold red]❌ ENTRY REJECTED BY RISK ENGINE:[/bold red] {reason}")
        console.print("[dim]Use --force to bypass session window filters during backtests or live testing.[/dim]")
        return

    action = args.action.upper() if args.action else "CALL"
    if args.auto:
        sig = evaluate_scalp_signals(symbol=symbol)
        if sig["signal"] in ["CALL", "PUT"]:
            action = sig["signal"]
            setup_name = sig["primary_setup"]
        else:
            setup_name = "Auto Manual Override"
    else:
        setup_name = args.setup or "Manual Discretionary Scalp"

    # Select contract
    rec = select_best_contract(symbol=symbol, action=action, config=config)
    if not rec:
        console.print("[red]Could not find suitable 0DTE contract meeting criteria.[/red]")
        return

    contract = rec["contract"]
    entry_price = contract["ask"] if contract["ask"] > 0 else contract["last_price"]
    qty = args.contracts if args.contracts else rec["sizing"]["suggested_contracts"]
    stop_price = rec["targets"]["stop_loss_price"]
    tp1_price = rec["targets"]["tp1_price"]
    tp2_price = rec["targets"]["tp2_price"]

    pos = open_position(
        symbol=symbol,
        action=action,
        contract_symbol=contract["contract_symbol"],
        strike=contract["strike"],
        expiration=rec["expiration"],
        entry_price=entry_price,
        quantity=qty,
        stop_loss_price=stop_price,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        setup_name=setup_name,
        notes=args.notes or ""
    )

    console.print(f"\n[bold green]✅ 0DTE POSITION FILLED & LOGGED![/bold green]")
    summary = (
        f"Position ID: [bold]{pos['id']}[/bold]\n"
        f"Contract: [bold cyan]{pos['contract_symbol']}[/bold cyan] ({pos['action']})\n"
        f"Strike: ${pos['strike']:.1f} | Expiration: {pos['expiration']}\n"
        f"Quantity: [bold]{pos['current_quantity']}[/bold] contracts @ [bold]${pos['entry_price']:.2f}[/bold] (Total Outlay: ${pos['entry_price'] * 100 * pos['current_quantity']:.2f})\n"
        f"Stop Loss: [red]${pos['stop_loss_price']:.2f} (-15%)[/red]\n"
        f"Target 1: [green]${pos['tp1_price']:.2f} (+20%)[/green] (Scale out 50%)\n"
        f"Target 2: [green]${pos['tp2_price']:.2f} (+40%)[/green] (Full close / trail runner)"
    )
    console.print(Panel(summary, title="Active Position Details", border_style="green"))


def cmd_status(args):
    """Monitor active open positions and live unrealized P&L."""
    console.print("\n[cyan]📈 Updating active positions with live market quotes...[/cyan]")
    positions = update_active_positions_quotes()

    if not positions:
        console.print("[dim]No open 0DTE positions active.[/dim]")
        # Print today's stats anyway
        stats = get_today_stats()
        console.print(f"\nToday's Performance: [bold]{stats['trades_count']}[/bold] trades | Realized P&L: [bold {'green' if stats['realized_pnl_usd'] >= 0 else 'red'}]${stats['realized_pnl_usd']:+.2f}[/bold {'green' if stats['realized_pnl_usd'] >= 0 else 'red'}]")
        return

    table = Table(title="Active 0DTE Positions", border_style="green")
    table.add_column("ID", style="bold")
    table.add_column("Symbol", justify="center")
    table.add_column("Action", justify="center")
    table.add_column("Strike", justify="right")
    table.add_column("Qty", justify="center")
    table.add_column("Entry", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Unrealized P&L", justify="right")
    table.add_column("Stop (-15%)", justify="right", style="red")
    table.add_column("Target 1 (+20%)", justify="right", style="green")
    table.add_column("Status / Alert", justify="center")

    total_unrealized_usd = 0.0
    for p in positions:
        total_unrealized_usd += p["unrealized_pnl_usd"]
        pnl_color = "green" if p["unrealized_pnl_usd"] >= 0 else "red"
        pnl_str = f"[{pnl_color}]${p["unrealized_pnl_usd"]:+.2f} ({p["unrealized_pnl_pct"]:+.1f}%)[/{pnl_color}]"

        alert = "HOLDING"
        if p["current_price"] <= p["stop_loss_price"]:
            alert = "[bold red]STOP BREACHED![/bold red]"
        elif p["current_price"] >= p["tp2_price"]:
            alert = "[bold green]TP2 REACHED![/bold green]"
        elif p["current_price"] >= p["tp1_price"]:
            alert = "[bold yellow]TP1 REACHED![/bold yellow]"

        table.add_row(
            p["id"],
            p["symbol"],
            p["action"],
            f"${p['strike']:.1f}",
            str(p["current_quantity"]),
            f"${p['entry_price']:.2f}",
            f"${p['current_price']:.2f}",
            pnl_str,
            f"${p['stop_loss_price']:.2f}",
            f"${p['tp1_price']:.2f}",
            alert
        )

    console.print(table)
    tot_color = "green" if total_unrealized_usd >= 0 else "red"
    console.print(f"Total Unrealized P&L: [bold {tot_color}]${total_unrealized_usd:+.2f}[/bold {tot_color}]\n")


def cmd_exit(args):
    """Close an active position and realize P&L."""
    positions = load_active_positions()
    if not positions:
        console.print("[dim]No open positions to close.[/dim]")
        return

    if args.all:
        console.print(f"\n[yellow]Liquidating all {len(positions)} active positions at current market quotes...[/yellow]")
        for p in list(positions):
            trade = close_position(
                position_id=p["id"],
                exit_price=p["current_price"],
                reason=args.reason or "FLUSH_ALL"
            )
            pnl_c = "green" if trade["realized_pnl_usd"] >= 0 else "red"
            console.print(f"Closed {p['contract_symbol']}: [bold {pnl_c}]${trade['realized_pnl_usd']:+.2f} ({trade['realized_pnl_pct']:+.1f}%)[/bold {pnl_c}]")
        return

    pos_id = args.id
    if not pos_id:
        console.print("[red]Please specify --id <position_id> or --all[/red]")
        return

    target_pos = next((p for p in positions if p["id"] == pos_id), None)
    if not target_pos:
        console.print(f"[red]Position ID {pos_id} not found.[/red]")
        return

    exit_price = args.price if args.price else target_pos["current_price"]
    trade = close_position(
        position_id=pos_id,
        exit_price=exit_price,
        reason=args.reason or "MANUAL_EXIT",
        partial_qty=args.contracts
    )

    pnl_c = "green" if trade["realized_pnl_usd"] >= 0 else "red"
    console.print(f"\n[bold green]✅ POSITION CLOSED & LOGGED TO JOURNAL![/bold green]")
    console.print(f"Contract: {trade['contract_symbol']} | Qty: {trade['quantity_closed']}")
    console.print(f"Entry: ${trade['entry_price']:.2f} -> Exit: ${trade['exit_price']:.2f}")
    console.print(f"Realized P&L: [bold {pnl_c}]${trade['realized_pnl_usd']:+.2f} ({trade['realized_pnl_pct']:+.1f}%)[/bold {pnl_c}]")


def cmd_history(args):
    """View closed trade history and operational performance metrics."""
    metrics = calculate_performance_metrics()
    today_stats = get_today_stats()
    history = load_trade_history()

    print_header()
    summary_table = Table(title="Operational Scalping Performance", border_style="cyan")
    summary_table.add_column("Metric", style="bold")
    summary_table.add_column("Value", justify="right")

    pnl_c = "green" if metrics["total_realized_pnl_usd"] >= 0 else "red"
    summary_table.add_row("Total Trades", str(metrics["total_trades"]))
    summary_table.add_row("Win Rate", f"{metrics['win_rate_pct']:.1f}% ({metrics['wins']}W / {metrics['losses']}L)")
    summary_table.add_row("Profit Factor", f"{metrics['profit_factor']:.2f}")
    summary_table.add_row("Total Realized P&L", f"[{pnl_c}]${metrics['total_realized_pnl_usd']:+.2f}[/{pnl_c}]")
    summary_table.add_row("Today's Realized P&L", f"${today_stats['realized_pnl_usd']:+.2f} ({today_stats['trades_count']} trades)")
    summary_table.add_row("Average Win", f"[green]${metrics['avg_win_usd']:.2f}[/green]")
    summary_table.add_row("Average Loss", f"[red]-${metrics['avg_loss_usd']:.2f}[/red]")
    summary_table.add_row("Max Single Win / Loss", f"+${metrics['max_win_usd']:.2f} / -${metrics['max_loss_usd']:.2f}")

    console.print(summary_table)

    if history:
        hist_table = Table(title="Recent Closed Trades (Last 10)", border_style="dim")
        hist_table.add_column("Date / Time", style="dim")
        hist_table.add_column("Contract", style="bold")
        hist_table.add_column("Action", justify="center")
        hist_table.add_column("Qty", justify="center")
        hist_table.add_column("Entry", justify="right")
        hist_table.add_column("Exit", justify="right")
        hist_table.add_column("P&L ($)", justify="right")
        hist_table.add_column("P&L (%)", justify="right")
        hist_table.add_column("Exit Reason", justify="center")

        for t in history[-10:]:
            p_color = "green" if t["realized_pnl_usd"] >= 0 else "red"
            hist_table.add_row(
                t.get("exit_timestamp", "")[:16],
                t["contract_symbol"],
                t["action"],
                str(t["quantity_closed"]),
                f"${t['entry_price']:.2f}",
                f"${t['exit_price']:.2f}",
                f"[{p_color}]${t['realized_pnl_usd']:+.2f}[/{p_color}]",
                f"[{p_color}]{t['realized_pnl_pct']:+.1f}%[/{p_color}]",
                t.get("exit_reason", "EXIT")
            )
        console.print(hist_table)


def main():
    parser = argparse.ArgumentParser(
        description="0DTE Options Intraday Momentum & Mean-Reversion Scalper",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Available Commands")

    # scan
    p_scan = subparsers.add_parser("scan", help="Scan SPY/QQQ for 0DTE setups & recommendations")
    p_scan.add_argument("--symbol", default="SPY", help="Ticker symbol (SPY or QQQ)")

    # chain
    p_chain = subparsers.add_parser("chain", help="Display 0DTE options board around ATM")
    p_chain.add_argument("--symbol", default="SPY", help="Ticker symbol")
    p_chain.add_argument("--range", type=float, default=5.0, help="Dollar strike range around spot")

    # enter
    p_enter = subparsers.add_parser("enter", help="Enter a 0DTE scalp trade")
    p_enter.add_argument("--symbol", default="SPY", help="Ticker symbol")
    p_enter.add_argument("--action", choices=["CALL", "PUT", "call", "put"], help="Trade direction")
    p_enter.add_argument("--contracts", type=int, help="Number of contracts")
    p_enter.add_argument("--auto", action="store_true", help="Auto-select recommended signal & sizing")
    p_enter.add_argument("--setup", help="Setup name / trigger")
    p_enter.add_argument("--notes", help="Trade notes")
    p_enter.add_argument("--force", action="store_true", help="Bypass session time filters")

    # status / monitor
    subparsers.add_parser("status", help="Monitor active positions & live unrealized P&L")
    subparsers.add_parser("monitor", help="Alias for status")

    # exit
    p_exit = subparsers.add_parser("exit", help="Close an open position")
    p_exit.add_argument("--id", help="Position ID to close")
    p_exit.add_argument("--price", type=float, help="Exit execution price override")
    p_exit.add_argument("--contracts", type=int, help="Partial close quantity")
    p_exit.add_argument("--reason", help="Reason for exit (e.g. TP1, TP2, STOP, MANUAL)")
    p_exit.add_argument("--all", action="store_true", help="Liquidate all open positions")

    # history
    subparsers.add_parser("history", help="View closed trades & performance metrics")

    # rules
    subparsers.add_parser("rules", help="Display 0DTE core rules cheat sheet")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "rules":
        cmd_rules(args)
    elif args.command == "scan":
        cmd_scan(args)
    elif args.command == "chain":
        cmd_chain(args)
    elif args.command == "enter":
        cmd_enter(args)
    elif args.command in ["status", "monitor"]:
        cmd_status(args)
    elif args.command == "exit":
        cmd_exit(args)
    elif args.command == "history":
        cmd_history(args)


if __name__ == "__main__":
    main()
