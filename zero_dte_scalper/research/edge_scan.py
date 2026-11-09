#!/usr/bin/env python3
"""
Edge scan: does a signal predict the underlying's next move at all?

For every completed 5m bar we compute the forward move of SPY/QQQ over 15/30/60 minutes
(and to the 15:50 flat), signed by the signal's direction, in basis points. An entry rule
with no positive signed forward move cannot be rescued by option exits.

Rules scanned:
  - every live-engine setup (score_bars with each setup alone, and the full engine)
  - a library of classic intraday candidates (ORB, VWAP, trend-day, mean reversion, gap)

Split: first 2/3 of days = in-sample (IS), last 1/3 = out-of-sample (OOS).
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay as R
from zero_dte_scalper.src.market_data import calculate_indicators

HORIZONS = {"15m": 3, "30m": 6, "60m": 12}


def feature_frame(sym: str, bars: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """One row per completed bar with indicators, levels, and forward returns (bps)."""
    days = sorted(set(bars.index.date))
    rows = []
    for i in range(1, len(days)):
        prev_d, d = days[i - 1], days[i]
        win = bars[(bars.index.date == prev_d) | (bars.index.date == d)]
        ind = calculate_indicators(win)
        t = ind[ind.index.date == d].copy()
        if len(t) < 40:
            continue
        pdy = daily[daily.index.date < d]
        if pdy.empty:
            continue
        pdh, pdl, pdc = (float(pdy.iloc[-1][k]) for k in ("High", "Low", "Close"))
        dprev = pdy["Close"]
        t["trend_up"] = bool(len(dprev) > 20 and dprev.iloc[-1] > dprev.ewm(span=20, adjust=False).mean().iloc[-1])
        t["day"] = d
        t["sym"] = sym
        t["hm"] = (t.index + pd.Timedelta(minutes=5)).strftime("%H:%M")  # bar close time
        t["open_px"] = float(t["Open"].iloc[0])
        t["gap_bps"] = (t["open_px"] / pdc - 1) * 1e4
        orb = t.between_time("09:30", "09:40")
        t["orb_h"], t["orb_l"] = float(orb["High"].max()), float(orb["Low"].min())
        orb30 = t.between_time("09:30", "09:55")
        t["orb30_h"], t["orb30_l"] = float(orb30["High"].max()), float(orb30["Low"].min())
        t["pdh"], t["pdl"], t["pdc"] = pdh, pdl, pdc
        t["hod"] = t["High"].cummax()
        t["lod"] = t["Low"].cummin()
        t["day_ret_bps"] = (t["Close"] / t["open_px"] - 1) * 1e4
        t["prev_close"] = t["Close"].shift(1)
        t["prev_vwap"] = t["VWAP"].shift(1)
        c = t["Close"].values
        for name, n in HORIZONS.items():
            fwd = np.full(len(c), np.nan)
            fwd[:-n] = (c[n:] / c[:-n] - 1) * 1e4
            t[f"f{name}"] = fwd
        flat_idx = np.searchsorted(t["hm"].values, "15:50")
        flat_px = c[min(flat_idx, len(c) - 1)]
        t["feod"] = (flat_px / c - 1) * 1e4
        # adverse excursion over next 30m (bps), for stop realism
        lows = t["Low"].values
        highs = t["High"].values
        mae_long = np.full(len(c), np.nan)
        mae_short = np.full(len(c), np.nan)
        for j in range(len(c) - 6):
            mae_long[j] = (lows[j + 1:j + 7].min() / c[j] - 1) * 1e4
            mae_short[j] = (highs[j + 1:j + 7].max() / c[j] - 1) * 1e4
        t["mae_long"], t["mae_short"] = mae_long, mae_short
        rows.append(t)
    return pd.concat(rows)


def candidate_rules(f: pd.DataFrame) -> dict:
    """Return {rule_name: Series of +1 (long) / -1 (short) / 0} — evaluated on completed bars."""
    C, V = f["Close"], f["VWAP"]
    rv = f["Rel_Volume"]
    hm = f["hm"]
    morning = (hm >= "09:45") & (hm <= "11:30")
    afternoon = (hm >= "13:30") & (hm <= "15:30")
    rules = {}

    def sig(long_mask, short_mask):
        return pd.Series(np.where(long_mask, 1, np.where(short_mask, -1, 0)), index=f.index)

    # ORB breakouts (first close beyond the range)
    first_up = (C > f["orb_h"]) & (f["prev_close"] <= f["orb_h"])
    first_dn = (C < f["orb_l"]) & (f["prev_close"] >= f["orb_l"])
    rules["orb15_break"] = sig(first_up & morning, first_dn & morning)
    rules["orb15_break_vol"] = sig(first_up & morning & (rv > 1.3), first_dn & morning & (rv > 1.3))
    rules["orb15_break_trend"] = sig(first_up & morning & f["trend_up"], first_dn & morning & ~f["trend_up"])
    f30u = (C > f["orb30_h"]) & (f["prev_close"] <= f["orb30_h"]) & (hm >= "10:00")
    f30d = (C < f["orb30_l"]) & (f["prev_close"] >= f["orb30_l"]) & (hm >= "10:00")
    rules["orb30_break"] = sig(f30u & (hm <= "12:00"), f30d & (hm <= "12:00"))
    # ORB failure (fade a breakout that closes back inside)
    rules["orb15_fail_fade"] = sig(
        morning & (f["prev_close"] < f["orb_l"]) & (C > f["orb_l"]),
        morning & (f["prev_close"] > f["orb_h"]) & (C < f["orb_h"]))

    # VWAP reclaim / loss
    rules["vwap_reclaim"] = sig((f["prev_close"] < f["prev_vwap"]) & (C > V) & (morning | afternoon),
                                (f["prev_close"] > f["prev_vwap"]) & (C < V) & (morning | afternoon))
    # Trend-day continuation: strong day, pullback to EMA9, resume
    strong_up = f["day_ret_bps"] > 40
    strong_dn = f["day_ret_bps"] < -40
    touch9_up = (f["Low"] <= f["EMA9"]) & (C > f["EMA9"]) & (C > f["Open"])
    touch9_dn = (f["High"] >= f["EMA9"]) & (C < f["EMA9"]) & (C < f["Open"])
    rules["trendday_pullback"] = sig(strong_up & touch9_up & (C > V), strong_dn & touch9_dn & (C < V))
    rules["trendday_pullback_pm"] = sig(strong_up & touch9_up & (C > V) & afternoon,
                                        strong_dn & touch9_dn & (C < V) & afternoon)
    # Mean reversion: stretched from VWAP with RSI extreme, reversal candle
    stretch = (C - V) / f["ATR"]
    rules["mr_vwap_stretch"] = sig((stretch < -2.0) & (f["RSI14"] < 30) & (C > f["Open"]),
                                   (stretch > 2.0) & (f["RSI14"] > 70) & (C < f["Open"]))
    rules["mr_rsi_extreme"] = sig((f["RSI14"] < 25) & (C > f["Open"]), (f["RSI14"] > 75) & (C < f["Open"]))
    # Prior-day level break / sweep
    rules["pdh_pdl_break"] = sig((C > f["pdh"]) & (f["prev_close"] <= f["pdh"]) & (hm >= "09:45"),
                                 (C < f["pdl"]) & (f["prev_close"] >= f["pdl"]) & (hm >= "09:45"))
    rules["pdh_pdl_sweep_fade"] = sig((f["Low"] < f["pdl"]) & (C > f["pdl"]) & (hm >= "09:45"),
                                      (f["High"] > f["pdh"]) & (C < f["pdh"]) & (hm >= "09:45"))
    # Gap: fade or go, decided at 10:00
    at10 = hm == "10:00"
    rules["gap_fade"] = sig(at10 & (f["gap_bps"] < -30) & (C > f["open_px"]),
                            at10 & (f["gap_bps"] > 30) & (C < f["open_px"]))
    rules["gap_go"] = sig(at10 & (f["gap_bps"] > 30) & (C > f["open_px"]),
                          at10 & (f["gap_bps"] < -30) & (C < f["open_px"]))
    # New HOD/LOD breakout in the afternoon (trend into close)
    rules["pm_hod_lod_break"] = sig(afternoon & (C >= f["hod"].shift(1)) & (C > V),
                                    afternoon & (C <= f["lod"].shift(1)) & (C < V))
    # Time-of-day drift with daily trend (baseline: is there a drift at all?)
    rules["trend_drift_1030"] = sig((hm == "10:30") & f["trend_up"], (hm == "10:30") & ~f["trend_up"])
    rules["intraday_mom_1400"] = sig((hm == "14:00") & (f["day_ret_bps"] > 0), (hm == "14:00") & (f["day_ret_bps"] < 0))
    return rules


def live_rules(sym: str, f: pd.DataFrame, data: dict) -> dict:
    out = {}
    variants = {"live_engine": {}, "live_orb_only": {"setups": ("orb",), "min_score": 1},
                "live_pullback_only": {"setups": ("pullback",), "min_score": 1},
                "live_sweep_only": {"setups": ("sweep",), "min_score": 1},
                "live_macd_only": {"setups": ("macd",), "min_score": 1},
                "live_score1": {"min_score": 1}, "live_score3": {"min_score": 3}}
    for name, params in variants.items():
        sigs = R.signal_cache(sym, data[sym]["bars"], data[sym]["daily"], params)
        close_t = f.index + pd.Timedelta(minutes=5)
        s = [1 if (x := sigs.get(t)) and x["signal"] == "CALL" else -1 if x and x["signal"] == "PUT" else 0
             for t in close_t]
        out[name] = pd.Series(s, index=f.index)
    return out


def evaluate(f: pd.DataFrame, s: pd.Series, split_day) -> dict:
    m = s != 0
    res = {}
    for part, mask in (("IS", f["day"] < split_day), ("OOS", f["day"] >= split_day), ("ALL", f["day"] == f["day"])):
        sel = m & mask
        d = s[sel]
        r = {"n": int(sel.sum()), "days": int(f.loc[sel, "day"].nunique())}
        for h in list(HORIZONS) + ["eod"]:
            x = (f.loc[sel, f"f{h}"] * d).dropna()
            r[f"{h}_bps"] = round(float(x.mean()), 2) if len(x) else 0.0
            r[f"{h}_hit"] = round(float((x > 0).mean() * 100), 1) if len(x) else 0.0
            r[f"{h}_t"] = round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2) if len(x) > 2 and x.std() > 0 else 0.0
        mae = np.where(d > 0, f.loc[sel, "mae_long"], -f.loc[sel, "mae_short"])
        r["mae30_bps"] = round(float(np.nanmean(mae)), 1) if len(mae) else 0.0
        res[part] = r
    return res


def main():
    data = R.load_data(("SPY", "QQQ"), refresh=False)
    frames = {s: feature_frame(s, data[s]["bars"], data[s]["daily"]) for s in ("SPY", "QQQ")}
    all_days = sorted(set(frames["SPY"]["day"]))
    split_day = all_days[int(len(all_days) * 2 / 3)]
    report = {"split_day": str(split_day), "days": len(all_days), "rules": {}}
    for sym, f in frames.items():
        rules = {**candidate_rules(f), **live_rules(sym, f, data)}
        for name, s in rules.items():
            report["rules"].setdefault(name, {})[sym] = evaluate(f, s, split_day)
    out = Path(__file__).resolve().parent / "results" / "edge_scan.json"
    out.write_text(json.dumps(report, indent=1, default=str))

    print(f"days={len(all_days)} split={split_day}  signed fwd move in bps (t-stat) | IS -> OOS")
    hdr = f"{'rule':24s} {'sym':4s} {'nIS':>4s} {'nOOS':>4s} | {'30m IS':>12s} {'30m OOS':>12s} | {'EOD IS':>12s} {'EOD OOS':>12s} | mae30"
    print(hdr)
    for name, per in report["rules"].items():
        for sym, r in per.items():
            a, b = r["IS"], r["OOS"]
            print(f"{name:24s} {sym:4s} {a['n']:4d} {b['n']:4d} | {a['30m_bps']:6.1f}({a['30m_t']:4.1f}) {b['30m_bps']:6.1f}({b['30m_t']:4.1f}) | "
                  f"{a['eod_bps']:6.1f}({a['eod_t']:4.1f}) {b['eod_bps']:6.1f}({b['eod_t']:4.1f}) | {r['ALL']['mae30_bps']:5.1f}")


if __name__ == "__main__":
    main()
