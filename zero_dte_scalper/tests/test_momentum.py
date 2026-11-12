"""Unit tests for the v4 noise-band momentum rule (pure functions, synthetic bars)."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from zero_dte_scalper.src.momentum import compute_state, should_exit, current_mark, momentum_signal, DEFAULTS
from zero_dte_scalper.src import momentum as M


def make_bars(n_days=16, drift_bps=10.0, today_move_bps=0.0, base=100.0):
    """Each past day drifts linearly to +drift_bps by the close; today drifts to today_move_bps."""
    rows, idx = [], []
    days = pd.bdate_range("2026-10-01", periods=n_days, tz="America/New_York")
    for i, d in enumerate(days):
        mv = today_move_bps if i == n_days - 1 else drift_bps * (1 if i % 2 else -1)
        for b in range(78):
            px = base * (1 + mv / 1e4 * (b + 1) / 78)
            o = base * (1 + mv / 1e4 * b / 78)
            rows.append((o, max(o, px), min(o, px), px, 1000)); idx.append(d + pd.Timedelta(hours=9, minutes=30 + 5 * b))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex(idx))


def mark_of(bars, hm):
    return pd.Timestamp(f"{bars.index[-1].date()} {hm}", tz="America/New_York")


def test_sigma_is_average_abs_move_at_time_of_day():
    bars = make_bars(drift_bps=10.0)
    st = compute_state(bars, mark_of(bars, "12:45"))          # bar 39 closes at 12:45 -> half the day
    assert st["history_days"] == 14
    assert abs(st["sigma_bps"] - 5.0) < 0.2                   # |10bp * 39/78|
    assert st["side"] == 0


def test_breakout_long_and_short():
    up = make_bars(today_move_bps=40.0)
    st = compute_state(up, mark_of(up, "12:45"))
    assert st["side"] == 1 and st["price"] > st["upper"]
    dn = make_bars(today_move_bps=-40.0)
    st = compute_state(dn, mark_of(dn, "12:45"))
    assert st["side"] == -1 and st["price"] < st["lower"]


def test_band_anchored_to_prev_close_on_gaps():
    bars = make_bars(today_move_bps=8.0)
    last_day = bars.index.date == bars.index[-1].date()
    bars.loc[last_day, ["Open", "High", "Low", "Close"]] *= 0.99      # gap down 1%: upper band stays at prev close
    st = compute_state(bars, mark_of(bars, "12:45"))
    assert st["upper"] > st["prev_close"] > st["day_open"] and st["side"] == 0


def test_missing_bar_or_history_returns_none():
    bars = make_bars()
    assert compute_state(bars.iloc[:-60], mark_of(bars, "15:30")) is None
    assert compute_state(make_bars(n_days=5), mark_of(make_bars(n_days=5), "12:45")) is None


def test_exit_rules():
    st = {"price": 100.0, "upper": 100.5, "lower": 99.0, "vwap": 99.8}
    assert should_exit("CALL", st) and should_exit("PUT", st)          # inside band: both sides flat
    assert should_exit("CALL", {**st, "price": 101.0}) is None
    assert should_exit("CALL", {**st, "price": 101.0, "vwap": 101.5})  # VWAP trails above the band
    assert should_exit("CALL", {**st, "price": 101.0, "vwap": 101.5}, use_vwap_stop=False) is None
    assert should_exit("PUT", {**st, "price": 98.5}) is None


def test_marks_only_every_30_minutes():
    t = lambda s: pd.Timestamp(f"2026-11-12 {s}", tz="America/New_York")
    assert current_mark(t("10:00:20"), DEFAULTS) == t("10:00:00")
    assert current_mark(t("10:04:59"), DEFAULTS) == t("10:00:00")
    assert current_mark(t("10:05:00"), DEFAULTS) is None
    assert current_mark(t("09:45:10"), DEFAULTS) is None
    assert current_mark(t("09:30:10"), DEFAULTS) is None
    assert current_mark(pd.Timestamp("2026-11-14 10:00:10", tz="America/New_York"), DEFAULTS) is None


def test_signal_shape_and_late_entry_block(monkeypatch):
    up = make_bars(today_move_bps=40.0)
    monkeypatch.setattr(M, "get_intraday_data", lambda **k: up)
    M._BARS.clear()
    s = momentum_signal("QQQ", now=mark_of(up, "12:45") + pd.Timedelta(minutes=15, seconds=10))   # 13:00 mark
    assert s["signal"] == "CALL" and s["conviction"] == "HIGH" and s["underlying_stop"] > 0 and s["timestamp"].endswith("13:00")
    s = momentum_signal("QQQ", now=mark_of(up, "13:10"))
    assert s["signal"] == "WAIT"
    M._BARS.clear()
    s = momentum_signal("QQQ", {"last_entry_mark": "12:30"}, now=mark_of(up, "13:00") + pd.Timedelta(seconds=5))
    assert s["signal"] == "WAIT"


def test_watch_only_symbols_never_signal(monkeypatch):
    up = make_bars(today_move_bps=40.0)
    monkeypatch.setattr(M, "get_intraday_data", lambda **k: up)
    M._BARS.clear()
    now = mark_of(up, "13:00") + pd.Timedelta(seconds=5)
    assert momentum_signal("SPY", {"symbols": ["QQQ"]}, now=now)["signal"] == "WAIT"
    assert momentum_signal("QQQ", {"symbols": ["QQQ"]}, now=now)["signal"] == "CALL"
