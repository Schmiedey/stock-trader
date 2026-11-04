#!/usr/bin/env python3
"""
0DTE Options Scalping Historical Backtest Engine
Simulates intraday 0DTE options execution on SPY and QQQ over the last 3 months
using Black-Scholes Greek modeling, realistic theta decay, bid-ask slippage, and strict risk controls.
"""

import os
import sys
import argparse
from datetime import datetime, time, timedelta
import pytz
import numpy as np
import pandas as pd
import yfinance as yf
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

# Ensure local zero_dte_scalper/src is loaded first
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from src.greeks import black_scholes_price, calculate_greeks

console = Console()


def fetch_historical_intraday(symbol: str = "SPY", period: str = "60d", interval: str = "5m") -> pd.DataFrame:
    """Fetch 60 days of 5-minute intraday bars with NY timezone conversion."""
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, prepost=False)
    if df.empty:
        raise ValueError(f"No intraday historical data returned for {symbol}")

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert("America/New_York")
    else:
        df.index = df.index.tz_convert("America/New_York")

    return df


def fetch_vix_history(period: str = "3mo") -> dict:
    """Fetch daily VIX closing prices mapped by date string (YYYY-MM-DD)."""
    try:
        vix = yf.Ticker("^VIX")
        df = vix.history(period=period)
        vix_map = {}
        for idx, row in df.iterrows():
            d_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
            vix_map[d_str] = float(row["Close"]) / 100.0  # e.g. 18.5 -> 0.185
        return vix_map
    except Exception:
        return {}


def fetch_macro_trend(symbol: str = "SPY") -> set:
    """Fetch 6-month daily history and identify dates where Close > 20-day SMA."""
    try:
        d_df = yf.Ticker(symbol).history(period="6mo", interval="1d")
        d_df["SMA20"] = d_df["Close"].rolling(20).mean()
        bull_dates = set()
        for idx, row in d_df.iterrows():
            if row["Close"] > row["SMA20"]:
                d_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
                bull_dates.add(d_str)
        return bull_dates
    except Exception:
        return set()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate VWAP, EMA 9, EMA 21, RSI 14, and rolling volume on intraday data."""
    df = df.copy()
    df["Date"] = df.index.date

    # Typical Price & Volume
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
    df["VP"] = typical_price * df["Volume"]

    # VWAP reset daily
    grouped = df.groupby("Date")
    df["Cum_VP"] = grouped["VP"].cumsum()
    df["Cum_Vol"] = grouped["Volume"].cumsum()
    df["VWAP"] = df["Cum_VP"] / df["Cum_Vol"].replace(0, np.nan)
    df["VWAP"] = df["VWAP"].ffill()

    # EMA 9 and 21
    df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA21"] = df["Close"].ewm(span=21, adjust=False).mean()

    # RSI 14
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI14"] = (100 - (100 / (1 + rs))).fillna(50.0)

    # Rolling volume average (20 bars)
    df["Vol_Avg20"] = df["Volume"].rolling(window=20).mean().bfill()

    return df


class Position:
    """Represents an active simulated 0DTE option trade."""

    def __init__(
        self,
        trade_id: int,
        date_str: str,
        entry_time: datetime,
        symbol: str,
        action: str,  # "CALL" or "PUT"
        strike: float,
        entry_spot: float,
        entry_opt_price: float,
        quantity: int,
        stop_loss_pct: float,
        tp1_pct: float,
        tp2_pct: float,
        setup: str,
        iv: float
    ):
        self.trade_id = trade_id
        self.date_str = date_str
        self.entry_time = entry_time
        self.symbol = symbol
        self.action = action.upper()
        self.strike = strike
        self.entry_spot = entry_spot
        self.entry_opt_price = entry_opt_price
        self.initial_qty = quantity
        self.current_qty = quantity
        self.stop_loss_pct = stop_loss_pct
        self.tp1_pct = tp1_pct
        self.tp2_pct = tp2_pct
        self.setup = setup
        self.iv = iv

        # Target and stop option price levels
        self.stop_loss_price = round(entry_opt_price * (1.0 - stop_loss_pct), 2)
        self.tp1_price = round(entry_opt_price * (1.0 + tp1_pct), 2)
        self.tp2_price = round(entry_opt_price * (1.0 + tp2_pct), 2)

        self.tp1_hit = False
        self.realized_pnl_usd = 0.0
        self.exit_time = None
        self.exit_opt_price = None
        self.exit_reason = None
        self.holding_bars = 0


def run_backtest(
    symbol: str = "SPY",
    risk_budget: float = 300.0,
    stop_loss_pct: float = 0.20,
    tp1_pct: float = 0.25,
    tp2_pct: float = 0.50,
    max_daily_loss: float = 600.0,
    max_trades_per_day: int = 3,
    slippage: float = 0.02,
    mode: str = "trend-filtered",  # "standard" or "trend-filtered"
    eval_on_close: bool = True
) -> dict:
    """
    Execute full historical 0DTE backtest over the 60-day period.
    """
    console.print(f"[cyan]Downloading historical intraday data for {symbol}...[/cyan]")
    raw_df = fetch_historical_intraday(symbol=symbol, period="60d", interval="5m")
    vix_map = fetch_vix_history()
    bull_dates = fetch_macro_trend(symbol=symbol) if mode == "trend-filtered" else set()

    df = compute_indicators(raw_df)
    unique_dates = sorted(list(set(df.index.date)))

    all_trades = []
    trade_id_counter = 1

    # Daily loop
    for day_idx, d in enumerate(unique_dates):
        d_str = d.strftime("%Y-%m-%d")
        is_bull_regime = d_str in bull_dates
        day_bars = df[df["Date"] == d]
        if len(day_bars) < 10:
            continue

        # Get previous day's high/low
        pdh = None
        pdl = None
        if day_idx > 0:
            prev_day_date = unique_dates[day_idx - 1]
            prev_bars = df[df["Date"] == prev_day_date]
            if not prev_bars.empty:
                pdh = float(prev_bars["High"].max())
                pdl = float(prev_bars["Low"].min())

        # Establish 15-minute Opening Range (09:30 to 09:45)
        orb_bars = day_bars.between_time("09:30", "09:45")
        if not orb_bars.empty:
            orb_high = float(orb_bars["High"].max())
            orb_low = float(orb_bars["Low"].min())
        else:
            orb_high = float(day_bars["High"].iloc[0])
            orb_low = float(day_bars["Low"].iloc[0])

        daily_iv = vix_map.get(d_str, 0.18)
        daily_trades_count = 0
        daily_realized_pnl = 0.0
        daily_circuit_breaker = False

        active_pos: Position | None = None

        # Intraday 5-minute bar loop
        for bar_idx, (bar_time, bar) in enumerate(day_bars.iterrows()):
            time_str = bar_time.strftime("%H:%M")
            spot_close = float(bar["Close"])
            spot_high = float(bar["High"])
            spot_low = float(bar["Low"])

            # Calculate remaining time to 16:00 EST in years
            close_datetime = bar_time.replace(hour=16, minute=0, second=0)
            diff_seconds = max(60.0, (close_datetime - bar_time).total_seconds())
            tte_years = diff_seconds / (365.25 * 86400.0)

            # -------------------------------------------------------------
            # 1. Manage Active Position (if any)
            # -------------------------------------------------------------
            if active_pos is not None:
                active_pos.holding_bars += 1
                is_call = (active_pos.action == "CALL")
                opt_type = "call" if is_call else "put"

                opt_price_close = black_scholes_price(spot_close, active_pos.strike, tte_years, daily_iv, 0.045, opt_type)

                if eval_on_close:
                    opt_test_low = opt_price_close
                    opt_test_high = opt_price_close
                else:
                    if is_call:
                        opt_test_high = black_scholes_price(spot_high, active_pos.strike, tte_years, daily_iv, 0.045, opt_type)
                        opt_test_low = black_scholes_price(spot_low, active_pos.strike, tte_years, daily_iv, 0.045, opt_type)
                    else:
                        opt_test_high = black_scholes_price(spot_low, active_pos.strike, tte_years, daily_iv, 0.045, opt_type)
                        opt_test_low = black_scholes_price(spot_high, active_pos.strike, tte_years, daily_iv, 0.045, opt_type)

                # Mandatory flat by 15:50
                if time_str >= "15:50":
                    exit_price = max(0.01, opt_price_close - slippage)
                    pnl = (exit_price - active_pos.entry_opt_price) * 100.0 * active_pos.current_qty
                    active_pos.realized_pnl_usd += pnl
                    active_pos.exit_time = bar_time
                    active_pos.exit_opt_price = exit_price
                    active_pos.exit_reason = "MANDATORY_FLAT"
                    daily_realized_pnl += pnl
                    all_trades.append(active_pos)
                    active_pos = None

                # Check Stop Loss
                elif opt_test_low <= active_pos.stop_loss_price:
                    exit_price = max(0.01, active_pos.stop_loss_price - slippage) if not eval_on_close else max(0.01, opt_price_close - slippage)
                    pnl = (exit_price - active_pos.entry_opt_price) * 100.0 * active_pos.current_qty
                    active_pos.realized_pnl_usd += pnl
                    active_pos.exit_time = bar_time
                    active_pos.exit_opt_price = exit_price
                    active_pos.exit_reason = "BREAKEVEN_STOP" if active_pos.tp1_hit else "STOP_LOSS"
                    daily_realized_pnl += pnl
                    all_trades.append(active_pos)
                    active_pos = None

                # Check Target 2 (+50%) if TP1 already hit
                elif active_pos.tp1_hit and opt_test_high >= active_pos.tp2_price:
                    exit_price = active_pos.tp2_price - slippage if not eval_on_close else opt_price_close - slippage
                    pnl = (exit_price - active_pos.entry_opt_price) * 100.0 * active_pos.current_qty
                    active_pos.realized_pnl_usd += pnl
                    active_pos.exit_time = bar_time
                    active_pos.exit_opt_price = exit_price
                    active_pos.exit_reason = "TP1_AND_TP2"
                    daily_realized_pnl += pnl
                    all_trades.append(active_pos)
                    active_pos = None

                # Check Target 1 (+25%) first scale-out
                elif (not active_pos.tp1_hit) and opt_test_high >= active_pos.tp1_price:
                    active_pos.tp1_hit = True
                    qty_to_scale = max(1, active_pos.current_qty // 2)
                    exit_price = active_pos.tp1_price - slippage if not eval_on_close else opt_price_close - slippage
                    pnl = (exit_price - active_pos.entry_opt_price) * 100.0 * qty_to_scale
                    active_pos.realized_pnl_usd += pnl
                    daily_realized_pnl += pnl
                    active_pos.current_qty -= qty_to_scale
                    # Move stop on remaining contracts to Breakeven
                    active_pos.stop_loss_price = active_pos.entry_opt_price

            # -------------------------------------------------------------
            # 2. Daily Circuit Breaker Check
            # -------------------------------------------------------------
            if daily_realized_pnl <= -abs(max_daily_loss):
                daily_circuit_breaker = True

            # -------------------------------------------------------------
            # 3. Check Signal Generation for New Entry
            # -------------------------------------------------------------
            if active_pos is None and not daily_circuit_breaker and daily_trades_count < max_trades_per_day:
                vwap = float(bar["VWAP"])
                ema9 = float(bar["EMA9"])
                ema21 = float(bar["EMA21"])
                rsi = float(bar["RSI14"])
                vol = float(bar["Volume"])
                vol_avg = float(bar["Vol_Avg20"])

                action = None
                chosen_setup = None

                if mode == "trend-filtered":
                    # Trend-filtered Morning Window (09:45 to 11:15 EST)
                    if "09:45" <= time_str <= "11:15":
                        if is_bull_regime:
                            # In Bull Market: ONLY TAKE CALLS
                            if spot_close > orb_high and spot_close > vwap and rsi >= 52.0 and vol > 1.2 * vol_avg:
                                action = "CALL"
                                chosen_setup = "BullRegime_ORB_Call"
                            elif ema9 > ema21 and spot_close > ema9 and 54.0 <= rsi <= 68.0 and vol > 1.2 * vol_avg:
                                action = "CALL"
                                chosen_setup = "BullRegime_EMA_Momentum_Call"
                        else:
                            # In Bear Market: ONLY TAKE PUTS
                            if spot_close < orb_low and spot_close < vwap and rsi <= 48.0 and vol > 1.2 * vol_avg:
                                action = "PUT"
                                chosen_setup = "BearRegime_ORB_Put"
                            elif ema9 < ema21 and spot_close < ema9 and 32.0 <= rsi <= 46.0 and vol > 1.2 * vol_avg:
                                action = "PUT"
                                chosen_setup = "BearRegime_EMA_Momentum_Put"
                else:
                    # Standard Mode (Full day execution across all setups)
                    is_morning = ("09:35" <= time_str < "11:30")
                    is_afternoon = ("13:30" <= time_str < "15:45")

                    if is_morning or is_afternoon:
                        if spot_close > orb_high and rsi >= 52.0 and vol > 1.15 * vol_avg:
                            action = "CALL"
                            chosen_setup = "ORB_Breakout"
                        elif spot_close < orb_low and rsi <= 48.0 and vol > 1.15 * vol_avg:
                            action = "PUT"
                            chosen_setup = "ORB_Breakdown"
                        elif abs(spot_low - vwap) / vwap < 0.0008 and spot_close > ema9 and rsi >= 50.0:
                            action = "CALL"
                            chosen_setup = "VWAP_Bounce"
                        elif abs(spot_high - vwap) / vwap < 0.0008 and spot_close < ema9 and rsi <= 50.0:
                            action = "PUT"
                            chosen_setup = "VWAP_Rejection"
                        elif ema9 > ema21 and spot_close > ema9 and 52.0 <= rsi <= 70.0:
                            action = "CALL"
                            chosen_setup = "EMA_Momentum"
                        elif ema9 < ema21 and spot_close < ema9 and 30.0 <= rsi <= 48.0:
                            action = "PUT"
                            chosen_setup = "EMA_Momentum"

                if action:
                    strike = round(spot_close)
                    opt_type = "call" if action == "CALL" else "put"
                    theo_price = black_scholes_price(spot_close, strike, tte_years, daily_iv, 0.045, opt_type)
                    entry_price = round(theo_price + slippage, 2)

                    if entry_price >= 0.40:
                        dollar_risk_per_contract = entry_price * 100.0 * stop_loss_pct
                        qty = int(risk_budget / dollar_risk_per_contract) if dollar_risk_per_contract > 0 else 1
                        qty = max(1, min(10, qty))

                        active_pos = Position(
                            trade_id=trade_id_counter,
                            date_str=d_str,
                            entry_time=bar_time,
                            symbol=symbol,
                            action=action,
                            strike=strike,
                            entry_spot=spot_close,
                            entry_opt_price=entry_price,
                            quantity=qty,
                            stop_loss_pct=stop_loss_pct,
                            tp1_pct=tp1_pct,
                            tp2_pct=tp2_pct,
                            setup=chosen_setup,
                            iv=daily_iv
                        )
                        trade_id_counter += 1
                        daily_trades_count += 1

    return {
        "symbol": symbol,
        "mode": mode,
        "eval_on_close": eval_on_close,
        "trades": all_trades,
        "total_days": len(unique_dates),
        "start_date": unique_dates[0].strftime("%Y-%m-%d"),
        "end_date": unique_dates[-1].strftime("%Y-%m-%d")
    }


def analyze_performance(trades: list[Position], total_days: int) -> dict:
    """Calculate comprehensive performance metrics from closed trade records."""
    if not trades:
        return {"total_trades": 0}

    pnls = [t.realized_pnl_usd for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    be = [p for p in pnls if p == 0]

    total_pnl = sum(pnls)
    win_count = len(wins)
    loss_count = len(losses)
    total_trades = len(trades)
    win_rate = (win_count / total_trades) * 100.0 if total_trades > 0 else 0.0

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)

    avg_win = (gross_win / win_count) if win_count > 0 else 0.0
    avg_loss = (gross_loss / loss_count) if loss_count > 0 else 0.0
    win_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    expectancy = (total_pnl / total_trades) if total_trades > 0 else 0.0

    # Max Drawdown calculation
    equity_curve = np.cumsum(pnls)
    running_max = np.maximum.accumulate(equity_curve)
    drawdowns = running_max - equity_curve
    max_drawdown = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    # Exit reason breakdown
    reason_counts = {}
    for t in trades:
        reason_counts[t.exit_reason] = reason_counts.get(t.exit_reason, 0) + 1

    # Setup breakdown
    setup_stats = {}
    for t in trades:
        s = t.setup
        if s not in setup_stats:
            setup_stats[s] = {"trades": 0, "pnl": 0.0, "wins": 0}
        setup_stats[s]["trades"] += 1
        setup_stats[s]["pnl"] += t.realized_pnl_usd
        if t.realized_pnl_usd > 0:
            setup_stats[s]["wins"] += 1

    for s in setup_stats:
        tr = setup_stats[s]["trades"]
        setup_stats[s]["win_rate"] = (setup_stats[s]["wins"] / tr * 100.0) if tr > 0 else 0.0

    # Monthly breakdown
    monthly_stats = {}
    for t in trades:
        m = t.date_str[:7]
        if m not in monthly_stats:
            monthly_stats[m] = {"trades": 0, "pnl": 0.0, "wins": 0}
        monthly_stats[m]["trades"] += 1
        monthly_stats[m]["pnl"] += t.realized_pnl_usd
        if t.realized_pnl_usd > 0:
            monthly_stats[m]["wins"] += 1

    for m in monthly_stats:
        tr = monthly_stats[m]["trades"]
        monthly_stats[m]["win_rate"] = (monthly_stats[m]["wins"] / tr * 100.0) if tr > 0 else 0.0

    avg_bars = np.mean([t.holding_bars for t in trades])
    avg_minutes = avg_bars * 5.0

    return {
        "total_trades": total_trades,
        "total_days": total_days,
        "trades_per_day": round(total_trades / total_days, 2),
        "win_rate_pct": round(win_rate, 1),
        "total_realized_pnl": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 2),
        "wins": win_count,
        "losses": loss_count,
        "breakevens": len(be),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "win_loss_ratio": round(win_loss_ratio, 2),
        "expectancy_per_trade": round(expectancy, 2),
        "max_drawdown": round(max_drawdown, 2),
        "avg_holding_minutes": round(avg_minutes, 1),
        "reason_counts": reason_counts,
        "setup_stats": setup_stats,
        "monthly_stats": monthly_stats
    }


def display_results(results: dict):
    """Render formatted Rich tables and performance summaries."""
    trades = results["trades"]
    symbol = results["symbol"]
    start_date = results["start_date"]
    end_date = results["end_date"]
    total_days = results["total_days"]
    mode = results["mode"]
    eval_close = results["eval_on_close"]

    metrics = analyze_performance(trades, total_days)

    if metrics["total_trades"] == 0:
        console.print("[red]No trades were generated during the backtest period.[/red]")
        return

    banner = Text()
    banner.append(f"📊 0DTE HISTORICAL BACKTEST RESULTS: {symbol}\n", style="bold yellow")
    banner.append(f"Mode: {mode.upper()} | Eval: {'Candle Close' if eval_close else 'Intrabar Wick'}\n", style="bold cyan")
    banner.append(f"Period: {start_date} to {end_date} ({total_days} Trading Days ~ 3 Months)", style="italic dim")
    console.print(Panel(banner, border_style="yellow"))

    # 1. Executive Summary Table
    pnl_c = "green" if metrics["total_realized_pnl"] >= 0 else "red"
    summary_table = Table(title="Overall Executive Performance Summary", border_style="cyan")
    summary_table.add_column("Metric", style="bold")
    summary_table.add_column("Value", justify="right")

    summary_table.add_row("Total Trades", f"{metrics['total_trades']} ({metrics['trades_per_day']} trades/day)")
    summary_table.add_row("Win Rate", f"[bold green]{metrics['win_rate_pct']:.1f}%[/bold green] ({metrics['wins']}W / {metrics['losses']}L / {metrics['breakevens']}BE)")
    summary_table.add_row("Profit Factor", f"[bold]{metrics['profit_factor']:.2f}[/bold]")
    summary_table.add_row("Total Realized Net P&L", f"[bold {pnl_c}]${metrics['total_realized_pnl']:+,.2f}[/bold {pnl_c}]")
    summary_table.add_row("Average Expectancy / Trade", f"[bold {pnl_c}]${metrics['expectancy_per_trade']:+.2f}[/bold {pnl_c}]")
    summary_table.add_row("Average Winner / Loser", f"[green]${metrics['avg_win']:.2f}[/green] / [red]-${metrics['avg_loss']:.2f}[/red]")
    summary_table.add_row("Win / Loss Payoff Ratio", f"{metrics['win_loss_ratio']:.2f} : 1")
    summary_table.add_row("Maximum System Drawdown", f"[red]-${metrics['max_drawdown']:,.2f}[/red]")
    summary_table.add_row("Average Holding Duration", f"{metrics['avg_holding_minutes']:.1f} minutes")

    console.print(summary_table)

    # 2. Setup Breakdown Table
    setup_table = Table(title="Performance Breakdown by Setup Type", border_style="yellow")
    setup_table.add_column("Setup Name", style="bold")
    setup_table.add_column("Trades", justify="center")
    setup_table.add_column("Win Rate", justify="right")
    setup_table.add_column("Realized Net P&L", justify="right")

    for s_name, s_data in metrics["setup_stats"].items():
        s_color = "green" if s_data["pnl"] >= 0 else "red"
        setup_table.add_row(
            s_name,
            str(s_data["trades"]),
            f"{s_data['win_rate']:.1f}%",
            f"[{s_color}]${s_data['pnl']:+,.2f}[/{s_color}]"
        )
    console.print(setup_table)

    # 3. Monthly Breakdown Table
    month_table = Table(title="Monthly P&L Progression", border_style="green")
    month_table.add_column("Month", style="bold")
    month_table.add_column("Trades", justify="center")
    month_table.add_column("Win Rate", justify="right")
    month_table.add_column("Realized P&L", justify="right")

    for m_name, m_data in metrics["monthly_stats"].items():
        m_color = "green" if m_data["pnl"] >= 0 else "red"
        month_table.add_row(
            m_name,
            str(m_data["trades"]),
            f"{m_data['win_rate']:.1f}%",
            f"[{m_color}]${m_data['pnl']:+,.2f}[/{m_color}]"
        )
    console.print(month_table)

    # 4. Exit Reason Distribution
    exit_table = Table(title="Exit Reason Distribution", border_style="magenta")
    exit_table.add_column("Exit Trigger", style="bold")
    exit_table.add_column("Count", justify="center")
    exit_table.add_column("Share (%)", justify="right")

    tot = metrics["total_trades"]
    for r_name, r_cnt in metrics["reason_counts"].items():
        exit_table.add_row(
            r_name,
            str(r_cnt),
            f"{(r_cnt / tot * 100.0):.1f}%"
        )
    console.print(exit_table)

    # 5. Sample Trades Table
    sample_table = Table(title="Sample Trades (Last 10)", border_style="dim")
    sample_table.add_column("Date", style="dim")
    sample_table.add_column("Action", justify="center")
    sample_table.add_column("Setup", style="italic")
    sample_table.add_column("Entry Spot", justify="right")
    sample_table.add_column("Strike", justify="right")
    sample_table.add_column("Opt Entry", justify="right")
    sample_table.add_column("Opt Exit", justify="right")
    sample_table.add_column("P&L ($)", justify="right")
    sample_table.add_column("Exit Reason", justify="center")

    for t in trades[-10:]:
        t_color = "green" if t.realized_pnl_usd >= 0 else "red"
        sample_table.add_row(
            t.date_str,
            t.action,
            t.setup,
            f"${t.entry_spot:.2f}",
            f"${t.strike:.1f}",
            f"${t.entry_opt_price:.2f}",
            f"${t.exit_opt_price:.2f}" if t.exit_opt_price else "-",
            f"[{t_color}]${t.realized_pnl_usd:+,.2f}[/{t_color}]",
            t.exit_reason or "-"
        )
    console.print(sample_table)


def main():
    parser = argparse.ArgumentParser(description="0DTE Options Historical Backtest Engine")
    parser.add_argument("--symbol", default="SPY", help="Ticker symbol (SPY or QQQ)")
    parser.add_argument("--risk", type=float, default=300.0, help="Risk budget per trade in USD")
    parser.add_argument("--stop", type=float, default=0.20, help="Stop loss fraction (e.g. 0.20 for -20 percent)")
    parser.add_argument("--tp1", type=float, default=0.25, help="Target 1 fraction (e.g. 0.25 for +25 percent)")
    parser.add_argument("--tp2", type=float, default=0.50, help="Target 2 fraction (e.g. 0.50 for +50 percent)")
    parser.add_argument("--max-loss", type=float, default=600.0, help="Max daily loss limit circuit breaker")
    parser.add_argument("--max-trades", type=int, default=3, help="Max trades per day")
    parser.add_argument("--slippage", type=float, default=0.02, help="Slippage per contract in USD")
    parser.add_argument("--mode", choices=["trend-filtered", "standard"], default="trend-filtered", help="Backtest mode")
    parser.add_argument("--wick-eval", action="store_true", help="Evaluate stops/targets on intrabar wicks instead of candle close")

    args = parser.parse_args()

    results = run_backtest(
        symbol=args.symbol.upper(),
        risk_budget=args.risk,
        stop_loss_pct=args.stop,
        tp1_pct=args.tp1,
        tp2_pct=args.tp2,
        max_daily_loss=args.max_loss,
        max_trades_per_day=args.max_trades,
        slippage=args.slippage,
        mode=args.mode,
        eval_on_close=(not args.wick_eval)
    )

    display_results(results)


if __name__ == "__main__":
    main()
