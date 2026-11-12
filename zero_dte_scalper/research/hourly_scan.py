"""Hourly-bar edge scan over ~2.9 years (Yahoo 1h). Tests well-documented intraday effects on a big sample.
Returns in bps on the underlying; t-stats; split into thirds for stability."""
import sys
from pathlib import Path
import numpy as np, pandas as pd

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
COST = 1.0  # bps round trip on the underlying (SPY spread ~0.15bp; conservative)


def day_table(sym):
    b = pd.read_pickle(CACHE / f"{sym}_1h.pkl")
    b["day"] = b.index.date
    b["hm"] = b.index.strftime("%H:%M")
    o = b.pivot_table(index="day", columns="hm", values="Open")
    c = b.pivot_table(index="day", columns="hm", values="Close")
    h = b.pivot_table(index="day", columns="hm", values="High")
    l = b.pivot_table(index="day", columns="hm", values="Low")
    full = c.notna().sum(axis=1) == 7
    return o[full], c[full], h[full], l[full]


def stat(x, label):
    x = pd.Series(x).dropna()
    if len(x) < 5:
        return f"{label:<44} n={len(x)}"
    t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
    k = len(x) // 3
    thirds = [x.iloc[:k].mean(), x.iloc[k:2 * k].mean(), x.iloc[2 * k:].mean()]
    pf = x[x > 0].sum() / max(-x[x < 0].sum(), 1e-9)
    return (f"{label:<44} n={len(x):4d} mean={x.mean():6.2f}bp t={t:5.2f} hit={(x > 0).mean() * 100:4.1f}% "
            f"PF={pf:4.2f} thirds=[{thirds[0]:6.1f} {thirds[1]:6.1f} {thirds[2]:6.1f}] sum={x.sum():7.0f}")


def noise_band(o, c, h, l, lookback=14, mult=1.0, exit_mode="band", last_entry="15:30"):
    """Zarattini/Aziz/Barbon noise-area momentum, decisions at each hourly bar close."""
    cols = list(c.columns)                       # '09:30'..'15:30' (bar start); close of col k is next hour
    dopen = o["09:30"]
    pclose = c["15:30"].shift(1)
    move = c.sub(dopen, axis=0).abs().div(dopen, axis=0)
    sig = move.rolling(lookback).mean().shift(1) * mult
    ub = pd.concat([dopen, pclose], axis=1).max(axis=1)
    lb = pd.concat([dopen, pclose], axis=1).min(axis=1)
    rets, days = [], []
    for d in c.index[lookback + 1:]:
        pos, entry, pnl, ntr = 0, 0.0, 0.0, 0
        for k, col in enumerate(cols[:-1]):      # decision at close of bar k; cannot enter at final close
            px = c.at[d, col]
            up, dn = ub[d] * (1 + sig.at[d, col]), lb[d] * (1 - sig.at[d, col])
            want = 1 if px > up else -1 if px < dn else (pos if exit_mode == "hold" else 0)
            if exit_mode == "open" and pos != 0 and want == 0:
                # stay until price crosses back through the day open
                want = pos if (px - dopen[d]) * pos > 0 else 0
            if want != pos:
                if pos != 0:
                    pnl += pos * (px / entry - 1) * 1e4 - COST; ntr += 1
                pos, entry = want, px
                if want != 0 and col > last_entry:
                    pos = 0
        if pos != 0:
            pnl += pos * (c.at[d, cols[-1]] / entry - 1) * 1e4 - COST; ntr += 1
        if ntr:
            rets.append(pnl); days.append(d)
    return pd.Series(rets, index=days)


def main(syms):
    for sym in syms:
        o, c, h, l = day_table(sym)
        print(f"\n=== {sym}  {len(c)} days  {c.index[0]} -> {c.index[-1]}  (net of {COST}bp/trade) ===")
        dopen, pclose, close = o["09:30"], c["15:30"].shift(1), c["15:30"]
        px1530 = o["15:30"]
        last30 = (close / px1530 - 1) * 1e4
        # 1. last half hour momentum
        for name, s in {"day ret (prev close->15:30)": px1530 / pclose - 1,
                        "intraday ret (open->15:30)": px1530 / dopen - 1,
                        "first hour ret": c["09:30"] / pclose - 1,
                        "14:30->15:30 ret": px1530 / o["14:30"] - 1}.items():
            print(stat(np.sign(s) * last30 - COST, f"last30 mom | sign({name})"))
            big = s.abs() > s.abs().rolling(60).quantile(0.7).shift(1)
            print(stat((np.sign(s) * last30 - COST)[big], f"   ...only top-30% |move|"))
        # 2. hour-to-hour continuation
        cols = list(c.columns)
        for k in range(1, 7):
            prev = c[cols[k - 1]] / o[cols[k - 1]] - 1
            nxt = (c[cols[k]] / o[cols[k]] - 1) * 1e4
            print(stat(np.sign(prev) * nxt - COST, f"continuation {cols[k-1]}->{cols[k]} bar"))
        # 3. gap -> first hour / rest of day
        gap = dopen / pclose - 1
        fh = (c["09:30"] / dopen - 1) * 1e4
        print(stat(np.sign(gap) * fh - COST, "gap continuation in first hour"))
        biggap = gap.abs() > 0.004
        print(stat((np.sign(gap) * fh - COST)[biggap], "   ...|gap|>40bp"))
        rest = (close / c["09:30"] - 1) * 1e4
        print(stat(np.sign(c["09:30"] / dopen - 1) * rest - COST, "first hour dir -> rest of day"))
        print(stat(np.sign(c["09:30"] / pclose - 1) * rest - COST, "first hour (from prev close) -> rest of day"))
        # 4. noise band momentum
        for mult in (0.75, 1.0, 1.25):
            for mode in ("band", "open", "hold"):
                r = noise_band(o, c, h, l, mult=mult, exit_mode=mode)
                print(stat(r, f"noise-band mom x{mult} exit={mode} (per day)"))


if __name__ == "__main__":
    main(sys.argv[1].split(",") if len(sys.argv) > 1 else ["SPY", "QQQ", "IWM"])
