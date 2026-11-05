"""Configuration loader and validator for 0DTE scalper."""

import json
from pathlib import Path
from datetime import datetime
import pytz

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"


def load_config() -> dict:
    """Load configuration from config.json with robust fallbacks."""
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Config file not found at {CONFIG_FILE}")

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    return config


def save_config(config: dict) -> None:
    """Save updated configuration to config.json."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def get_current_ny_time() -> datetime:
    """Return the current datetime in America/New_York timezone."""
    ny_tz = pytz.timezone("America/New_York")
    return datetime.now(ny_tz)


def get_market_session_state(config: dict | None = None) -> dict:
    """
    Evaluate current New York time against 0DTE operational windows.
    Returns:
        {
            "current_time_str": "HH:MM",
            "is_market_open": bool,
            "session_phase": "PRE_MARKET" | "OPENING_VOLATILITY" | "PRIME_MORNING" | "MIDDAY_CHOP" | "AFTERNOON_POWER" | "MANDATORY_FLAT" | "AFTER_HOURS",
            "trading_allowed": bool,
            "reason": str
        }
    """
    if config is None:
        config = load_config()

    hours = config.get("trading_hours", {})
    now = get_current_ny_time()
    current_time_str = now.strftime("%H:%M")
    weekday = now.weekday()  # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday

    if weekday >= 5:
        return {
            "current_time_str": current_time_str,
            "is_market_open": False,
            "session_phase": "WEEKEND",
            "trading_allowed": False,
            "reason": "Markets are closed on weekends."
        }

    m_open = hours.get("market_open", "09:30")
    m_close = hours.get("market_close", "16:00")
    prime_start = hours.get("prime_morning_start", "09:45")
    prime_end = hours.get("prime_morning_end", "11:30")
    chop_start = hours.get("midday_chop_start", "11:30")
    chop_end = hours.get("midday_chop_end", "13:30")
    pm_start = hours.get("afternoon_window_start", "13:30")
    pm_end = hours.get("afternoon_window_end", "15:30")
    mand_exit = hours.get("mandatory_exit_time", "15:50")

    if current_time_str < m_open:
        return {
            "current_time_str": current_time_str,
            "is_market_open": False,
            "session_phase": "PRE_MARKET",
            "trading_allowed": False,
            "reason": "Pre-market session. Regular market opens at 09:30 EST."
        }
    elif current_time_str < prime_start:
        return {
            "current_time_str": current_time_str,
            "is_market_open": True,
            "session_phase": "OPENING_VOLATILITY",
            "trading_allowed": False,
            "reason": "Opening 15-minute price discovery (09:30 - 09:45 EST). Wait for 15m range to establish."
        }
    elif prime_start <= current_time_str < prime_end:
        return {
            "current_time_str": current_time_str,
            "is_market_open": True,
            "session_phase": "PRIME_MORNING",
            "trading_allowed": True,
            "reason": "Prime morning momentum session. Ideal for 0DTE scalping."
        }
    elif chop_start <= current_time_str < chop_end:
        return {
            "current_time_str": current_time_str,
            "is_market_open": True,
            "session_phase": "MIDDAY_CHOP",
            "trading_allowed": False,
            "reason": "Midday chop zone (11:30 - 13:30). High theta decay and low volume. No new entries."
        }
    elif pm_start <= current_time_str < pm_end:
        return {
            "current_time_str": current_time_str,
            "is_market_open": True,
            "session_phase": "AFTERNOON_POWER",
            "trading_allowed": True,
            "reason": "Afternoon power hour session. Trend continuation / late day gamma scalping."
        }
    elif pm_end <= current_time_str < mand_exit:
        return {
            "current_time_str": current_time_str,
            "is_market_open": True,
            "session_phase": "MANDATORY_FLAT_PREP",
            "trading_allowed": False,
            "reason": "Pre-exit window. Prepare to close all active open positions."
        }
    elif mand_exit <= current_time_str < m_close:
        return {
            "current_time_str": current_time_str,
            "is_market_open": True,
            "session_phase": "MANDATORY_FLAT",
            "trading_allowed": False,
            "reason": "Mandatory liquidation window (15:50 - 16:00). All open positions must be flat."
        }
    else:
        return {
            "current_time_str": current_time_str,
            "is_market_open": False,
            "session_phase": "AFTER_HOURS",
            "trading_allowed": False,
            "reason": "Market is closed."
        }
