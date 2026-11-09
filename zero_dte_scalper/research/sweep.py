#!/usr/bin/env python3
"""
Walk-forward sweep: entry rules x exit structures through the options replay.

Selection uses in-sample (IS) days only. Out-of-sample (OOS) days are reported, never tuned on.
Output: results/sweep.json (every run) and a ranked table on stdout.
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
import edge_scan as E

OUT = Path(__file__).resolve().parent / "results"

EXITS = {
    "stop": {"prem20": {"stop_mode": "premium", "stop_loss_pct": 0.20},
             "prem35": {"stop_mode": "premium", "stop_loss_pct": 0.35},
             "under": {"stop_mode": "underlying", "stop_loss_pct": 0.35, "hard_stop_pct": 0.60}},
    "targets": {"30/80s": {"take_profit_1_pct": 0.30, "take_profit_2_pct": 0.80, "tp1_scale_out": True},
                "50/150s": {"take_profit_1_pct": 0.50, "take_profit_2_pct": 1.50, "tp1_scale_out": True},
                "50/100": {"take_profit_1_pct": 0.50, "take_profit_2_pct": 1.00, "tp1_scale_out": False},
                "100/300": {"take_profit_1_pct": 1.00, "take_profit_2_pct": 3.00, "tp1_scale_out": False}},
    "hold": {"20m": {"max_hold_time_minutes": 20}, "60m": {"max_hold_time_minutes": 60},
             "eod": {"max_hold_time_minutes": 400}},
    "inval": {"on": {"underlying_invalidation_exit": True}, "off": {"underlying_invalidation_exit": False}},
    # option pricing: x1.2 matches observed real 0DTE straddle prices vs VIX; rank on that
    "iv": {"x1.0": {"replay_iv_mult": 1.0}, "x1.2": {"replay_iv_mult": 1.2}},
}

# Entry windows wide open for research rules (each rule carries its own time-of-day logic);
# live-engine variants keep the live session windows.
OPEN_HOURS = {"trading_hours": {"timezone": "America/New_York", "market_open": "09:30",
                                "prime_morning_start": "09:45", "prime_morning_end": "15:30",
                                "midday_chop_start": "15:30", "midday_chop_end": "15:30",
                                "afternoon_window_start": "15:30", "afternoon_window_end": "15:30",
                                "mandatory_exit_time": "15:50", "market_close": "16:00"}}

RULES_KEEP = ["orb15_break", "orb30_break", "orb15_fail_fade", "pdh_pdl_break", "pm_hod_lod_break",
              "trendday_pullback", "trendday_pullback_pm", "mr_vwap_stretch", "vwap_reclaim", "gap_go",
              "trend_drift_1030", "intraday_mom_1400"]


def rule_sigs(frames: dict, rules: dict, name: str) -> dict:
    out = {}
    for sym, f in frames.items():
        s = rules[sym][name]
        d = {}
        for idx in s.index[s != 0]:
            row = f.loc[idx]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            px, atr = float(row["Close"]), float(row["ATR"]) if not np.isnan(row["ATR"]) else 0.5
            long = s.loc[idx] > 0 if not isinstance(s.loc[idx], pd.Series) else s.loc[idx].iloc[0] > 0
            d[idx + pd.Timedelta(minutes=5)] = {
                "signal": "CALL" if long else "PUT", "conviction": "RULE", "primary_setup": name,
                "current_price": px, "underlying_stop": px - atr if long else px + atr,
            }
        out[sym] = d
    return out


def split_metrics(trades, split_day):
    is_t = [t for t in trades if t["date"] < str(split_day)]
    oos_t = [t for t in trades if t["date"] >= str(split_day)]
    return R.metrics(is_t), R.metrics(oos_t)


def main():
    data = R.load_data(("SPY", "QQQ"), refresh=False)
    frames = {s: E.feature_frame(s, data[s]["bars"], data[s]["daily"]) for s in ("SPY", "QQQ")}
    rules = {s: E.candidate_rules(frames[s]) for s in frames}
    days = sorted(set(frames["SPY"]["day"]))
    split_day = days[int(len(days) * 2 / 3)]

    entries = {name: ("rule", rule_sigs(frames, rules, name)) for name in RULES_KEEP}
    entries.update({"live_engine": ("live", {}), "live_score1": ("live", {"min_score": 1}),
                    "live_no_sweep": ("live", {"setups": ("orb", "pullback", "macd")})})

    runs = []
    combos = list(itertools.product(*[EXITS[k].items() for k in EXITS]))
    print(f"{len(entries)} entries x {len(combos)} exit combos = {len(entries) * len(combos)} runs, split={split_day}")
    for ename, (kind, payload) in entries.items():
        for combo in combos:
            cfg, label = {}, []
            for (lab, over) in combo:
                cfg.update(over)
                label.append(lab)
            if kind == "rule":
                cfg.update(OPEN_HOURS)
                r = R.run(data=data, config_overrides=cfg, sigs_override=payload)
            else:
                r = R.run(data=data, config_overrides=cfg, signal_params=payload)
            m_is, m_oos = split_metrics(r["trades"], split_day)
            runs.append({"entry": ename, "exit": "/".join(label), "cfg": cfg if kind == "live" else
                         {k: v for k, v in cfg.items() if k != "trading_hours"},
                         "IS": m_is, "OOS": m_oos, "ALL": r["metrics"]})
        best = max((x for x in runs if x["entry"] == ename and x["exit"].endswith("x1.2")),
                   key=lambda x: x["IS"].get("expectancy") or -1e9)
        print(f"  {ename:22s} best IS exp {best['IS'].get('expectancy')} ({best['exit']}) -> OOS exp {best['OOS'].get('expectancy')}")

    (OUT / "sweep.json").write_text(json.dumps({"split_day": str(split_day), "runs": runs}, default=str))

    ok = [x for x in runs if x["IS"].get("trades", 0) >= 15 and x["OOS"].get("trades", 0) >= 8
          and x["exit"].endswith("x1.2")]
    ok.sort(key=lambda x: x["IS"]["expectancy"], reverse=True)
    print("\nTop 25 by IN-SAMPLE expectancy at realistic pricing (iv x1.2); OOS shown for honesty:")
    print(f"{'entry':22s} {'exit':28s} | {'IS n':>4s} {'IS exp':>7s} {'IS pf':>5s} | {'OOS n':>5s} {'OOS exp':>7s} {'OOS pf':>6s} {'OOS wr':>6s}")
    for x in ok[:25]:
        a, b = x["IS"], x["OOS"]
        print(f"{x['entry']:22s} {x['exit']:28s} | {a['trades']:4d} {a['expectancy']:7.1f} {a['profit_factor']:5} | "
              f"{b['trades']:5d} {b['expectancy']:7.1f} {b['profit_factor']:6} {b['win_rate']:6.1f}")
    both = [x for x in ok if x["IS"]["profit_factor"] > 1.15 and x["OOS"]["profit_factor"] > 1.15]
    print(f"\nRuns profitable (PF>1.15) in BOTH halves: {len(both)} of {len(ok)}")
    for x in sorted(both, key=lambda x: min(x['IS']['profit_factor'], x['OOS']['profit_factor']), reverse=True)[:15]:
        print(f"  {x['entry']:22s} {x['exit']:28s} IS pf {x['IS']['profit_factor']} n={x['IS']['trades']} | OOS pf {x['OOS']['profit_factor']} n={x['OOS']['trades']} exp {x['OOS']['expectancy']}")


if __name__ == "__main__":
    main()
