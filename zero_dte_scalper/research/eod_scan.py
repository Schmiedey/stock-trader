"""Dig into the end-of-day reversal found by hourly_scan: fade the day's move into the close."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
from hourly_scan import day_table, CACHE

def st(x, label):
    x = pd.Series(x).dropna()
    if len(x) < 8: return f"{label:<46} n={len(x)}"
    t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x))); k = len(x) // 3
    return (f"{label:<46} n={len(x):4d} mean={x.mean():6.2f}bp t={t:5.2f} hit={(x>0).mean()*100:4.1f}% "
            f"thirds=[{x.iloc[:k].mean():5.1f} {x.iloc[k:2*k].mean():5.1f} {x.iloc[2*k:].mean():5.1f}]")

vix = pd.read_pickle(CACHE / "VIX_1d.pkl")["Close"]; vix.index = vix.index.date
for sym in ["SPY", "QQQ", "IWM"]:
    o, c, h, l = day_table(sym)
    pclose = c["15:30"].shift(1); px = o["15:30"]
    last30 = (c["15:30"] / px - 1) * 1e4
    day = (px / pclose - 1) * 1e4
    intr = (px / o["09:30"] - 1) * 1e4
    pv = vix.shift(1).reindex(c.index)
    rng = ((h.max(axis=1) - l.min(axis=1)) / o["09:30"] * 1e4)
    print(f"\n=== {sym} hourly, GROSS fade return 15:30->16:00 (bps) ===")
    print(st(last30, "unconditional last30 (drift)"))
    fade = -np.sign(day) * last30
    print(st(fade[day > 0], "fade | up day")); print(st(fade[day < 0], "fade | down day"))
    for lo, hi in ((0, 25), (25, 50), (50, 100), (100, 999)):
        m = (day.abs() >= lo) & (day.abs() < hi)
        print(st(fade[m], f"fade | |day move| {lo}-{hi}bp"))
    for lo, hi in ((0, 14), (14, 17), (17, 21), (21, 99)):
        m = (pv >= lo) & (pv < hi)
        print(st(fade[m], f"fade | prior VIX {lo}-{hi}"))
    # is price near day extreme at 15:30?
    hi_d = h.iloc[:, :6].max(axis=1); lo_d = l.iloc[:, :6].min(axis=1)
    pos = (px - lo_d) / (hi_d - lo_d)
    print(st(fade[((day > 0) & (pos > 0.8)) | ((day < 0) & (pos < 0.2))], "fade | at day extreme (top/bottom 20%)"))
    print(st(fade[((day > 0) & (pos < 0.5)) | ((day < 0) & (pos > 0.5))], "fade | already retraced >50%"))
    lasth = (px / o["14:30"] - 1) * 1e4
    print(st(fade[np.sign(lasth) == np.sign(day)], "fade | 14:30-15:30 hour WITH day move"))
    print(st(fade[np.sign(lasth) != np.sign(day)], "fade | 14:30-15:30 hour AGAINST day move"))
    print(st(-np.sign(lasth) * last30, "fade last hour (14:30->15:30) instead"))
    both = (np.sign(lasth) == np.sign(day)) & (day.abs() >= 25)
    print(st(fade[both], "fade | hour with move & |day|>=25bp"))

    # 5m profile (63 days)
    b = pd.read_pickle(CACHE / f"{sym}_5m.pkl"); b["day"] = b.index.date; b["hm"] = b.index.strftime("%H:%M")
    C = b.pivot_table(index="day", columns="hm", values="Close"); O = b.pivot_table(index="day", columns="hm", values="Open")
    C = C[C.notna().sum(axis=1) >= 76]; O = O.loc[C.index]
    pc = C["15:55"].shift(1)
    print(f"--- {sym} 5m ({len(C)} days): fade sign(prev close -> entry), entry time x exit time, gross bps (t) ---")
    exits = ["15:45", "15:50", "15:55", "16:00"]
    print("entry   " + "".join(f"{e:>16}" for e in exits))
    for ent in ["14:00", "14:30", "15:00", "15:15", "15:30", "15:40", "15:45"]:
        row = f"{ent}   "
        for ex in exits:
            if ex <= ent: row += f"{'':>16}"; continue
            xp = C["15:55"] if ex == "16:00" else O[ex]
            r = (-np.sign(O[ent] / pc - 1) * (xp / O[ent] - 1) * 1e4).dropna()
            row += f"{r.mean():8.2f}({r.mean()/(r.std()/np.sqrt(len(r))):5.2f}) "
        print(row)
