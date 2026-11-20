"""Which 0DTE structure best expresses the v4.0.2 momentum signal (band stop)? 2021-26 and holdout 2019-20.
Black-Scholes at prior-day VXN x mult (no skew), spread max($0.02, 3%) per leg, $ per 1 structure per trade."""
import sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent)); sys.path.insert(0, str(Path(__file__).parent))
from zero_dte_scalper.src.greeks import black_scholes_price as bs
import improve as I
from long_scan import CACHE
Y = 365.25 * 86400

def leg(S, K, k, iv, kind):
    return max(0.01, bs(S, K, max(60, (389 - k) * 60) / Y, iv, option_type=kind))

def pnl(D, t, im, struct):
    out = np.full(len(t), np.nan)
    for i, r in enumerate(t.itertuples()):
        iv = D["iv0"][r.d] * im
        if not np.isfinite(iv): continue
        kind = "call" if r.side > 0 else "put"; s = r.side
        if struct == "ATM": legs = [(round(r.e), 1)]
        elif struct == "0.5% ITM": legs = [(round(r.e * (1 - s * 0.005)), 1)]
        elif struct == "0.3% OTM": legs = [(round(r.e * (1 + s * 0.003)), 1)]
        elif struct.startswith("debit"):
            w = float(struct.split()[1].rstrip("%")) / 100
            legs = [(round(r.e), 1), (round(r.e * (1 + s * w)), -1)]
        v = 0.0
        for K, q in legs:
            a, b = leg(r.e, K, r.ek, iv, kind), leg(r.x, K, r.xk, iv, kind)
            sp = lambda m: max(0.02, 0.03 * m)
            v += q * ((b - q * sp(b) / 2) - (a + q * sp(a) / 2))
        out[i] = v * 100
    return out

rows = []
for tag in ("long", "hold"):
    D = I.data("QQQ", tag=tag)
    if tag == "hold":
        v = pd.read_pickle(CACHE / "VXN_1d_hold.pkl")["Close"]; v.index = v.index.date; D["iv0"] = v.shift(1).reindex(D["idx"]).values / 100
    t = I.backtest(D, stop="band")
    for st in ("ATM", "0.5% ITM", "0.3% OTM", "debit 0.5%", "debit 1%", "debit 2%"):
        for im in (1.0, 1.2, 1.5):
            p = pnl(D, t, im, st); p = p[np.isfinite(p)]
            rows.append(dict(sample="2021-26" if tag == "long" else "2019-20 holdout", structure=st, iv=im, n=len(p),
                             avg=p.mean(), pf=p[p > 0].sum() / -p[p < 0].sum(), worst=p.min(), win=(p > 0).mean() * 100))
df = pd.DataFrame(rows)
df.to_csv(Path(__file__).parent / "results" / "structure_QQQ.csv", index=False)
pv = df.pivot_table(index=["structure"], columns=["sample", "iv"], values="pf").round(2)
print("Profit factor by structure (rows) / sample x IV multiplier (cols)\n", pv.to_string())
print("\nAvg $ per trade\n", df.pivot_table(index="structure", columns=["sample", "iv"], values="avg").round(1).to_string())
