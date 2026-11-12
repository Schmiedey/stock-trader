"""Weekly / monthly results of the v4 momentum rule (backtest). $ figures:
  options: 1 ATM 0DTE contract per trade, Black-Scholes at prior-day vol index x1.2, 3% spread
  shares:  $25,000 of the ETF per trade (no leverage), 1bp cost
Usage: python periods.py QQQ"""
import sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent)); sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import momentum as RM
from long_scan import load, MINS, CACHE

sym = sys.argv[1] if len(sys.argv) > 1 else "QQQ"
O, C, H, L, years, idx = load(sym)
b = pd.read_pickle(CACHE / f"{sym}_1m_long.pkl"); b["day"] = b.index.date; b["hm"] = b.index.strftime("%H:%M")
V = b.pivot_table(index="day", columns="hm", values="Volume").reindex(index=idx, columns=MINS).fillna(0).values
v = pd.read_pickle(CACHE / f"{'VXN' if sym == 'QQQ' else 'VIX'}_1d_long.pkl")["Close"]; v.index = v.index.date
db, tr = RM.backtest(O, C, H, L, V, years, v.shift(1).reindex(idx).values / 100, iv_mult=1.2)
tr["date"] = pd.to_datetime([idx[int(d)] for d in tr.d])
tr["opt_usd"] = tr.opt * 100; tr["shr_usd"] = tr.bps / 1e4 * 25000
tr.to_csv(Path(__file__).parent / "results" / f"momentum_trades_{sym}.csv", index=False)
all_days = pd.to_datetime(list(idx[15:]))
for name, rule in (("WEEK", "W-FRI"), ("MONTH", "ME")):
    g = tr.set_index("date").resample(rule).agg(trades=("bps", "size"), wins=("opt_usd", lambda x: (x > 0).sum()), opt=("opt_usd", "sum"), shr=("shr_usd", "sum"))
    g = g[g.index >= all_days[0]]
    print(f"\n== {sym} by {name} ({len(g)} periods, {all_days[0].date()} -> {all_days[-1].date()})")
    for col, lab in (("opt", "1 option contract"), ("shr", "$25k shares      ")):
        x = g[col]
        print(f"  {lab}: positive {((x > 0).mean() * 100):4.1f}% | avg ${x.mean():7.0f} | median ${x.median():7.0f} | best ${x.max():7.0f} | worst ${x.min():7.0f} | longest losing run {max((len(list(grp)) for k, grp in __import__('itertools').groupby(x <= 0) if k), default=0)}")
    n = 10 if name == "WEEK" else 14
    print(f"  last {n}:  period-end   trades wins  options$  shares$")
    for d, r in g.tail(n).iterrows():
        print(f"            {d.date()}   {int(r.trades):3d}   {int(r.wins):3d}   {r.opt:8.0f}  {r.shr:7.0f}")
print("\nper year:  " + "  ".join(f"{y}: opt ${tr[tr.year == y].opt_usd.sum():.0f} / shr ${tr[tr.year == y].shr_usd.sum():.0f}" for y in sorted(set(years))))
