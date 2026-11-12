"""Close-fade scalp: at 15:50 ET fade the day's move (prev close -> now), exit 15:55. Timing + robustness."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"

def piv(sym, iv):
    b = pd.read_pickle(CACHE / f"{sym}_{iv}.pkl"); b["day"] = b.index.date; b["hm"] = b.index.strftime("%H:%M")
    C = b.pivot_table(index="day", columns="hm", values="Close"); O = b.pivot_table(index="day", columns="hm", values="Open")
    need = 76 if iv == "5m" else 380
    C = C[C.notna().sum(axis=1) >= need]
    return O.loc[C.index], C

def ts(r):
    r = r.dropna(); return r.mean(), (r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 3 and r.std() > 0 else 0), len(r)

if __name__ == "__main__":
    SY = ["SPY", "QQQ", "IWM"]
    print("== 1m profile (last ~20 days): fade-signed return of each minute, signal = sign(prev close -> 15:40) ==")
    acc = {}
    for s in SY:
        O, C = piv(s, "1m"); last = C.ffill(axis=1).iloc[:, -1]; pc = last.shift(1)
        sg = -np.sign(O["15:40"] / pc - 1)
        acc[s] = pd.DataFrame({m: sg * (C[m] / O[m] - 1) * 1e4 for m in C.columns if m >= "15:40"})
    mins = acc["SPY"].columns
    print("min    " + "".join(f"{s:>9}" for s in SY) + "   | cum SPY")
    cum = 0
    for m in mins:
        cum += acc["SPY"][m].mean()
        print(f"{m}  " + "".join(f"{acc[s][m].mean():9.2f}" for s in SY) + f"   | {cum:6.2f}")

    print("\n== 5m: every bar of the day, fade-signed return (signal = sign(prev close -> bar open)); |t|>2 only ==")
    for s in SY:
        O, C = piv(s, "5m"); pc = C["15:55"].shift(1); out = []
        for m in C.columns:
            mu, t, n = ts(-np.sign(O[m] / pc - 1) * (C[m] / O[m] - 1) * 1e4)
            if abs(t) > 2: out.append(f"{m}:{mu:+.1f}({t:+.1f})")
        print(s, len(C), "days:", "  ".join(out))

    print("\n== 5m robustness of 15:50->15:55 fade (gross bps) ==")
    for s in SY:
        O, C = piv(s, "5m"); pc = C["15:55"].shift(1)
        ret = (O["15:55"] / O["15:50"] - 1) * 1e4
        day = (O["15:50"] / pc - 1) * 1e4; intr = (O["15:50"] / O["09:30"] - 1) * 1e4
        lasth = (O["15:50"] / O["14:50"] - 1) * 1e4; l20 = (O["15:50"] / O["15:30"] - 1) * 1e4
        print(f"-- {s}: 5m-bar std {ret.std():.1f}bp")
        for name, sg in {"prev close->15:50": day, "open->15:50": intr, "14:50->15:50": lasth, "15:30->15:50": l20}.items():
            r = -np.sign(sg) * ret; mu, t, n = ts(r); k = n // 2; r2 = r.dropna()
            print(f"   signal {name:<18} n={n} mean={mu:5.2f} t={t:5.2f} hit={(r2 > 0).mean() * 100:4.1f}% halves=[{r2.iloc[:k].mean():5.2f} {r2.iloc[k:].mean():5.2f}]")
        for th in (10, 25, 50):
            r = (-np.sign(day) * ret)[day.abs() >= th]; mu, t, n = ts(r)
            print(f"   |day move|>={th:<3}bp        n={n} mean={mu:5.2f} t={t:5.2f} hit={(r.dropna() > 0).mean() * 100:4.1f}%")
        r = (-np.sign(day) * ret); print(f"   up days {ts(r[day > 0])[0]:5.2f} (n={ts(r[day > 0])[2]})  down days {ts(r[day < 0])[0]:5.2f} (n={ts(r[day < 0])[2]})")
        for ex in ("15:55", "16:00"):
            for en in ("15:45", "15:50"):
                xp = C["15:55"] if ex == "16:00" else O[ex]
                if en < ex:
                    mu, t, n = ts(-np.sign(O[en] / pc - 1) * (xp / O[en] - 1) * 1e4); print(f"   {en}->{ex}: {mu:5.2f} (t={t:4.2f})", end="")
        print()
