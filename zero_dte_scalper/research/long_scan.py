"""Multi-year 1-minute edge scan (data from fetch_duka.py). Everything is in bps on the underlying, by year.

Sections: A close-fade, B time-of-day profile, C extreme-move follow-through, D noise-band momentum, E ORB-failure fade.
Usage: python long_scan.py SPY,QQQ [A,B,C,D,E]
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
MINS = pd.date_range("09:30", "15:59", freq="1min").strftime("%H:%M")
IX = {m: i for i, m in enumerate(MINS)}


def load(sym):
    b = pd.read_pickle(CACHE / f"{sym}_1m_long.pkl")
    b["day"] = b.index.date
    b["hm"] = b.index.strftime("%H:%M")
    C = b.pivot_table(index="day", columns="hm", values="Close").reindex(columns=MINS)
    O = b.pivot_table(index="day", columns="hm", values="Open").reindex(columns=MINS)
    H = b.pivot_table(index="day", columns="hm", values="High").reindex(columns=MINS)
    L = b.pivot_table(index="day", columns="hm", values="Low").reindex(columns=MINS)
    ok = C.notna().sum(axis=1) >= 385          # full sessions only (drops half days)
    C, O, H, L = (x[ok] for x in (C, O, H, L))
    C = C.ffill(axis=1).bfill(axis=1)
    O = O.fillna(C.shift(1, axis=1)).bfill(axis=1)
    H = H.fillna(C); L = L.fillna(C)
    years = np.array([d.year for d in C.index])
    return O.values, C.values, H.values, L.values, years, C.index


def line(label, r, years, cost=0.0):
    """r: per-trade gross bps (nan = no trade)."""
    m = ~np.isnan(r)
    x = r[m] - cost
    if len(x) < 10:
        return f"{label:<40} n={len(x)}"
    t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
    pf = x[x > 0].sum() / max(-x[x < 0].sum(), 1e-9)
    by = " ".join(f"{x[years[m] == y].mean():6.2f}" if (years[m] == y).sum() >= 5 else "     ." for y in sorted(set(years)))
    return f"{label:<40} n={len(x):5d} mean={x.mean():6.2f} t={t:6.2f} hit={(x > 0).mean() * 100:4.1f}% PF={pf:4.2f} | {by}"


def sec_A(O, C, H, L, years, cost):
    pc = np.r_[np.nan, C[:-1, -1]]
    print(f"A. CLOSE FADE: fade sign(prev close -> entry). gross bps unless noted; years: {sorted(set(years))}")
    for en in ("15:30", "15:45", "15:49", "15:50", "15:51", "15:52"):
        for ex in ("15:54", "15:55", "15:57", "15:59c"):
            px = C[:, -1] if ex == "15:59c" else O[:, IX[ex]]
            e = O[:, IX[en]]
            r = -np.sign(e / pc - 1) * (px / e - 1) * 1e4
            print(line(f"  {en}->{ex}", r, years))
    e, px = O[:, IX["15:51"]], O[:, IX["15:55"]]
    day = (e / pc - 1) * 1e4
    base = -np.sign(day) * (px / e - 1) * 1e4
    print(line(f"  15:51->15:55 NET {cost}bp", base, years, cost))
    print(line("    up days", np.where(day > 0, base, np.nan), years))
    print(line("    down days", np.where(day < 0, base, np.nan), years))
    for lo, hi in ((0, 20), (20, 50), (50, 100), (100, 9999)):
        print(line(f"    |day move| {lo}-{hi}bp", np.where((abs(day) >= lo) & (abs(day) < hi), base, np.nan), years))
    intr = (e / O[:, 0] - 1) * 1e4
    print(line("    signal = open->15:51 instead", -np.sign(intr) * (px / e - 1) * 1e4, years))
    agree = np.sign(intr) == np.sign(day)
    print(line("    day & intraday signs agree", np.where(agree, base, np.nan), years))
    l30 = (e / O[:, IX["15:20"]] - 1)
    print(line("    last 30m WITH day move", np.where(np.sign(l30) == np.sign(day), base, np.nan), years))
    print(line("    last 30m AGAINST day move", np.where(np.sign(l30) != np.sign(day), base, np.nan), years))
    # reaction to the 15:50 minute itself (imbalance print): continue or fade it?
    imb = (O[:, IX["15:51"]] / O[:, IX["15:50"]] - 1)
    print(line("    follow 15:50 minute's direction", np.sign(imb) * (px / e - 1) * 1e4, years))


def sec_B(O, C, H, L, years):
    pc = np.r_[np.nan, C[:-1, -1]]
    print("B. TIME-OF-DAY PROFILE (5-min buckets): momentum-signed bps; shown if |t|>3 and same sign in >=80% of years")
    ys = sorted(set(years))
    for name, ref in (("prev close", lambda k: pc), ("day open", lambda k: O[:, 0]),
                      ("30m ago", lambda k: O[:, max(k - 30, 0)]), ("5m ago", lambda k: O[:, max(k - 5, 0)])):
        out = []
        for k in range(5, 385, 5):
            nxt = O[:, k + 5] if k + 5 < 390 else C[:, -1]
            r = np.sign(O[:, k] / ref(k) - 1) * (nxt / O[:, k] - 1) * 1e4
            m = ~np.isnan(r) & (r != 0)
            x = r[m]
            t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
            same = np.mean([np.sign(x[years[m] == y].mean()) == np.sign(x.mean()) for y in ys])
            if abs(t) > 3 and same >= 0.8:
                out.append(f"{MINS[k]}:{x.mean():+.2f}(t{t:+.1f})")
        print(f"  signal=move since {name:<10}: " + ("  ".join(out) if out else "none"))


def sec_C(O, C, H, L, years):
    print("C. EXTREME 5-MIN MOVES: continuation-signed forward bps after |z|>thr (z vs same-time-of-day vol, prior 20d)")
    r5 = np.full(C.shape, np.nan)
    r5[:, 5:] = C[:, 5:] / C[:, :-5] - 1
    vol = pd.DataFrame(np.abs(r5)).rolling(20, min_periods=15).median().shift(1).values * 1.4826
    z = r5 / vol
    Y = np.repeat(years[:, None], C.shape[1], axis=1)
    for lo, hi, nm in ((IX["09:40"], IX["10:30"], "open"), (IX["10:30"], IX["14:00"], "midday"), (IX["14:00"], IX["15:40"], "pm")):
        for thr in (2.5, 4.0):
            for h in (1, 5, 15, 30):
                sel = np.zeros(C.shape, bool)
                sel[:, lo:hi] = np.abs(z[:, lo:hi]) > thr
                # de-cluster: only first trigger in any 5-min run
                prev = np.zeros_like(sel); prev[:, 1:] = sel[:, :-1]
                for s in range(2, 6):
                    prev[:, s:] |= sel[:, :-s]
                sel &= ~prev
                di, mi = np.where(sel)
                ok = mi + 1 + h < 390
                di, mi = di[ok], mi[ok]
                r = np.sign(z[di, mi]) * (O[di, mi + 1 + h] / O[di, mi + 1] - 1) * 1e4
                print(line(f"  {nm:<6} |z|>{thr} fwd {h:2d}m", r, Y[di, mi]))


def sec_D(O, C, H, L, years, cost=1.0):
    print(f"D. NOISE-BAND MOMENTUM (30-min checks, 14d lookback), per-day bps net of {cost}bp/trade")
    pc = np.r_[np.nan, C[:-1, -1]]
    op = O[:, 0]
    checks = [IX[m] - 1 for m in pd.date_range("10:00", "15:30", freq="30min").strftime("%H:%M")]  # close of prior minute
    move = np.abs(C / op[:, None] - 1)
    sig = pd.DataFrame(move).rolling(14).mean().shift(1).values
    ub, lb = np.fmax(op, pc), np.fmin(op, pc)
    for mult in (0.8, 1.0, 1.3):
        for mode in ("band", "open", "eod"):
            res = np.full(len(C), np.nan)
            for d in range(15, len(C)):
                pos, entry, pnl, n = 0, 0.0, 0.0, 0
                for k in checks:
                    px = C[d, k]
                    up, dn = ub[d] * (1 + mult * sig[d, k]), lb[d] * (1 - mult * sig[d, k])
                    want = 1 if px > up else -1 if px < dn else 0
                    if want == 0 and pos != 0:
                        if mode == "eod" or (mode == "open" and (px - op[d]) * pos > 0):
                            want = pos
                    if want != pos:
                        if pos:
                            pnl += pos * (px / entry - 1) * 1e4 - cost; n += 1
                        pos, entry = want, px
                if pos:
                    pnl += pos * (C[d, -1] / entry - 1) * 1e4 - cost; n += 1
                if n:
                    res[d] = pnl
            print(line(f"  x{mult} exit={mode}", res, years))


def sec_E(O, C, H, L, years):
    print("E. ORB(15m) FAILURE FADE on 5-min closes, 09:45-11:30: fade-signed forward bps from next bar open")
    c5 = C[:, 4::5]                      # 5-min bar closes (78)
    orh, orl = H[:, :15].max(axis=1), L[:, :15].min(axis=1)
    for h, nm in ((3, "15m"), (6, "30m"), (12, "60m"), (None, "EOD")):
        res, yy = [], []
        for d in range(len(C)):
            for b in range(3, 24):       # bar b closes at 9:30+5(b+1); 9:45..11:30
                side = 0
                if c5[d, b - 1] > orh[d] and c5[d, b] < orh[d]: side = -1
                if c5[d, b - 1] < orl[d] and c5[d, b] > orl[d]: side = 1
                if side:
                    e = c5[d, b]
                    x = C[d, -6] if h is None else c5[d, min(b + h, 77)]
                    res.append(side * (x / e - 1) * 1e4); yy.append(years[d])
                    break                # first signal of the day only
        print(line(f"  first fail of day, fwd {nm}", np.array(res), np.array(yy)))


if __name__ == "__main__":
    syms = sys.argv[1].split(",") if len(sys.argv) > 1 else ["SPY", "QQQ"]
    secs = sys.argv[2].split(",") if len(sys.argv) > 2 else list("ABCDE")
    for s in syms:
        O, C, H, L, years, idx = load(s)
        print(f"\n######## {s}: {len(C)} full sessions {idx[0]} -> {idx[-1]} ########")
        if "A" in secs: sec_A(O, C, H, L, years, 0.5)
        if "B" in secs: sec_B(O, C, H, L, years)
        if "C" in secs: sec_C(O, C, H, L, years)
        if "D" in secs: sec_D(O, C, H, L, years)
        if "E" in secs: sec_E(O, C, H, L, years)
