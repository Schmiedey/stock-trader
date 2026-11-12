"""Improvement search for the v4 momentum rule with a strict split: choose on 2021-23 (IS), confirm on 2024-26 (OOS).
Shares P&L in bps net of 1bp/trade; option P&L modelled (BS at prior-day vol index x mult, 3% spread, min $0.02).
Usage: python improve.py QQQ"""
import sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent)); sys.path.insert(0, str(Path(__file__).parent))
from zero_dte_scalper.src.greeks import black_scholes_price as bs
from long_scan import load, IX, CACHE, MINS

YEAR_S = 365.25 * 86400


def data(sym, tag="long"):
    import long_scan
    if tag != "long":
        b = pd.read_pickle(CACHE / f"{sym}_1m_{tag}.pkl"); b.to_pickle(CACHE / f"_tmp_1m_long.pkl"); O, C, H, L, years, idx = load("_tmp")
    else:
        b = pd.read_pickle(CACHE / f"{sym}_1m_long.pkl"); O, C, H, L, years, idx = load(sym)
    b["day"] = b.index.date; b["hm"] = b.index.strftime("%H:%M")
    V = b.pivot_table(index="day", columns="hm", values="Volume").reindex(index=idx, columns=MINS).fillna(0).values
    v = pd.read_pickle(CACHE / f"{'VXN' if sym == 'QQQ' else 'VIX'}_1d_long.pkl")["Close"]; v.index = v.index.date
    iv0 = v.shift(1).reindex(idx).values / 100
    return dict(O=O, C=C, H=H, L=L, V=V, years=years, idx=idx, iv0=iv0)


def backtest(D, step=30, mult=1.0, lookback=14, stop="vwap", first="10:00", last_entry="15:30", side=0, max_trades=99,
             cost=1.0, exit_at="15:59", confirm=0):
    O, C, H, L, V, years = D["O"], D["C"], D["H"], D["L"], D["V"], D["years"]
    pc = np.r_[np.nan, C[:-1, -1]]; op = O[:, 0]
    sig = pd.DataFrame(np.abs(C / op[:, None] - 1)).rolling(lookback).mean().shift(1).values * mult
    ub, lb = np.fmax(op, pc), np.fmin(op, pc)
    vwap = np.cumsum((H + L + C) / 3 * V, axis=1) / np.maximum(np.cumsum(V, axis=1), 1e-9)
    checks = list(range(IX[first] - 1, IX[exit_at], step)); xk_last = IX[exit_at] - 1
    T = []
    for d in range(max(lookback, 14) + 1, len(C)):
        pos, e_px, e_k, n, streak = 0, 0.0, 0, 0, 0
        for k in checks:
            px = C[d, k]; up, dn = ub[d] * (1 + sig[d, k]), lb[d] * (1 - sig[d, k])
            if pos:
                if stop == "vwap": s = max(up, vwap[d, k]) if pos > 0 else min(dn, vwap[d, k])
                elif stop == "band": s = up if pos > 0 else dn
                elif stop == "vwap_only": s = vwap[d, k]
                elif stop == "open": s = op[d]
                else: s = -np.inf if pos > 0 else np.inf            # "eod": never
                if (px < s) if pos > 0 else (px > s):
                    T.append((d, pos, e_k, k, e_px, px)); pos = 0
            if pos == 0 and k <= IX[last_entry] and n < max_trades:
                w = 1 if px > up else -1 if px < dn else 0
                streak = streak + 1 if w else 0
                if w and (side == 0 or w == side) and streak > confirm:
                    pos, e_px, e_k = w, px, k; n += 1
        if pos: T.append((d, pos, e_k, xk_last, e_px, C[d, xk_last]))
    t = pd.DataFrame(T, columns=["d", "side", "ek", "xk", "e", "x"])
    t["bps"] = t.side * (t.x / t.e - 1) * 1e4 - cost
    t["year"] = years[t.d.values]
    return t


def opt_pnl(D, t, iv_mult=1.2, moneyness=0.0, spread=0.03):
    """$ per contract. moneyness = strike offset in % of spot, positive = in the money."""
    out = np.empty(len(t))
    for i, r in enumerate(t.itertuples()):
        iv = D["iv0"][r.d] * iv_mult
        if not np.isfinite(iv): out[i] = np.nan; continue
        kind = "call" if r.side > 0 else "put"
        K = round(r.e * (1 - r.side * moneyness / 100))
        a = max(0.01, bs(r.e, K, max(60, (389 - r.ek) * 60) / YEAR_S, iv, option_type=kind))
        b = max(0.01, bs(r.x, K, max(60, (389 - r.xk) * 60) / YEAR_S, iv, option_type=kind))
        sp = lambda m: max(0.02, spread * m)
        out[i] = ((b - sp(b) / 2) - (a + sp(a) / 2)) * 100
    return out


def sharpe(t, D, lo, hi, w=None):
    days = np.arange(len(D["C"])); m = (D["years"] >= lo) & (D["years"] <= hi); m[:15] = False
    x = np.zeros(len(days)); v = t.bps.values * (w[t.d.values] if w is not None else 1.0)
    np.add.at(x, t.d.values, v); x = x[m]
    return x.mean() / x.std() * np.sqrt(252), x.sum() / 100 / (m.sum() / 252)


def row(label, t, D, w=None):
    a, b = sharpe(t, D, 2021, 2023, w), sharpe(t, D, 2024, 2026, w)
    i, o = t[t.year <= 2023], t[t.year >= 2024]
    return (f"{label:<38} IS: n={len(i):4d} {i.bps.mean():5.2f}bp Sharpe {a[0]:5.2f} {a[1]:5.1f}%/y | "
            f"OOS: n={len(o):4d} {o.bps.mean():5.2f}bp Sharpe {b[0]:5.2f} {b[1]:5.1f}%/y")


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "QQQ"
    D = data(sym)
    print(f"== {sym} shares, net 1bp/trade")
    base = backtest(D)
    print(row("BASE (v4 live)", base, D))
    for label, kw in [("long only", dict(side=1)), ("short only", dict(side=-1)),
                      ("stop: band only", dict(stop="band")), ("stop: VWAP only", dict(stop="vwap_only")),
                      ("stop: day open", dict(stop="open")), ("stop: none (hold to close)", dict(stop="eod")),
                      ("1 trade per day max", dict(max_trades=1)), ("2 trades per day max", dict(max_trades=2)),
                      ("entries from 10:30", dict(first="10:30")), ("entries until 14:00", dict(last_entry="14:00")),
                      ("entries until 12:00", dict(last_entry="12:00")),
                      ("need 2 marks outside band", dict(confirm=1)),
                      ("band x0.8", dict(mult=0.8)), ("band x1.2", dict(mult=1.2)), ("band x1.5", dict(mult=1.5)),
                      ("lookback 7d", dict(lookback=7)), ("lookback 30d", dict(lookback=30)), ("lookback 60d", dict(lookback=60)),
                      ("check every 15m", dict(step=15)), ("check every 60m", dict(step=60)),
                      ("long only + hold to close", dict(side=1, stop="eod")), ("long only + VWAP-only stop", dict(side=1, stop="vwap_only"))]:
        print(row(label, backtest(D, **kw), D))
    # regime filters on the base trades (post-hoc, not path dependent)
    iv = D["iv0"]; med = pd.Series(iv).rolling(250, min_periods=60).median().shift(1).values
    dow = np.array([d.weekday() for d in D["idx"]])
    gap = np.abs(D["O"][:, 0] / np.r_[np.nan, D["C"][:-1, -1]] - 1) * 1e4
    rng = pd.Series((D["H"].max(axis=1) - D["L"].min(axis=1)) / D["O"][:, 0]).rolling(14).mean().shift(1).values
    for label, mask in [("vol index above 1y median", iv > med), ("vol index below 1y median", iv <= med),
                        ("gap > 30bp", gap > 30), ("gap <= 30bp", gap <= 30),
                        ("Mon/Fri", (dow == 0) | (dow == 4)), ("Tue-Thu", (dow >= 1) & (dow <= 3))]:
        print(row("only " + label, base[np.nan_to_num(mask[base.d.values]).astype(bool)], D))
    w = np.clip(0.012 / rng, 0.25, 4.0); w = np.nan_to_num(w, nan=1.0)
    print(row("vol-targeted size (x0.25-4)", base, D, w))
    print(f"== {sym} options, $ per contract per trade (IS / OOS), by strike and IV assumption")
    for mny, lab in ((-0.3, "0.3% OTM"), (0.0, "ATM"), (0.5, "0.5% ITM"), (1.0, "1% ITM"), (2.0, "2% ITM")):
        cells = []
        for im in (1.0, 1.2, 1.5):
            p = opt_pnl(D, base, im, mny); i, o = p[base.year.values <= 2023], p[base.year.values >= 2024]
            pf = np.nansum(p[p > 0]) / -np.nansum(p[p < 0])
            cells.append(f"IVx{im}: {np.nanmean(i):6.1f} / {np.nanmean(o):6.1f} PF {pf:4.2f}")
        print(f"  {lab:<9} " + " | ".join(cells))
