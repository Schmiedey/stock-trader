"""
Intraday noise-band momentum (v4) — the only rule that survived the 6-year 1-minute study
(research/RESULTS.md, research/momentum.py). After Zarattini, Aziz & Barbon (2024).

Every 30 minutes (10:00 .. 15:30 ET) on the completed 5-minute bar:
  sigma(t) = average |price(t) / day open - 1| at this time of day over the last 14 sessions
  upper    = max(open, prev close) * (1 + sigma),  lower = min(open, prev close) * (1 - sigma)
  price > upper -> long (CALL);  price < lower -> short (PUT)
  exit when price is back under max(upper, VWAP) (long) / above min(lower, VWAP) (short), or at the close.

Decisions are made ONLY at the 30-minute marks: checking faster turned the edge negative in research.
compute_state() is pure; the rest is thin I/O + caching so the daemon can call it every few seconds.
"""
from __future__ import annotations

import pandas as pd

from .config import get_current_ny_time
from .market_data import get_intraday_data

DEFAULTS = {"lookback": 14, "min_history": 10, "band_mult": 1.0, "step_minutes": 30,
            "first_mark": "10:00", "last_entry_mark": "15:30", "use_vwap_stop": True}

_BARS: dict = {}    # (symbol, mark) -> state, so each mark costs one data fetch per symbol
_LAST: dict = {}    # symbol -> last computed state (for the dashboard between marks)


def compute_state(bars: pd.DataFrame, mark: pd.Timestamp, lookback: int = 14, band_mult: float = 1.0,
                  min_history: int = 10) -> dict | None:
    """Band state at `mark` (tz-aware NY time on a 5-minute boundary) from multi-day 5-minute bars.
    Returns None when the bar closing at `mark` or enough history is missing."""
    bar_start = mark - pd.Timedelta(minutes=5)
    hm = bar_start.strftime("%H:%M")
    days = bars.groupby(bars.index.date)
    today = mark.date()
    moves, prev_close, t_open, t_px, t_vwap = [], None, None, None, None
    for d, g in days:
        if d > today or g.index[0].strftime("%H:%M") != "09:30":
            continue
        at = g[g.index.strftime("%H:%M") == hm]
        if d == today:
            if at.empty:
                return None
            upto = g[g.index <= bar_start]
            t_open, t_px = float(g["Open"].iloc[0]), float(at["Close"].iloc[0])
            tp = (upto["High"] + upto["Low"] + upto["Close"]) / 3.0
            vol = float(upto["Volume"].sum())
            t_vwap = float((tp * upto["Volume"]).sum() / vol) if vol > 0 else t_px
        else:
            if not at.empty:
                moves.append(abs(float(at["Close"].iloc[0]) / float(g["Open"].iloc[0]) - 1.0))
            prev_close = float(g["Close"].iloc[-1])
    moves = moves[-lookback:]
    if t_px is None or prev_close is None or len(moves) < min_history:
        return None
    sigma = band_mult * sum(moves) / len(moves)
    upper = max(t_open, prev_close) * (1 + sigma)
    lower = min(t_open, prev_close) * (1 - sigma)
    side = 1 if t_px > upper else -1 if t_px < lower else 0
    return {"mark": mark.strftime("%Y-%m-%d %H:%M"), "price": round(t_px, 2), "upper": round(upper, 2),
            "lower": round(lower, 2), "vwap": round(t_vwap, 2), "sigma_bps": round(sigma * 1e4, 1),
            "day_open": round(t_open, 2), "prev_close": round(prev_close, 2), "side": side, "history_days": len(moves)}


def should_exit(action: str, st: dict, use_vwap_stop: bool = True) -> str | None:
    """Exit reason for an open CALL/PUT given the band state at a mark, else None."""
    if action == "CALL":
        stop = max(st["upper"], st["vwap"]) if use_vwap_stop else st["upper"]
        if st["price"] < stop:
            return f"MOMENTUM_EXIT (price {st['price']} back under {stop})"
    elif action == "PUT":
        stop = min(st["lower"], st["vwap"]) if use_vwap_stop else st["lower"]
        if st["price"] > stop:
            return f"MOMENTUM_EXIT (price {st['price']} back above {stop})"
    return None


def current_mark(now, P: dict) -> pd.Timestamp | None:
    """The decision mark we are inside (mark <= now < mark + 5 min), or None between marks."""
    now = pd.Timestamp(now)
    step = int(P["step_minutes"])
    floor = now.floor(f"{step}min")
    if now - floor >= pd.Timedelta(minutes=5) or now.weekday() >= 5:
        return None
    hm = floor.strftime("%H:%M")
    return floor if P["first_mark"] <= hm <= "15:55" else None


def state_at_mark(symbol: str, params: dict | None = None, now=None) -> dict | None:
    """Band state for the mark we are in right now (cached per mark). None between marks or if data is late."""
    P = {**DEFAULTS, **(params or {})}
    mark = current_mark(now or get_current_ny_time(), P)
    if mark is None:
        return None
    key = (symbol, mark)
    if key not in _BARS:
        bars = get_intraday_data(symbol=symbol, interval="5m", period="1mo")
        st = compute_state(bars, mark, P["lookback"], P["band_mult"], P["min_history"])
        if st is None:
            return None            # bar not published yet: retry on the next call
        _BARS.clear() if len(_BARS) > 50 else None
        _BARS[key] = st
        _LAST[symbol] = st
    return _BARS[key]


def momentum_signal(symbol: str, params: dict | None = None, now=None) -> dict:
    """Signal fields in the same shape the daemon expects from evaluate_scalp_signals()."""
    P = {**DEFAULTS, **(params or {})}
    out = {"signal": "WAIT", "conviction": "NONE", "primary_setup": "Momentum: waiting for 30-min mark",
           "active_triggers": [], "veto_reasons": [], "reason": "Between decision marks.",
           "momentum": _LAST.get(symbol)}
    if P.get("symbols") and symbol not in P["symbols"]:
        out.update(primary_setup="Momentum: symbol not traded", reason=f"{symbol} is watch-only (no out-of-sample edge).")
        return out
    try:
        st = state_at_mark(symbol, P, now)
    except Exception as e:
        out["reason"] = f"Momentum data error: {e}"
        return out
    if st is None:
        return out
    out["momentum"] = st
    hm = st["mark"][-5:]
    if st["side"] == 0:
        out.update(primary_setup="Momentum: inside noise band",
                   reason=f"{hm} price {st['price']} inside [{st['lower']}, {st['upper']}]")
        return out
    if hm > P["last_entry_mark"]:
        out.update(primary_setup="Momentum: too late for new entries", reason=f"{hm} is past {P['last_entry_mark']}")
        return out
    long = st["side"] > 0
    band = st["upper"] if long else st["lower"]
    stop = (max(band, st["vwap"]) if long else min(band, st["vwap"])) if P["use_vwap_stop"] else band
    setup = f"Momentum {'breakout above' if long else 'breakdown below'} noise band {band}"
    out.update(signal="CALL" if long else "PUT", conviction="HIGH", primary_setup=setup, active_triggers=[setup],
               underlying_stop=stop, underlying_target=round(st["price"] * (1 + (1 if long else -1) * st["sigma_bps"] / 1e4), 2),
               current_price=st["price"], timestamp=st["mark"],
               reason=f"{hm} price {st['price']} {'>' if long else '<'} band {band} (sigma {st['sigma_bps']}bp, {st['history_days']}d)")
    return out


def momentum_exit(symbol: str, action: str, params: dict | None = None, now=None) -> str | None:
    """Exit reason for an open position at the current mark, else None (also None between marks)."""
    P = {**DEFAULTS, **(params or {})}
    st = state_at_mark(symbol, P, now)
    return should_exit(action, st, P["use_vwap_stop"]) if st else None
