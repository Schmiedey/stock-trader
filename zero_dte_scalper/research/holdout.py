"""One-shot holdout: run the frozen v4 rule (no parameters touched) on years never used in research.
Usage: python holdout.py QQQ   (needs data/cache/QQQ_1m_hold.pkl from: fetch_duka.py QQQ 2017-01-01 2020-12-31 hold 2)"""
import sys, warnings
import numpy as np, pandas as pd, yfinance as yf
warnings.filterwarnings("ignore")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import improve as I
from long_scan import CACHE

sym = sys.argv[1] if len(sys.argv) > 1 else "QQQ"
vn = "VXN" if sym == "QQQ" else "VIX"
p = CACHE / f"{vn}_1d_hold.pkl"
if not p.exists():
    d = yf.download(f"^{vn}", start="2016-06-01", end="2021-01-10", progress=False, auto_adjust=False)
    if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.get_level_values(0)
    d.to_pickle(p)
D = I.data(sym, tag="hold")
v = pd.read_pickle(p)["Close"]; v.index = v.index.date
D["iv0"] = v.shift(1).reindex(D["idx"]).values / 100
t = I.backtest(D)
ys = sorted(set(D["years"]))
print(f"{sym} HOLDOUT {D['idx'][0]} -> {D['idx'][-1]} ({len(D['C'])} sessions), frozen v4 rule, shares net 1bp")
x = np.zeros(len(D["C"])); np.add.at(x, t.d.values, t.bps.values); x = x[15:]; yy = D["years"][15:]
tt = t.bps.mean() / (t.bps.std() / np.sqrt(len(t)))
print(f"  trades={len(t)} win={(t.bps > 0).mean() * 100:.1f}% avg={t.bps.mean():.2f}bp t={tt:.2f} Sharpe={x.mean() / x.std() * np.sqrt(252):.2f}")
print("  %/yr unlevered: " + "  ".join(f"{y}: {x[yy == y].sum() / 100:5.1f} (n={int((t.year == y).sum())})" for y in ys))
for im in (1.0, 1.2, 1.5):
    o = I.opt_pnl(D, t, im)
    print(f"  ATM option IVx{im}: avg ${np.nanmean(o):6.1f}/contract PF {np.nansum(o[o > 0]) / -np.nansum(o[o < 0]):.2f} | by year: " + "  ".join(f"{y}: ${np.nansum(o[t.year.values == y]):.0f}" for y in ys))
