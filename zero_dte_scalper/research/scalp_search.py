"""Wide scalp search on 6 years of 1-minute bars: ~30 triggers x 3 sessions x 6 holds (1-15 min), follow or fade.
Selection on 2021-2023 only (|t| > 3), then judged on 2024-2026 (never seen during selection).
Entry = next minute's open after the trigger bar closes; exit = open h minutes later. bps on the underlying.
Usage: python scalp_search.py QQQ  -> prints survivors, saves results/scalp_search_{SYM}.csv"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
from long_scan import load, IX, CACHE, MINS

HOLDS = (1, 2, 3, 5, 10, 15)
SESS = {"open": ("09:35", "10:30"), "mid": ("10:30", "14:00"), "pm": ("14:00", "15:40")}
COST = 0.5   # bps round trip (1c on a $600 ETF is ~0.17bp; this leaves room for slippage)


def ema(x, n):
    a = 2 / (n + 1); out = np.empty_like(x); out[:, 0] = x[:, 0]
    for k in range(1, x.shape[1]):
        out[:, k] = a * x[:, k] + (1 - a) * out[:, k - 1]
    return out


def roll(x, n, fn):
    return getattr(pd.DataFrame(x.T).rolling(n, min_periods=n), fn)().values.T


def rsi(c, n):
    d = np.diff(c, axis=1, prepend=c[:, :1])
    up, dn = ema(np.maximum(d, 0), 2 * n - 1), ema(np.maximum(-d, 0), 2 * n - 1)
    return 100 * up / np.maximum(up + dn, 1e-12)


def triggers(O, C, H, L, V):
    """{name: direction matrix (+1 / -1 / 0)} where the sign is the direction of the triggering move."""
    T = {}
    tp = (H + L + C) / 3
    vwap = np.cumsum(tp * V, axis=1) / np.maximum(np.cumsum(V, axis=1), 1e-9)
    dev = C / vwap - 1
    dsd = np.sqrt(np.cumsum(dev ** 2, axis=1) / np.arange(1, C.shape[1] + 1))       # running intraday sd of VWAP deviation
    z = dev / np.maximum(dsd, 1e-6)
    for k in (2.0, 3.0):
        T[f"vwap_dev>{k}sd"] = np.where(z > k, 1, np.where(z < -k, -1, 0))
    for n, lo in ((2, 5), (2, 1), (5, 10)):
        r = rsi(C, n); T[f"rsi{n}<{lo}"] = np.where(r > 100 - lo, 1, np.where(r < lo, -1, 0))
    m20, s20 = roll(C, 20, "mean"), roll(C, 20, "std")
    for k in (2.0, 3.0):
        T[f"boll20_{k}"] = np.where(C > m20 + k * s20, 1, np.where(C < m20 - k * s20, -1, 0))
    up = (C > O).astype(int); dn = (C < O).astype(int)
    for n in (3, 5, 7):
        T[f"{n}_bars_in_row"] = np.where(roll(up, n, "sum") == n, 1, np.where(roll(dn, n, "sum") == n, -1, 0))
    for n in (15, 30, 60, 120):
        hh = np.roll(roll(H, n, "max"), 1, axis=1); ll = np.roll(roll(L, n, "min"), 1, axis=1)
        T[f"break_{n}m_range"] = np.where(C > hh, 1, np.where(C < ll, -1, 0))
    hod = np.roll(np.maximum.accumulate(H, axis=1), 1, axis=1); lod = np.roll(np.minimum.accumulate(L, axis=1), 1, axis=1)
    T["break_HOD_LOD"] = np.where(C > hod, 1, np.where(C < lod, -1, 0))
    T["break_HOD_LOD"][:, :6] = 0
    e9, e21, e50 = ema(C, 9), ema(C, 21), ema(C, 50)
    T["ema9x21"] = np.where(e9 > e21, 1, -1)
    T["price_x_vwap"] = np.where(C > vwap, 1, -1)
    T["price_x_ema50"] = np.where(C > e50, 1, -1)
    rng = H - L; arng = roll(rng, 20, "mean"); av = roll(V, 20, "mean")
    body = C - O
    for k in (2.5, 4.0):
        T[f"wide_bar>{k}x"] = np.where((rng > k * arng) & (body > 0), 1, np.where((rng > k * arng) & (body < 0), -1, 0))
    T["vol_spike>3x"] = np.where((V > 3 * av) & (body > 0), 1, np.where((V > 3 * av) & (body < 0), -1, 0))
    T["vol_spike>5x"] = np.where((V > 5 * av) & (body > 0), 1, np.where((V > 5 * av) & (body < 0), -1, 0))
    # trend pullback: trend by EMAs+VWAP, bar tags EMA21 and closes back with the trend
    T["pullback_ema21"] = np.where((e9 > e21) & (C > vwap) & (L <= e21) & (C > e21) & (body > 0), 1,
                                   np.where((e9 < e21) & (C < vwap) & (H >= e21) & (C < e21) & (body < 0), -1, 0))
    T["pullback_vwap"] = np.where((e9 > e21) & (L <= vwap) & (C > vwap) & (body > 0), 1,
                                  np.where((e9 < e21) & (H >= vwap) & (C < vwap) & (body < 0), -1, 0))
    # rejection wicks (pin bars): long lower wick = up signal
    lw, uw = np.minimum(O, C) - L, H - np.maximum(O, C)
    T["pin_bar"] = np.where((lw > 2 * np.abs(body)) & (lw > 2 * uw) & (rng > 1.5 * arng), 1,
                            np.where((uw > 2 * np.abs(body)) & (uw > 2 * lw) & (rng > 1.5 * arng), -1, 0))
    # engulfing
    pb = np.roll(body, 1, axis=1)
    T["engulfing"] = np.where((body > 0) & (pb < 0) & (body > 1.5 * -pb) & (rng > arng), 1,
                              np.where((body < 0) & (pb > 0) & (-body > 1.5 * pb) & (rng > arng), -1, 0))
    # opening range (5/15/30) first break
    for n in (5, 15, 30):
        oh, ol = H[:, :n].max(axis=1)[:, None], L[:, :n].min(axis=1)[:, None]
        t = np.where(C > oh, 1, np.where(C < ol, -1, 0)); t[:, :n] = 0
        T[f"orb{n}_break"] = t
    # 5-minute momentum burst vs prior-20-day vol at this time of day
    r5 = np.zeros_like(C); r5[:, 5:] = C[:, 5:] / C[:, :-5] - 1
    vol = pd.DataFrame(np.abs(r5)).rolling(20, min_periods=15).median().shift(1).values * 1.4826
    zz = r5 / np.where(vol > 0, vol, np.nan)
    for k in (2.0, 3.0):
        T[f"burst5m>{k}z"] = np.nan_to_num(np.where(zz > k, 1, np.where(zz < -k, -1, 0)))
    # first-5-minute candle direction, entered at 09:35
    f5 = np.zeros_like(C); f5[:, 4] = np.sign(C[:, 4] - O[:, 0]); T["first5m_candle"] = f5
    return T


def edge(t):
    """Keep only the first bar of each trigger run (state changes), per day."""
    prev = np.roll(t, 1, axis=1); prev[:, 0] = 0
    return np.where(t != prev, t, 0)


def scan(sym):
    O, C, H, L, years, idx = load(sym)
    b = pd.read_pickle(CACHE / f"{sym}_1m_long.pkl"); b["day"] = b.index.date; b["hm"] = b.index.strftime("%H:%M")
    V = b.pivot_table(index="day", columns="hm", values="Volume").reindex(index=idx, columns=MINS).fillna(0).values
    T = triggers(O, C, H, L, V)
    IS = years <= 2023
    rows = []
    for name, t in T.items():
        t = edge(np.nan_to_num(t))
        for sn, (a, z) in SESS.items():
            if name == "first5m_candle" and sn != "open": continue
            lo, hi = (IX["09:34"] if name == "first5m_candle" else IX[a]), IX[z]
            for h in HOLDS:
                seg = t[:, lo:hi]
                di, mi = np.nonzero(seg); mi = mi + lo
                ok = mi + 1 + h < 390; di, mi = di[ok], mi[ok]
                if len(di) < 200: continue
                r = t[di, mi] * (O[di, mi + 1 + h] / O[di, mi + 1] - 1) * 1e4
                def st(x):
                    return (x.mean(), x.mean() / (x.std(ddof=1) / np.sqrt(len(x))), len(x)) if len(x) > 30 else (0, 0, len(x))
                (m1, t1, n1), (m2, t2, n2) = st(r[IS[di]]), st(r[~IS[di]])
                rows.append(dict(trigger=name, sess=sn, hold=h, n_is=n1, is_bps=m1, is_t=t1, n_oos=n2, oos_bps=m2, oos_t=t2))
    return pd.DataFrame(rows), (O, C, H, L, V, years, idx, T)


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "QQQ"
    df, _ = scan(sym)
    df.to_csv(Path(__file__).parent / "results" / f"scalp_search_{sym}.csv", index=False)
    print(f"{sym}: {len(df)} trigger x session x hold combos tested. IS=2021-23, OOS=2024-26. + = follow the trigger, - = fade it")
    sel = df[df.is_t.abs() > 3].copy()
    sel["same_sign"] = np.sign(sel.is_bps) == np.sign(sel.oos_bps)
    sel["oos_net"] = sel.oos_bps.abs() * sel.same_sign - COST - (~sel.same_sign) * sel.oos_bps.abs()
    print(f"selected in-sample (|t|>3): {len(sel)} (chance alone would give ~{len(df) * 0.0027:.0f}); "
          f"same sign out-of-sample: {int(sel.same_sign.sum())}; OOS |t|>2 & same sign: {int(((sel.oos_t.abs() > 2) & sel.same_sign).sum())}; "
          f"OOS net of {COST}bp cost > 0: {int((sel.oos_net > 0).sum())}")
    pd.set_option("display.width", 220)
    print(sel.sort_values("oos_net", ascending=False).head(25).round(2).to_string(index=False))
