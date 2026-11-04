"""Risk management and daily circuit breaker rules for 0DTE scalping."""

from datetime import datetime
from .config import load_config, get_market_session_state, get_current_ny_time


def check_trading_permissions(
    daily_realized_pnl: float,
    daily_trades_count: int,
    config: dict | None = None,
    bypass_time_filter: bool = False
) -> tuple[bool, str]:
    """
    Validates whether a new 0DTE trade is allowed under risk controls:
    1. Circuit Breaker: Max daily loss limit.
    2. Overtrading Prevention: Max trades per day limit.
    3. Session Window: Market hours and Midday Chop avoidance.
    """
    if config is None:
        config = load_config()

    max_daily_loss = config.get("max_daily_loss_usd", 600.0)
    max_trades = config.get("max_trades_per_day", 4)

    # 1. Circuit Breaker Check
    if daily_realized_pnl <= -abs(max_daily_loss):
        return False, f"CIRCUIT BREAKER TRIGGERED: Daily loss (-${abs(daily_realized_pnl):.2f}) reached or exceeded limit (-${max_daily_loss:.2f}). Trading is locked for the rest of today."

    # 2. Max Trades Check
    if daily_trades_count >= max_trades:
        return False, f"DAILY TRADE LIMIT REACHED: Already executed {daily_trades_count} / {max_trades} trades today. Preserve capital and step away."

    # 3. Market Session Check
    if not bypass_time_filter:
        session = get_market_session_state(config)
        if not session["trading_allowed"]:
            return False, f"SESSION RESTRICTION [{session['session_phase']}]: {session['reason']}"

    return True, "Trade permitted under current risk parameters."
