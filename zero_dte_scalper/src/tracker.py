"""Portfolio tracker, real-time position monitor, and trade history ledger."""

import json
import uuid
import time
from pathlib import Path
from datetime import datetime
import pytz
import yfinance as yf

from .greeks import black_scholes_price
from .market_data import calculate_time_to_expiration_years

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ACTIVE_POSITIONS_FILE = DATA_DIR / "active_positions.json"
TRADE_HISTORY_FILE = DATA_DIR / "trade_history.json"


def _ensure_data_files():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ACTIVE_POSITIONS_FILE.exists():
        with open(ACTIVE_POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)
    if not TRADE_HISTORY_FILE.exists():
        with open(TRADE_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)


def get_current_timestamp_ny() -> str:
    ny_tz = pytz.timezone("America/New_York")
    return datetime.now(ny_tz).strftime("%Y-%m-%d %H:%M:%S EST")


def get_today_ny_date() -> str:
    ny_tz = pytz.timezone("America/New_York")
    return datetime.now(ny_tz).strftime("%Y-%m-%d")


def load_active_positions() -> list[dict]:
    _ensure_data_files()
    try:
        with open(ACTIVE_POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_active_positions(positions: list[dict]) -> None:
    _ensure_data_files()
    with open(ACTIVE_POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, indent=2)


def load_trade_history() -> list[dict]:
    _ensure_data_files()
    try:
        with open(TRADE_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_trade_history(history: list[dict]) -> None:
    _ensure_data_files()
    with open(TRADE_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def open_position(
    symbol: str,
    action: str,
    contract_symbol: str,
    strike: float,
    expiration: str,
    entry_price: float,
    quantity: int,
    stop_loss_price: float,
    tp1_price: float,
    tp2_price: float,
    setup_name: str = "Manual Scalp",
    notes: str = "",
    entry_spot_price: float = 0.0,
    iv: float = 0.16
) -> dict:
    """Create and record a new open position with complete risk parameters."""
    positions = load_active_positions()
    pos_id = str(uuid.uuid4())[:8]
    timestamp = get_current_timestamp_ny()

    position = {
        "id": pos_id,
        "symbol": symbol,
        "action": action.upper(),
        "contract_symbol": contract_symbol,
        "strike": strike,
        "expiration": expiration,
        "entry_timestamp": timestamp,
        "entry_epoch": time.time(),
        "entry_date": get_today_ny_date(),
        "entry_price": round(entry_price, 2),
        "entry_spot_price": round(entry_spot_price, 2) if entry_spot_price > 0 else 0.0,
        "current_spot_price": round(entry_spot_price, 2) if entry_spot_price > 0 else 0.0,
        "iv": iv,
        "initial_quantity": quantity,
        "current_quantity": quantity,
        "stop_loss_price": round(stop_loss_price, 2),
        "tp1_price": round(tp1_price, 2),
        "tp2_price": round(tp2_price, 2),
        "tp1_hit": False,
        "current_price": round(entry_price, 2),
        "unrealized_pnl_usd": 0.0,
        "unrealized_pnl_pct": 0.0,
        "elapsed_minutes": 0.0,
        "status": "OPEN",
        "setup": setup_name,
        "notes": notes
    }

    positions.append(position)
    save_active_positions(positions)
    return position


def close_position(
    position_id: str,
    exit_price: float,
    reason: str = "MANUAL_CLOSE",
    partial_qty: int | None = None
) -> dict:
    """Close an open position (full or partial) and record to trade history."""
    positions = load_active_positions()
    history = load_trade_history()

    pos_index = -1
    for i, p in enumerate(positions):
        if p["id"] == position_id:
            pos_index = i
            break

    if pos_index == -1:
        raise ValueError(f"Position with ID {position_id} not found in active positions.")

    pos = positions[pos_index]
    qty_to_close = pos["current_quantity"] if (partial_qty is None or partial_qty >= pos["current_quantity"]) else partial_qty
    remaining_qty = pos["current_quantity"] - qty_to_close

    realized_pnl_usd = round((exit_price - pos["entry_price"]) * 100.0 * qty_to_close, 2)
    realized_pnl_pct = round(((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100.0, 2)
    timestamp = get_current_timestamp_ny()

    trade_record = {
        "id": pos["id"],
        "symbol": pos["symbol"],
        "action": pos["action"],
        "contract_symbol": pos["contract_symbol"],
        "strike": pos["strike"],
        "expiration": pos["expiration"],
        "entry_timestamp": pos["entry_timestamp"],
        "exit_timestamp": timestamp,
        "entry_date": pos.get("entry_date", get_today_ny_date()),
        "entry_price": pos["entry_price"],
        "exit_price": round(exit_price, 2),
        "quantity_closed": qty_to_close,
        "realized_pnl_usd": realized_pnl_usd,
        "realized_pnl_pct": realized_pnl_pct,
        "exit_reason": reason,
        "setup": pos.get("setup", "Scalp"),
        "is_partial": (remaining_qty > 0)
    }

    history.append(trade_record)
    save_trade_history(history)

    if remaining_qty > 0:
        pos["current_quantity"] = remaining_qty
        save_active_positions(positions)
    else:
        positions.pop(pos_index)
        save_active_positions(positions)

    return trade_record


def update_active_positions_quotes() -> list[dict]:
    """
    Fetch current quotes for active positions and calculate unrealized P&L.
    Uses robust real-time Black-Scholes valuation tied to live underlying spot price
    with sanity filtering against erratic/stale options ticker prints.
    """
    positions = load_active_positions()
    if not positions:
        return []

    updated = False
    now_epoch = time.time()

    for pos in positions:
        symbol = pos["symbol"]
        contract_sym = pos["contract_symbol"]
        strike = pos["strike"]
        action = pos.get("action", "CALL").upper()
        opt_type = "call" if action == "CALL" else "put"
        expiration = pos.get("expiration", get_today_ny_date())
        iv = pos.get("iv", 0.16)

        # 1. Fetch live spot price of underlying (streaming real-time on yfinance)
        spot_px = 0.0
        try:
            spot_ticker = yf.Ticker(symbol)
            spot_px = float(spot_ticker.fast_info.last_price or 0.0)
            if spot_px <= 0:
                hist = spot_ticker.history(period="1d", interval="1m")
                if not hist.empty:
                    spot_px = float(hist["Close"].iloc[-1])
        except Exception:
            pass

        if spot_px <= 0:
            spot_px = pos.get("current_spot_price") or pos.get("entry_spot_price") or (742.0 if symbol == "SPY" else 654.0)

        pos["current_spot_price"] = round(spot_px, 2)

        # 2. Compute theoretical Black-Scholes fair value
        tte_years = calculate_time_to_expiration_years(expiration)
        bs_price = black_scholes_price(
            spot=spot_px,
            strike=strike,
            tte_years=tte_years,
            volatility=iv,
            option_type=opt_type
        )

        # Intrinsic floor
        if opt_type == "call":
            intrinsic = max(0.0, spot_px - strike)
        else:
            intrinsic = max(0.0, strike - spot_px)

        # 3. Check live options quote first (with Anti-Spike Sanity Gate)
        live_val = None
        try:
            c_ticker = yf.Ticker(contract_sym)
            px = float(c_ticker.fast_info.last_price or 0.0)
            if px <= 0:
                h = c_ticker.history(period="1d", interval="1m")
                if not h.empty:
                    px = float(h["Close"].iloc[-1])
            if px > 0 and px >= intrinsic:
                live_val = px
        except Exception:
            pass

        # Model-anchored sanity gate: the option should move roughly as Black-Scholes
        # predicts from the underlying's move since entry. yfinance option prints are
        # often stale or phantom (e.g. -74% in the same second as entry while spot is
        # flat), which triggered fake stops and fake targets. Reject prints that
        # disagree with the model by more than 25% (and more than $0.05).
        if not pos.get("entry_spot_price"):
            pos["entry_spot_price"] = round(spot_px, 2)
        entry_spot = pos["entry_spot_price"]
        model_px = pos["entry_price"]
        try:
            bs_entry = black_scholes_price(spot=entry_spot, strike=strike, tte_years=tte_years,
                                           volatility=iv, option_type=opt_type)
            if bs_entry > 0.01:
                model_px = pos["entry_price"] * (bs_price / bs_entry)
        except Exception:
            pass
        pos["model_price"] = round(model_px, 2)

        if live_val is not None:
            gap = abs(live_val - model_px)
            if model_px > 0 and gap / model_px > 0.25 and gap > 0.05:
                final_price = round(model_px, 2)
                pos["quote_source"] = "MODEL (rejected print %.2f)" % live_val
            else:
                final_price = round(live_val, 2)
                pos["quote_source"] = "LIVE"
        else:
            final_price = round(model_px, 2)
            pos["quote_source"] = "MODEL"

        # Never permit quotes below intrinsic value
        final_price = max(final_price, round(intrinsic, 2))
        final_price = max(0.01, final_price)

        pos["current_price"] = final_price
        pos["unrealized_pnl_usd"] = round((final_price - pos["entry_price"]) * 100.0 * pos["current_quantity"], 2)
        pos["unrealized_pnl_pct"] = round(((final_price - pos["entry_price"]) / pos["entry_price"]) * 100.0, 2)

        entry_epoch = pos.get("entry_epoch", now_epoch)
        pos["elapsed_minutes"] = round((now_epoch - entry_epoch) / 60.0, 1)
        updated = True

    if updated:
        save_active_positions(positions)

    return positions


def get_today_stats() -> dict:
    """Return realized P&L, unique position count, and win/loss breakdown for today."""
    today = get_today_ny_date()
    history = load_trade_history()
    today_trades = [t for t in history if t.get("entry_date") == today]

    realized_pnl = sum(t.get("realized_pnl_usd", 0.0) for t in today_trades)
    unique_positions = {t["id"] for t in today_trades if "id" in t}
    pos_count = len(unique_positions) if unique_positions else len(today_trades)

    return {
        "today_date": today,
        "trades_count": pos_count,
        "execution_events_count": len(today_trades),
        "realized_pnl_usd": round(realized_pnl, 2),
        "wins": len([t for t in today_trades if t.get("realized_pnl_usd", 0) > 0]),
        "losses": len([t for t in today_trades if t.get("realized_pnl_usd", 0) < 0])
    }


def calculate_performance_metrics() -> dict:
    """Compute all-time trading statistics."""
    history = load_trade_history()
    if not history:
        return {
            "total_trades": 0,
            "win_rate_pct": 0.0,
            "total_realized_pnl_usd": 0.0,
            "profit_factor": 0.0,
            "wins": 0,
            "losses": 0,
            "avg_win_usd": 0.0,
            "avg_loss_usd": 0.0,
            "max_win_usd": 0.0,
            "max_loss_usd": 0.0
        }

    wins = [t["realized_pnl_usd"] for t in history if t["realized_pnl_usd"] > 0]
    losses = [abs(t["realized_pnl_usd"]) for t in history if t["realized_pnl_usd"] < 0]

    total_trades = len(history)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades) * 100.0 if total_trades > 0 else 0.0

    total_pnl = sum(t["realized_pnl_usd"] for t in history)
    gross_win = sum(wins)
    gross_loss = sum(losses)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)

    avg_win = (gross_win / win_count) if win_count > 0 else 0.0
    avg_loss = (gross_loss / loss_count) if loss_count > 0 else 0.0
    max_win = max(wins) if wins else 0.0
    max_loss = max(losses) if losses else 0.0

    return {
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate, 1),
        "total_realized_pnl_usd": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 2),
        "wins": win_count,
        "losses": loss_count,
        "avg_win_usd": round(avg_win, 2),
        "avg_loss_usd": round(avg_loss, 2),
        "max_win_usd": round(max_win, 2),
        "max_loss_usd": round(max_loss, 2)
    }
