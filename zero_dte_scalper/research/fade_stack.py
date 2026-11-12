"""Follow-up to scalp_search: does stacking conditions make the midday breakout-fade big enough to trade?
Fade-signed forward bps (gross) by number of agreeing fade signals and by regime. IS 2021-23 / OOS 2024-26."""
import sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
from long_scan import IX
from scalp_search import scan, edge

def show(label, r, isd):
    def st(x): return f"{x.mean():6.2f}bp t={x.mean() / (x.std(ddof=1) / np.sqrt(len(x))):5.2f} n={len(x):5d}" if len(x) > 30 else f"{'n=' + str(len(x)):>26}"
    print(f"  {label:<46} IS {st(r[isd])} | OOS {st(r[~isd])}")

for sym in sys.argv[1].split(","):
    _, (O, C, H, L, V, years, idx, T) = scan(sym)
    names = ["break_15m_range", "break_30m_range", "break_60m_range", "break_120m_range", "rsi2<5", "boll20_2.0", "3_bars_in_row", "vwap_dev>2.0sd", "burst5m>2.0z", "break_HOD_LOD"]
    S = sum(np.nan_to_num(T[n]) for n in names)                     # net count of stretched-up minus stretched-down signals
    op = O[:, :1]; pc = np.r_[np.nan, C[:-1, -1]][:, None]
    sig = pd.DataFrame(np.abs(C / op - 1)).rolling(14).mean().shift(1).values
    inside = (C < np.fmax(op, pc) * (1 + sig)) & (C > np.fmin(op, pc) * (1 - sig))
    dayrng = pd.Series((H.max(axis=1) - L.min(axis=1)) / O[:, 0]).rolling(20).mean().shift(1).values[:, None]
    lowvol = dayrng < np.nanmedian(dayrng)
    print(f"== {sym}: fade when >=k stretch signals agree (first minute it happens), midday 10:30-14:30")
    for k in (2, 3, 4, 5, 6):
        t = edge(np.where(S >= k, 1, np.where(S <= -k, -1, 0)))
        for h in (5, 15, 30):
            lo, hi = IX["10:30"], IX["14:30"]
            for lab, mask in (("all", np.ones_like(inside)), ("inside noise band", inside), ("outside noise band", ~inside), ("low-vol regime", lowvol & np.ones_like(inside)), ("high-vol regime", ~lowvol & np.ones_like(inside))):
                if lab != "all" and h != 15: continue
                di, mi = np.nonzero(t[:, lo:hi] * mask[:, lo:hi]); mi += lo
                r = -t[di, mi] * (O[di, mi + 1 + h] / O[di, mi + 1] - 1) * 1e4
                show(f"k>={k} hold {h:2d}m {lab}", r, years[di] <= 2023)
