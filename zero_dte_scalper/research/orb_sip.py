"""5-minute opening-range breakout on "stocks in play" (after Zarattini, Barbon & Aziz 2024).
Each day: rank the universe by relative volume of the first 5-minute candle (vs its own 14-day average), keep the
top N with RVOL >= min_rvol, trade in the direction of that candle:
  entry  = stop order at the candle's high (long) / low (short), from 09:35
  stop   = stop_atr x ATR14(daily) from entry;  exit = stop, optional R target, or the close
Simulated on 1-minute bars where available (last ~20 days), 5-minute bars before that; an entry bar that also
touches the stop counts as a loss (pessimistic). Costs: cost_bps round trip + 1 tick-ish slippage on stops.
Usage: python orb_sip.py"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

D = Path(__file__).resolve().parent.parent / "data" / "cache" / "stocks"


def prep():
    b5, b1, bd = (pd.read_pickle(D / f"bars_{k}.pkl") for k in ("5m", "1m", "1d"))
    first = {}
    for t, df in b5.items():
        df = df.dropna()
        f = df[df.index.strftime("%H:%M") == "09:30"].copy()
        f.index = f.index.date
        first[t] = f
    atr = {}
    for t, d in bd.items():
        d = d.dropna()
        tr = pd.concat([d.High - d.Low, (d.High - d.Close.shift()).abs(), (d.Low - d.Close.shift()).abs()], axis=1).max(axis=1)
        a = tr.rolling(14).mean().shift(1); a.index = a.index.date
        atr[t] = a
    return b5, b1, first, atr


def sim_day(day_bars, side, level, atr, stop_atr, target_r, mode):
    """Returns (entry, exit, risk_per_share, minutes_held) or None. day_bars start at 09:35."""
    o, h, l, c = (day_bars[k].values for k in ("Open", "High", "Low", "Close"))
    if mode == "market":                       # variant A: in at 09:35 open, stop at far side of the first candle
        i0, entry, stop = 0, o[0], level
        if (entry - stop) * side <= 0: return None
    else:
        hit = np.nonzero(h >= level)[0] if side > 0 else np.nonzero(l <= level)[0]
        if len(hit) == 0: return None
        i0 = hit[0]
        entry = max(level, o[i0]) if side > 0 else min(level, o[i0])
        stop = entry - side * stop_atr * atr
    risk = abs(entry - stop)
    tgt = entry + side * target_r * risk if target_r else None
    for i in range(i0, len(o)):
        if i > i0 or mode == "market":
            if (o[i] - stop) * side <= 0: return entry, o[i], risk, i - i0          # gapped through the stop
        if (l[i] <= stop) if side > 0 else (h[i] >= stop): return entry, stop, risk, i - i0
        if tgt is not None and ((h[i] >= tgt) if side > 0 else (l[i] <= tgt)): return entry, tgt, risk, i - i0
    return entry, c[-1], risk, len(o) - i0


def run(b5, b1, first, atr, top_n=5, min_rvol=1.0, stop_atr=0.10, target_r=None, mode="stop", cost_bps=3.0,
        min_price=5.0, direction="candle", rank="rvol"):
    days = sorted(set().union(*[set(f.index) for f in first.values()]))
    rv = {t: f.Volume / f.Volume.rolling(14).mean().shift(1) for t, f in first.items()}
    rows = []
    for d in days[14:]:
        cand = []
        for t, f in first.items():
            if d not in f.index or t not in atr or d not in atr[t].index: continue
            r, a, bar = rv[t].get(d), atr[t].get(d), f.loc[d]
            if not np.isfinite(r) or not np.isfinite(a) or r < min_rvol or bar.Open < min_price or bar.Close == bar.Open: continue
            cand.append((r, t, bar, a))
        cand.sort(key=lambda x: -x[0])
        for r, t, bar, a in cand[:top_n]:
            side = 1 if bar.Close > bar.Open else -1
            if direction == "fade": side = -side
            src = b1.get(t)
            db = src[(src.index.date == d)] if src is not None else None
            fine = db is not None and len(db) > 300
            if not fine:
                db = b5[t][b5[t].index.date == d]
            db = db.dropna().between_time("09:35", "15:59")
            if len(db) < 20: continue
            if mode == "market": level = bar.Low if side > 0 else bar.High
            else: level = bar.High if side > 0 else bar.Low
            res = sim_day(db, side, level, a, stop_atr, target_r, mode)
            if res is None: continue
            e, x, risk, held = res
            pct = side * (x / e - 1) * 1e4 - cost_bps
            rows.append(dict(day=d, t=t, side=side, rvol=r, bps=pct, R=pct / (risk / e * 1e4), held=held * (1 if fine else 5), fine=fine))
    return pd.DataFrame(rows)


def summ(label, tr):
    if len(tr) < 20: return f"{label:<44} n={len(tr)}"
    x = tr.bps; daily = tr.groupby("day").bps.mean()
    t = daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily)))
    pf = x[x > 0].sum() / -x[x < 0].sum()
    f = tr[tr.fine]
    return (f"{label:<44} n={len(tr):4d} win={(x > 0).mean() * 100:4.1f}% avg={x.mean():6.1f}bp ({tr.R.mean():+.2f}R) PF={pf:4.2f} "
            f"day-t={t:5.2f} hold={tr.held.median():3.0f}m | 1m-bar days only: n={len(f):3d} avg={f.bps.mean():6.1f}bp")


if __name__ == "__main__":
    b5, b1, first, atr = prep()
    print("ORB stocks-in-play, net of 3bp round trip. bps are on position notional.")
    base = None
    for label, kw in [("paper rule: top5, stop 10% ATR, exit close", {}),
                      ("  top 10", {"top_n": 10}), ("  top 20", {"top_n": 20}), ("  top 3", {"top_n": 3}),
                      ("  RVOL >= 2", {"min_rvol": 2.0}), ("  RVOL >= 3, top 10", {"min_rvol": 3.0, "top_n": 10}),
                      ("  no RVOL filter, random-ish (top 173)", {"top_n": 173, "min_rvol": 0}),
                      ("  stop 5% ATR", {"stop_atr": 0.05}), ("  stop 20% ATR", {"stop_atr": 0.20}), ("  stop 50% ATR", {"stop_atr": 0.5}),
                      ("  target 2R", {"target_r": 2}), ("  target 5R", {"target_r": 5}), ("  target 10R", {"target_r": 10}),
                      ("  market at 09:35, stop = candle far side", {"mode": "market"}),
                      ("  FADE the candle (control)", {"direction": "fade"}),
                      ("  cost 8bp", {"cost_bps": 8.0})]:
        tr = run(b5, b1, first, atr, **kw)
        if base is None: base = tr
        print(summ(label, tr))
    base.to_pickle(D / "orb_sip_base.pkl")
