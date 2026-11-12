"""6-year check of the afternoon iron-fly (variance risk premium) using 1-minute bars.
Credit is modelled as Black-Scholes at prior-day VIX x mult (calendar time), which the live IV-calibration log
shows is ~1.0x the real SPY straddle at 13:00-13:30. Payoff uses the real 15:50 price. Per-share $ by year."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from zero_dte_scalper.src.greeks import black_scholes_price as bs
from long_scan import load, IX, CACHE

def run(sym="SPY", vix_name="VIX", leg_cost=0.015):
    O, C, H, L, years, idx = load(sym)
    v = pd.read_pickle(CACHE / f"{vix_name}_1d_long.pkl")["Close"]; v.index = v.index.date
    iv0 = v.shift(1).reindex(idx).values / 100.0
    ys = sorted(set(years))
    print(f"{sym}: iron fly, wings 0.4% of spot, exit 15:50, cost ${leg_cost}/leg each way. mean $/share per day by year {ys}")
    for en in ("10:00", "12:00", "13:30", "14:30"):
        for mult in (0.85, 1.0):
            pnl = np.full(len(C), np.nan); cred = np.full(len(C), np.nan)
            mins_left = 390 - IX[en]
            for d in range(len(C)):
                if not np.isfinite(iv0[d]): continue
                s = O[d, IX[en]]; k = round(s); w = max(2, round(0.004 * s)); iv = iv0[d] * mult
                T = mins_left * 60 / (365.25 * 86400); T2 = 600 / (365.25 * 86400)
                def val(sp, t):
                    return (bs(sp, k, t, iv, option_type="call") + bs(sp, k, t, iv, option_type="put")
                            - bs(sp, k + w, t, iv, option_type="call") - bs(sp, k - w, t, iv, option_type="put"))
                c = val(s, T); x = val(C[d, IX["15:49"]], T2)
                cred[d] = c; pnl[d] = c - x - 8 * leg_cost
            m = ~np.isnan(pnl); x = pnl[m]
            t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x))); pf = x[x > 0].sum() / -x[x < 0].sum()
            by = " ".join(f"{x[years[m] == y].mean():6.3f}" for y in ys)
            print(f"  {en} iv x{mult:<4} n={len(x)} credit={np.nanmean(cred):.2f} mean={x.mean():6.3f} t={t:5.2f} win={(x > 0).mean() * 100:4.1f}% PF={pf:4.2f} | {by}")

if __name__ == "__main__":
    import yfinance as yf
    for n in ("VIX", "VXN"):
        p = CACHE / f"{n}_1d_long.pkl"
        if not p.exists():
            d = yf.download(f"^{n}", start="2020-12-01", progress=False, auto_adjust=False)
            if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.get_level_values(0)
            d.to_pickle(p)
    run("SPY", "VIX")
