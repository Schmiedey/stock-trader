"""Intraday noise-band momentum (Zarattini, Aziz & Barbon 2024, "Beat the Market") on multi-year 1-minute bars.
Go with the move once price leaves the band open*(1 +/- avg |move from open| at this time of day over 14 days);
flat when it comes back inside (or crosses VWAP). Reports shares P&L (bps/day) and a 0DTE ATM option expression.
Usage: python momentum.py SPY"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent)); sys.path.insert(0, str(Path(__file__).parent))
from zero_dte_scalper.src.greeks import black_scholes_price as bs
from long_scan import load, IX, CACHE

YEAR_S = 365.25 * 86400


def backtest(O, C, H, L, V, years, iv0=None, step=30, mult=1.0, lookback=14, use_vwap=True, first="10:00",
             last_entry="15:30", cost=1.0, iv_mult=1.2, side_filter=0):
    pc = np.r_[np.nan, C[:-1, -1]]; op = O[:, 0]
    sig = pd.DataFrame(np.abs(C / op[:, None] - 1)).rolling(lookback).mean().shift(1).values * mult
    ub, lb = np.fmax(op, pc), np.fmin(op, pc)
    tp = (H + L + C) / 3
    vwap = np.cumsum(tp * V, axis=1) / np.maximum(np.cumsum(V, axis=1), 1e-9)
    checks = list(range(IX[first] - 1, 389, step))
    day_bps = np.zeros(len(C)); trades = []
    for d in range(lookback + 1, len(C)):
        pos, e_px, e_k, e_opt, strike = 0, 0.0, 0, 0.0, 0
        def opt(px, k, kind, K):
            T = max(60, (390 - k - 1) * 60) / YEAR_S
            return max(0.01, bs(px, K, T, iv0[d] * iv_mult, option_type=kind))
        def close(k, px):
            nonlocal pos
            r = pos * (px / e_px - 1) * 1e4 - cost
            day_bps[d] += r
            o_pnl = np.nan
            if iv0 is not None and np.isfinite(iv0[d]):
                x = opt(px, k, "call" if pos > 0 else "put", strike)
                spr = lambda m: max(0.02, 0.03 * m)
                o_pnl = (x - spr(x) / 2) - (e_opt + spr(e_opt) / 2)
            trades.append((d, years[d], pos, e_k, k, r, o_pnl, e_opt))
            pos = 0
        for k in checks:
            px = C[d, k]
            up, dn = ub[d] * (1 + sig[d, k]), lb[d] * (1 - sig[d, k])
            if use_vwap:
                up_x, dn_x = max(up, vwap[d, k]), min(dn, vwap[d, k])
            else:
                up_x, dn_x = up, dn
            if pos > 0 and px < up_x: close(k, px)
            elif pos < 0 and px > dn_x: close(k, px)
            if pos == 0 and k <= IX[last_entry]:
                want = 1 if px > up else -1 if px < dn else 0
                if want and (side_filter == 0 or want == side_filter):
                    pos, e_px, e_k, strike = want, px, k, round(px)
                    if iv0 is not None and np.isfinite(iv0[d]):
                        e_opt = opt(px, k, "call" if want > 0 else "put", strike)
        if pos: close(388, C[d, 388])       # flat by 15:59
    return day_bps, pd.DataFrame(trades, columns=["d", "year", "side", "ek", "xk", "bps", "opt", "eopt"])


def report(label, day_bps, tr, years):
    ys = sorted(set(years)); x = day_bps[15:]; yy = years[15:]
    sh = x.mean() / x.std() * np.sqrt(252)
    eq = np.cumsum(x); dd = (eq - np.maximum.accumulate(eq)).min()
    by = " ".join(f"{x[yy == y].sum() / 100:6.1f}" for y in ys)
    print(f"{label:<34} trades={len(tr):4d} ({len(tr) / len(x):.2f}/day, hold {((tr.xk - tr.ek).mean()):4.0f}m) win={(tr.bps > 0).mean() * 100:4.1f}% "
          f"avg={tr.bps.mean():5.2f}bp Sharpe={sh:5.2f} maxDD={dd / 100:6.1f}% | %/yr unlevered: {by}")


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    O, C, H, L, years, idx = load(sym)
    b = pd.read_pickle(CACHE / f"{sym}_1m_long.pkl"); b["day"] = b.index.date; b["hm"] = b.index.strftime("%H:%M")
    V = b.pivot_table(index="day", columns="hm", values="Volume").reindex(index=idx, columns=pd.date_range("09:30", "15:59", freq="1min").strftime("%H:%M")).fillna(0).values
    v = pd.read_pickle(CACHE / f"{'VIX' if sym != 'QQQ' else 'VXN'}_1d_long.pkl")["Close"]; v.index = v.index.date
    iv0 = v.shift(1).reindex(idx).values / 100
    print(f"== {sym} {idx[0]} -> {idx[-1]} | shares, net of 1bp per trade | years {sorted(set(years))}")
    base = None
    for label, kw in [("base 30m, band+VWAP stop", {}), ("band stop only", {"use_vwap": False}),
                      ("check every 15m", {"step": 15}), ("check every 5m", {"step": 5}), ("check every 1m", {"step": 1}),
                      ("band x0.7", {"mult": 0.7}), ("band x1.3", {"mult": 1.3}), ("lookback 30d", {"lookback": 30}),
                      ("long only", {"side_filter": 1}), ("short only", {"side_filter": -1}),
                      ("entries until 13:00 only", {"last_entry": "13:00"}), ("cost 3bp", {"cost": 3.0})]:
        db, tr = backtest(O, C, H, L, V, years, iv0, **kw)
        report(label, db, tr, years)
        if base is None: base = tr
    print("-- 0DTE ATM option expression of base rule ($ per contract-share, incl. 3% spread), by IV assumption")
    for im in (1.0, 1.2, 1.4):
        db, tr = backtest(O, C, H, L, V, years, iv0, iv_mult=im)
        t = tr.dropna(subset=["opt"]); pf = t.opt[t.opt > 0].sum() / -t.opt[t.opt < 0].sum()
        by = " ".join(f"{t.opt[t.year == y].sum():7.1f}" for y in sorted(set(years)))
        print(f"  IV x{im}: n={len(t)} avg entry ${t.eopt.mean():.2f} avg pnl ${t.opt.mean():+.3f} win={(t.opt > 0).mean() * 100:.0f}% PF={pf:.2f} | $/yr per 1 contract-share: {by}")
