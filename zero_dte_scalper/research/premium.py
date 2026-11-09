#!/usr/bin/env python3
"""
0DTE premium-selling test (variance risk premium): sell defined-risk structures intraday,
priced at the prior day's VIX/VXN, settled on the real 16:00 close.

Structures (1 unit, defined risk):
  ifly{W}   short ATM call+put, long wings +/-W points          (iron butterfly)
  ic{D}     short |delta|=D call+put, long wings 3 points further out (iron condor)

Stress knobs:
  iv_mult  0.8 = assume we only collect 80% of VIX-implied premium (0DTE IV is often below VIX)
  cost     per-leg cost (half spread + slippage), default $0.015
  stop_k   close if mark loss >= k x credit (None = hold to settlement)

Output: results/premium.json and a table. IS/OOS split matches edge_scan (2/3 : 1/3 of days).
"""
from __future__ import annotations

import sys
import json
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay as R
from zero_dte_scalper.src.greeks import black_scholes_price, calculate_greeks

ENTRY_TIMES = ["09:45", "10:30", "12:00", "13:30", "14:30"]
STRUCTS = ["ifly5", "ifly3", "ic0.20", "ic0.10"]


def legs_for(struct, spot, t, iv):
    """Return list of (strike, kind, sign) where sign=-1 short, +1 long."""
    atm = round(spot)
    if struct.startswith("ifly"):
        w = int(struct[4:])
        return [(atm, "call", -1), (atm, "put", -1), (atm + w, "call", +1), (atm - w, "put", +1)], w
    d = float(struct[2:])
    tte = R.tte_years(t)
    ks = range(atm - 25, atm + 26)
    kc = min((k for k in ks if k >= atm), key=lambda k: abs(abs(calculate_greeks(spot, k, tte, iv, option_type="call")["delta"]) - d))
    kp = min((k for k in ks if k <= atm), key=lambda k: abs(abs(calculate_greeks(spot, k, tte, iv, option_type="put")["delta"]) - d))
    return [(kc, "call", -1), (kp, "put", -1), (kc + 3, "call", +1), (kp - 3, "put", +1)], 3


def value(legs, spot, t, iv):
    return sum(s * R.option_mid(spot, k, t, iv, kind) for k, kind, s in legs)


def simulate(bars_day: pd.DataFrame, iv: float, entry_hm: str, struct: str, stop_k, cost: float):
    closes = bars_day["Close"]
    close_hm = (bars_day.index + pd.Timedelta(minutes=5)).strftime("%H:%M")
    if entry_hm not in close_hm:
        return None
    i0 = list(close_hm).index(entry_hm)
    t0 = bars_day.index[i0] + pd.Timedelta(minutes=5)
    spot0 = float(closes.iloc[i0])
    legs, width = legs_for(struct, spot0, t0, iv)
    gross = -value(legs, spot0, t0, iv)
    credit = gross - cost * 4
    if credit <= 0.05:
        return None
    max_risk = width - credit
    exit_reason, pnl = "SETTLE", None
    for j in range(i0 + 1, len(bars_day)):
        t = bars_day.index[j] + pd.Timedelta(minutes=5)
        b = bars_day.iloc[j]
        if stop_k is not None and t.strftime("%H:%M") < "15:55":
            # cost to close the structure at the worse wick of this bar
            close_cost = max(-value(legs, float(b["High"]), t, iv), -value(legs, float(b["Low"]), t, iv))
            if close_cost - gross >= stop_k * credit:
                pnl = -stop_k * credit - cost * 4  # stopped at the trigger, pay exit costs
                exit_reason = "STOP"
                break
    if pnl is None:
        s_end = float(closes.iloc[-1])  # 15:55 bar close == 16:00 settle
        payoff = sum(s * max(0.0, (s_end - k) if kind == "call" else (k - s_end)) for k, kind, s in legs)
        pnl = credit + payoff  # payoff is negative for shorts in the money
    return {"credit": round(credit, 3), "pnl": round(pnl * 100, 2), "max_risk": round(max_risk * 100, 2),
            "reason": exit_reason, "spot0": spot0}


def main():
    data = R.load_data(("SPY", "QQQ"), refresh=False)
    days = sorted(set(data["SPY"]["bars"].index.date))[1:]
    split = days[int(len(days) * 2 / 3)]
    grid = list(itertools.product(["SPY", "QQQ"], STRUCTS, ENTRY_TIMES, [None, 2.0, 1.0], [1.0, 0.8]))
    rows = []
    for sym, struct, hm, stop_k, iv_mult in grid:
        bars = data[sym]["bars"]
        vol = data[sym]["vol"]
        trades = []
        for d in days:
            bd = bars[bars.index.date == d]
            if len(bd) < 70:
                continue
            prev = vol[vol.index.date < d]
            if prev.empty:
                continue
            iv = float(prev["Close"].iloc[-1]) / 100.0 * iv_mult
            r = simulate(bd, iv, hm, struct, stop_k, cost=0.015)
            if r:
                r["date"] = str(d)
                trades.append(r)
        def m(ts):
            if not ts:
                return {"n": 0}
            p = np.array([x["pnl"] for x in ts])
            w, l = p[p > 0], p[p < 0]
            return {"n": len(p), "exp": round(p.mean(), 2), "wr": round(len(w) / len(p) * 100, 1),
                    "pf": round(w.sum() / -l.sum(), 2) if len(l) else 99.0, "pnl": round(p.sum(), 0),
                    "worst": round(p.min(), 0), "avg_risk": round(np.mean([x["max_risk"] for x in ts]), 0),
                    "ret_on_risk_pct": round(p.mean() / np.mean([x["max_risk"] for x in ts]) * 100, 2)}
        rows.append({"sym": sym, "struct": struct, "entry": hm, "stop_k": stop_k, "iv_mult": iv_mult,
                     "IS": m([x for x in trades if x["date"] < str(split)]),
                     "OOS": m([x for x in trades if x["date"] >= str(split)]), "ALL": m(trades)})
    out = Path(__file__).resolve().parent / "results" / "premium.json"
    out.write_text(json.dumps({"split": str(split), "rows": rows}, default=str))

    print(f"split={split}  per-unit $ P&L, 1 structure/day")
    print(f"{'sym':4s} {'struct':7s} {'entry':5s} {'stop':>4s} {'ivx':>4s} | {'IS n':>4s} {'IS exp':>7s} {'IS pf':>5s} | {'OOS n':>5s} {'OOS exp':>7s} {'OOS pf':>6s} {'worst':>6s} {'RoR%':>6s}")
    good = [r for r in rows if r["IS"].get("n", 0) >= 20]
    good.sort(key=lambda r: r["IS"]["exp"] / max(1, r["IS"]["avg_risk"]), reverse=True)
    for r in good[:30]:
        a, b = r["IS"], r["OOS"]
        print(f"{r['sym']:4s} {r['struct']:7s} {r['entry']:5s} {str(r['stop_k']):>4s} {r['iv_mult']:4.1f} | {a['n']:4d} {a['exp']:7.1f} {a['pf']:5} | "
              f"{b.get('n',0):5d} {b.get('exp',0):7.1f} {b.get('pf',0):6} {r['ALL']['worst']:6.0f} {b.get('ret_on_risk_pct',0):6.2f}")
    both = [r for r in good if r["IS"]["pf"] > 1.2 and r["OOS"].get("pf", 0) > 1.2 and r["iv_mult"] == 0.8]
    print(f"\nProfitable in BOTH halves even at 80% premium: {len(both)} of {len([r for r in good if r['iv_mult']==0.8])}")


if __name__ == "__main__":
    main()
