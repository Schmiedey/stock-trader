#!/usr/bin/env python3
"""
Replay backtester that runs the LIVE engine over history.

Same code as the daemon:
  - signals:  src/signals.py  score_bars()          (what evaluate_scalp_signals runs)
  - levels:   src/market_data.py compute_levels()  (what get_key_levels runs)
  - sizing:   src/options_selector.py select_best_contract()
  - exits:    src/exits.py decide_exit()             (what runner.manage_active_positions runs)
  - session:  src/config.py get_market_session_state(now=...)
  - limits:   max concurrent, trades/day, daily loss, re-entry cooldowns

What is simulated (and how it is kept conservative):
  - Signals are scored on completed 5m bars (live scores the forming bar every ~10s).
  - Options are priced with Black-Scholes off the previous day's VIX (SPY) / VXN (QQQ).
    Entry pays the ask, marks and exits use the bid (spread = max($0.02, 3% of mid)).
  - Inside each later bar the path is Open -> adverse extreme -> favorable extreme -> Close,
    so stops are tested before targets. Stops confirm on one check (live needs two).

Usage:
  python research/replay.py                       # SPY+QQQ, live config, full cache
  python research/replay.py --symbols SPY --out results/spy.json
"""
from __future__ import annotations

import sys
import json
import math
import hashlib
import argparse
from pathlib import Path
from datetime import timedelta

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[2]
SCALPER = ROOT / "zero_dte_scalper"
sys.path.insert(0, str(ROOT))

from zero_dte_scalper.src.config import load_config, get_market_session_state
from zero_dte_scalper.src.market_data import calculate_indicators, compute_levels
from zero_dte_scalper.src.signals import score_bars, DEFAULT_PARAMS
from zero_dte_scalper.src.options_selector import select_best_contract
from zero_dte_scalper.src.exits import decide_exit
from zero_dte_scalper.src.greeks import black_scholes_price, calculate_greeks

CACHE = SCALPER / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
RESULTS = SCALPER / "research" / "results"
RESULTS.mkdir(parents=True, exist_ok=True)
NY = "America/New_York"
VOL_INDEX = {"SPY": "^VIX", "QQQ": "^VXN"}


# --------------------------------------------------------------------------- data
def _to_ny(df: pd.DataFrame) -> pd.DataFrame:
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(NY)
    return df


def load_bars(symbol: str, refresh: bool = True) -> pd.DataFrame:
    """5m RTH bars, cached on disk and merged with each fetch so history grows past 60 days."""
    path = CACHE / f"{symbol}_5m.pkl"
    old = pd.read_pickle(path) if path.exists() else None
    if refresh or old is None:
        try:
            new = _to_ny(yf.Ticker(symbol).history(period="60d", interval="5m", prepost=False))
            new = new[["Open", "High", "Low", "Close", "Volume"]]
            df = new if old is None else pd.concat([old, new])
            df = df[~df.index.duplicated(keep="last")].sort_index()
            df.to_pickle(path)
            old = df
        except Exception as e:
            print(f"[warn] {symbol} fetch failed ({e}); using cache")
    df = old.between_time("09:30", "15:55")
    # Drop the current (possibly incomplete) session
    today = pd.Timestamp.now(tz=NY).date()
    return df[df.index.date < today]


def load_daily(symbol: str, refresh: bool = True) -> pd.DataFrame:
    path = CACHE / f"{symbol.replace('^', '')}_1d.pkl"
    if refresh or not path.exists():
        try:
            d = yf.Ticker(symbol).history(period="2y", interval="1d")[["Open", "High", "Low", "Close"]]
            d.index = pd.to_datetime(d.index.date)
            d.to_pickle(path)
        except Exception as e:
            print(f"[warn] daily {symbol} fetch failed ({e}); using cache")
    return pd.read_pickle(path)


# --------------------------------------------------------------------------- signals
def precompute_signals(symbol: str, bars: pd.DataFrame, daily: pd.DataFrame, params: dict) -> dict:
    """
    Score every completed bar with the live engine. Returns {bar_close_time: signal_dict}.
    Indicators are computed on a 2-day window (previous + current session), like the live
    daemon's period="2d" fetch; all indicators are causal so truncation is exact.
    """
    days = sorted(set(bars.index.date))
    out = {}
    for i in range(1, len(days)):
        prev_d, d = days[i - 1], days[i]
        win = bars[(bars.index.date == prev_d) | (bars.index.date == d)]
        if len(win) < 30:
            continue
        ind = calculate_indicators(win)
        prev_day = daily[daily.index.date < d]
        if prev_day.empty:
            continue
        pdh, pdl, pdc = (float(prev_day.iloc[-1][k]) for k in ("High", "Low", "Close"))
        today_idx = np.where(ind.index.date == d)[0]
        for j in today_idx:
            sub = ind.iloc[: j + 1]
            bar_close = sub.index[-1] + timedelta(minutes=5)
            levels = compute_levels(sub, pdh, pdl, pdc, as_of=bar_close.to_pydatetime(), symbol=symbol)
            sig = score_bars(sub, levels, symbol=symbol, params=params)
            out[bar_close] = {k: sig[k] for k in ("signal", "conviction", "primary_setup", "current_price",
                                                  "underlying_stop", "rsi", "rel_vol", "atr", "vwap")}
    return out


def signal_cache(symbol: str, bars: pd.DataFrame, daily: pd.DataFrame, params: dict) -> dict:
    key = json.dumps({**DEFAULT_PARAMS, **params}, sort_keys=True, default=list)
    tag = hashlib.md5(f"{key}|{len(bars)}|{bars.index[-1]}".encode()).hexdigest()[:12]
    path = CACHE / f"sig_{symbol}_{tag}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    sigs = precompute_signals(symbol, bars, daily, params)
    pd.to_pickle(sigs, path)
    return sigs


# --------------------------------------------------------------------------- options
def tte_years(t: pd.Timestamp) -> float:
    close = t.replace(hour=16, minute=0, second=0)
    return max(60.0, (close - t).total_seconds()) / (365.25 * 86400.0)


def spread_for(mid: float) -> float:
    return max(0.02, round(0.03 * mid, 2))


def option_mid(spot, strike, t, iv, kind):
    return max(0.01, black_scholes_price(spot, strike, tte_years(t), iv, option_type=kind))


def synthetic_chain(symbol: str, spot: float, t: pd.Timestamp, iv: float) -> dict:
    calls, puts = [], []
    tte = tte_years(t)
    for k in range(int(round(spot)) - 8, int(round(spot)) + 9):
        for kind, bucket in (("call", calls), ("put", puts)):
            mid = option_mid(spot, k, t, iv, kind)
            g = calculate_greeks(spot, float(k), tte, iv, option_type=kind)
            spr = spread_for(mid)
            bucket.append({
                "contract_symbol": f"{symbol}{t:%y%m%d}{kind[0].upper()}{k*1000:08d}",
                "strike": float(k), "bid": round(max(0.01, mid - spr / 2), 2), "ask": round(mid + spr / 2, 2),
                "mid": round(mid, 2), "last_price": round(mid, 2), "spread": spr,
                "volume": 100000, "open_interest": 5000, "iv": iv, "delta": g["delta"],
            })
    return {"symbol": symbol, "spot_price": spot, "expiration": t.strftime("%Y-%m-%d"),
            "is_true_0dte": True, "calls": calls, "puts": puts}


# --------------------------------------------------------------------------- replay
def run(symbols=("SPY", "QQQ"), config_overrides: dict | None = None, signal_params: dict | None = None,
        filters: dict | None = None, start=None, end=None, refresh=False, data=None,
        sigs_override: dict | None = None) -> dict:
    """
    filters (research-only gates applied on top of the live signal):
      regime: None | "trend"   -> calls only when prior daily close > 20d EMA, puts only when below
      vix_min / vix_max: float -> only trade when prior-day vol index is in range
      windows: list[("HH:MM","HH:MM")] -> override allowed entry windows (bar close time)
    """
    cfg = {**load_config(), **(config_overrides or {})}
    params = {**cfg.get("signal_params", {}), **(signal_params or {})}
    filters = filters or {}
    slip = cfg.get("replay_slippage_usd", 0.01)

    data = data or load_data(symbols, refresh)
    sigs = sigs_override or {s: signal_cache(s, data[s]["bars"], data[s]["daily"], params) for s in symbols}

    # merged timeline of bar-close times
    # every completed bar (not just signal bars) so open positions are managed continuously
    sig_days = {t.date() for s in symbols for t in sigs[s]}
    timeline = sorted({ix + timedelta(minutes=5) for s in symbols for ix in data[s]["bars"].index
                       if ix.date() in sig_days})
    if start:
        timeline = [t for t in timeline if t.date() >= pd.Timestamp(start).date()]
    if end:
        timeline = [t for t in timeline if t.date() <= pd.Timestamp(end).date()]

    bar_lookup = {s: data[s]["bars"] for s in symbols}
    trades, pos = [], None
    day, day_pnl, day_trades, last_exit = None, 0.0, 0, {}

    def ctx(sym, t):
        prev = data[sym]["vol"][data[sym]["vol"].index.date < t.date()]
        iv = float(prev["Close"].iloc[-1]) / 100.0 if len(prev) else 0.18
        iv *= cfg.get("replay_iv_mult", 1.0)
        dl = data[sym]["daily"]
        dprev = dl[dl.index.date < t.date()]["Close"]
        ema20 = dprev.ewm(span=20, adjust=False).mean()
        trend_up = bool(len(dprev) > 20 and dprev.iloc[-1] > ema20.iloc[-1])
        return iv, trend_up

    def mark(p, spot, t):
        mid = option_mid(spot, p["strike"], t, p["iv"], p["kind"])
        return max(0.01, round(mid - spread_for(mid) / 2, 2))

    def close(p, qty, px, reason, t):
        nonlocal day_pnl
        pnl = (px - slip - p["entry_price"]) * 100.0 * qty
        p["realized"] += pnl
        p["current_quantity"] -= qty
        day_pnl += pnl
        p["fills"].append({"t": str(t), "qty": qty, "px": round(px - slip, 2), "reason": reason})
        if p["current_quantity"] <= 0:
            trades.append({
                "symbol": p["symbol"], "action": p["action"], "setup": p["setup"],
                "entry_time": str(p["entry_t"]), "exit_time": str(t), "date": str(p["entry_t"].date()),
                "hour": p["entry_t"].hour, "weekday": p["entry_t"].weekday(),
                "entry_price": p["entry_price"], "qty": p["initial_quantity"], "strike": p["strike"],
                "pnl": round(p["realized"], 2), "pnl_pct_of_risk": round(p["realized"] / max(1.0, p["risk_usd"]), 3),
                "exit_reason": reason, "hold_min": round((t - p["entry_t"]).total_seconds() / 60, 1),
                "iv": p["iv"], "fills": p["fills"],
            })
            return True
        return False

    for t in timeline:
        if day != t.date():
            day, day_pnl, day_trades, last_exit = t.date(), 0.0, 0, {}
            if pos is not None:  # never carry overnight (shouldn't happen)
                pos = None
        session = get_market_session_state(cfg, now=t.to_pydatetime())
        phase = session["session_phase"]

        # ---- manage open position through this bar's path
        if pos is not None:
            bars = bar_lookup[pos["symbol"]]
            bt = t - timedelta(minutes=5)
            if bt in bars.index:
                b = bars.loc[bt]
                adverse, favor = (b["Low"], b["High"]) if pos["action"] == "CALL" else (b["High"], b["Low"])
                path = [(b["Open"], bt + timedelta(minutes=1)), (adverse, bt + timedelta(minutes=2)),
                        (favor, bt + timedelta(minutes=3)), (b["Close"], t)]
                for spot, pt in path:
                    pos["current_price"] = mark(pos, float(spot), pt)
                    pos["current_spot_price"] = float(spot)
                    pos["unrealized_pnl_pct"] = (pos["current_price"] - pos["entry_price"]) / pos["entry_price"] * 100
                    pos["elapsed_minutes"] = (pt - pos["entry_t"]).total_seconds() / 60.0
                    ph = phase if pt == t else get_market_session_state(cfg, now=pt.to_pydatetime())["session_phase"]
                    d = decide_exit(pos, {**cfg, "stop_confirm_checks": 1}, ph)
                    pos.update(d["updates"])
                    a = d["action"]
                    if a:
                        # fill at the trigger level (stop/target), not the extreme of the bar
                        px = pos["current_price"]
                        if a["tag"] == "STOP_LOSS":
                            # gap through the stop at the open fills at the open; otherwise at the stop
                            px = px if pt == path[0][1] else pos["stop_loss_price"]
                        elif a["tag"] == "TARGET_2":
                            px = pos["tp2_price"]
                        elif a["tag"] == "TARGET_1":
                            px = pos["tp1_price"]
                        done = close(pos, a["qty"], px, a["reason"], pt)
                        if done:
                            last_exit[pos["symbol"]] = (pt, a["tag"])
                            pos = None
                            break

        # ---- entries
        if pos is not None or not session["trading_allowed"]:
            continue
        if day_pnl <= -abs(cfg["max_daily_loss_usd"]) or day_trades >= cfg["max_trades_per_day"]:
            continue
        if filters.get("windows"):
            hm = t.strftime("%H:%M")
            if not any(a <= hm <= b for a, b in filters["windows"]):
                continue

        for sym in symbols:
            sig = sigs[sym].get(t)
            if not sig or sig["signal"] not in ("CALL", "PUT"):
                continue
            if sym in last_exit:
                lt, tag = last_exit[sym]
                cool = cfg.get("reentry_cooldown_seconds", 300) if tag in ("STOP_LOSS", "INVALIDATION") else 180
                if (t - lt).total_seconds() < cool:
                    continue
            iv, trend_up = ctx(sym, t)
            if filters.get("regime") == "trend" and ((sig["signal"] == "CALL") != trend_up):
                continue
            vix = iv * 100
            if not (filters.get("vix_min", 0) <= vix <= filters.get("vix_max", 999)):
                continue

            spot = float(sig["current_price"])
            chain = synthetic_chain(sym, spot, t, iv)
            rec = select_best_contract(symbol=sym, action=sig["signal"], chain_data=chain, config=cfg)
            if not rec or not rec.get("contract"):
                continue
            c = rec["contract"]
            if c["spread"] > cfg.get("max_bid_ask_spread_usd", 0.04) * 2.5:
                continue
            entry = round(rec["entry_price"] + slip, 2)
            qty = max(1, rec["sizing"]["suggested_contracts"])
            tg = rec["targets"]
            pos = {
                "symbol": sym, "action": sig["signal"], "kind": "call" if sig["signal"] == "CALL" else "put",
                "setup": sig["primary_setup"], "strike": c["strike"], "iv": iv, "entry_t": t,
                "entry_price": entry, "initial_quantity": qty, "current_quantity": qty,
                "stop_loss_price": round(entry * (1 - cfg["stop_loss_pct"]), 2),
                "tp1_price": round(entry * (1 + cfg["take_profit_1_pct"]), 2),
                "tp2_price": round(entry * (1 + cfg["take_profit_2_pct"]), 2),
                "underlying_stop": sig["underlying_stop"], "tp1_hit": False, "stop_breach_count": 0,
                "realized": 0.0, "fills": [], "risk_usd": entry * 100 * qty * cfg["stop_loss_pct"],
            }
            day_trades += 1
            break

    return {"config": {k: cfg.get(k) for k in ("stop_loss_pct", "take_profit_1_pct", "take_profit_2_pct",
                                               "max_hold_time_minutes", "optimal_target_delta", "tp1_scale_out",
                                               "underlying_invalidation_exit", "trading_hours")},
            "signal_params": params, "filters": filters, "symbols": list(symbols),
            "days": len({t.date() for t in timeline}), "trades": trades, "metrics": metrics(trades)}


def load_data(symbols, refresh=True) -> dict:
    out = {}
    for s in symbols:
        out[s] = {"bars": load_bars(s, refresh), "daily": load_daily(s, refresh),
                  "vol": load_daily(VOL_INDEX[s], refresh)}
    return out


def metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    p = np.array([t["pnl"] for t in trades])
    wins, losses = p[p > 0], p[p < 0]
    eq = np.cumsum(p)
    dd = eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]
    gross_l = -losses.sum()
    return {
        "trades": len(p), "win_rate": round(len(wins) / len(p) * 100, 1),
        "pnl": round(p.sum(), 2), "expectancy": round(p.mean(), 2),
        "profit_factor": round(wins.sum() / gross_l, 2) if gross_l > 0 else math.inf,
        "avg_win": round(wins.mean(), 2) if len(wins) else 0.0,
        "avg_loss": round(losses.mean(), 2) if len(losses) else 0.0,
        "max_dd": round(dd.min(), 2), "sharpe_per_trade": round(p.mean() / p.std(), 3) if p.std() > 0 else 0.0,
        "avg_hold_min": round(np.mean([t["hold_min"] for t in trades]), 1),
    }


def breakdown(trades: list[dict], key) -> dict:
    g = {}
    for t in trades:
        k = key(t)
        g.setdefault(k, []).append(t)
    return {str(k): metrics(v) for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="SPY,QQQ")
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--out", default="results/live_config.json")
    a = ap.parse_args()
    r = run(tuple(a.symbols.split(",")), refresh=not a.no_refresh)
    r["by_setup"] = breakdown(r["trades"], lambda t: t["setup"].split(" (")[0][:40])
    r["by_hour"] = breakdown(r["trades"], lambda t: f"{t['hour']:02d}h")
    r["by_symbol_side"] = breakdown(r["trades"], lambda t: f"{t['symbol']} {t['action']}")
    r["by_exit"] = breakdown(r["trades"], lambda t: t["exit_reason"].split(" (")[0])
    out = SCALPER / "research" / a.out
    out.write_text(json.dumps(r, indent=1, default=str))
    print(f"{r['days']} days | " + " ".join(f"{k}={v}" for k, v in r["metrics"].items()))
    for name in ("by_symbol_side", "by_exit", "by_hour"):
        print(f"-- {name}")
        for k, m in r[name].items():
            print(f"   {k:28s} n={m['trades']:3d} pnl={m.get('pnl',0):8.0f} pf={m.get('profit_factor',0)} wr={m.get('win_rate',0)}")
    print("saved", out)
