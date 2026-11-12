# 0DTE Edge Research — v4.0 (2026-11-12): the 6-year test

**Data:** SPY + QQQ **1-minute bars, 2021-01-04 → 2026-11-11** (~1,445 full sessions each; `fetch_duka.py`), plus Yahoo 1h (2.9 years) and 5m/1m (60/20 days). v3 below was fitted on 58 days. This is 25× more data and it overturns v3.

## Verdict

| Idea | Sample | Result |
|---|---|---|
| ORB-failure fade (v3 live scalper) | 968 SPY / 988 QQQ signals | **No edge.** 30-min follow-through −0.01 bp (t −0.0) SPY, +0.8 bp (t 0.7) QQQ. The v3 PF 2.2 was 58-day noise. |
| SPY iron fly 13:30 (v3 live seller) | 1,441 days, modelled credit | **No edge.** PF 0.73 at 1.0× VIX credit (the level the live IV log shows at 13:00), negative in 2022–2024. About zero before costs; the 8 leg-fills eat it. Only 2025–26 look fine. |
| Close fade 15:50→15:55 | 1,440 days | **No edge.** Looked like t≈3 on the last 60 days; −1.1 bp net over 6 years. |
| Fade/follow extreme 1–5 min moves (true scalps, 1–30 min holds) | 600–7,000 events per bucket | **Nothing** beyond ±0.6 bp, inside the spread. |
| Time-of-day drift, gap, first-hour, last-half-hour rules | 6 years | Nothing stable (last-30-min reversal 2024–26 only; it was momentum in 2022). |
| **Noise-band intraday momentum** | 1,395 QQQ / 1,406 SPY trades | **Edge.** QQQ: +4.0 bp/trade net of 1 bp cost, Sharpe 1.10, **positive in all 6 years** (5.8 / 15.6 / 13.5 / 7.5 / 8.0 / 4.7 % per year, unlevered), max drawdown −7.7%. SPY: Sharpe 0.74, 5 of 6 years (2026: −1.8%). |

**There is no scalping edge in SPY/QQQ price bars.** The faster the rule trades, the worse it does: the same momentum rule checked every 30 min has Sharpe 1.10 on QQQ; every 5 min 0.42; every minute −0.87. v4 therefore trades about once a day and holds about two hours.

## v4 rule (live in paper: `src/momentum.py`)

After Zarattini, Aziz & Barbon (2024), "Beat the Market: An Effective Intraday Momentum Strategy". Every 30 minutes from 10:00 to 15:30 ET, on the completed 5-minute bar:

- `sigma(t)` = average |price(t) / day open − 1| at that time of day over the last 14 sessions
- `upper = max(open, prev close) × (1 + sigma)`, `lower = min(open, prev close) × (1 − sigma)`
- price > upper → buy an ATM 0DTE call; price < lower → buy an ATM 0DTE put
- exit when price is back under max(upper, VWAP) (calls) / above min(lower, VWAP) (puts), or at 15:50. No profit target, no premium stop (−95% backstop only).
- QQQ first, SPY only if QQQ has no position; one position at a time.

0DTE option expression (Black-Scholes at prior-day VXN/VIX × multiplier, 3% spread), QQQ:

| IV multiplier | PF | Years positive | Avg P&L per contract |
|---|---|---|---|
| 1.0× | 1.75 | 6 of 6 | +$25 |
| 1.2× (matches morning calibration) | 1.43 | 6 of 6 | +$16 |
| 1.4× | 1.19 | 4 of 6 (2021, 2026 slightly negative) | +$8 |

Win rate is about 30%: most trades lose a little, trend days pay for them. Expect long losing streaks.

Robustness (shares, QQQ): band ×0.7 / ×1.3, lookback 30 days, band-only stop all stay positive; long side carries most of it (Sharpe 1.26 vs 0.31 short); at 3 bp cost per trade SPY goes to zero.

## Honest limits

- The option P&L is modelled. Real 0DTE IV runs 1.0–1.5× the VIX model through the day (`data/iv_calibration.jsonl`); at 1.4× the edge is thin. The shares result (Sharpe ~1.1) is the solid number; the options version is a leveraged bet on it.
- Paper fills use CBOE quotes delayed ~15 minutes. The daemon shifts the entry ask by delta × (spot now − spot in quote) so breakouts don't get a stale head start, but it is still an estimate.
- The 1-minute history is CFD bid data (tracks the ETFs closely; live-rule vs research parity on Yahoo 5m bars: ~90% identical entries over the last 45 days).
- 2026 is the weakest year on both symbols. A Sharpe-1 strategy can be flat for a year.

## Paper checkpoint

After 40 trades (~8 weeks): expect PF > 1.1 and 25–35% winners. If PF < 0.9, the option pricing is eating the edge: switch the expression to shares.

## Reproduce

```bash
cd zero_dte_scalper/research
../../venv/bin/python fetch_duka.py SPY,QQQ 2021-01-01   # ~50 min first time, cached after
../../venv/bin/python long_scan.py SPY,QQQ               # every rule in the table, by year
../../venv/bin/python momentum.py QQQ                    # v4 rule + variants + option expression
../../venv/bin/python premium_long.py                    # iron fly over 6 years
../../venv/bin/python -m pytest ../tests -q
```

---

# (Superseded) 0DTE Edge Research — v3.0 (2026-11-09), 58 days


**Data:** SPY + QQQ 5-minute bars, 58 trading days (2026-08-14 → 2026-11-06), cached in `data/cache/` and extended on every run.
**Walk-forward:** in-sample (IS) = first 38 days (to 2026-10-09), out-of-sample (OOS) = last 20 days (from 2026-10-12). Parameters were picked on IS only.
**Engine:** `replay.py` runs the *live* code paths: `score_bars`, `compute_levels`, `select_best_contract`, `decide_exit`, and the session/risk limits. Parity check: replay signals match the daemon's real 11/4 and 11/6 entries within one bar. The ORB-fade run through the live engine reproduces the research rule (66 vs 67 trades, same PF).

## Verdict

| Strategy | Trades | PF | P&L | Max DD | IS PF → OOS PF |
|---|---|---|---|---|---|
| v2.6 live engine (what was running) | 116 | **0.72** | −$2,126 | −$2,342 | <1 → <1 |
| **v3 ORB-failure fade** (long 0DTE, now live) | 61 | **2.22** | +$3,683 | −$495 | 2.27 → 2.15 |
| **SPY iron fly 13:30 ±3, 2 units** (short 0DTE, now live) | 58 days | **3.27** | +$5,273 | −$448 | 3.05 → 3.83 |

Long-option numbers are priced at **1.2× VIX IV**. That matches real SPY/QQQ 0DTE straddles observed 2026-11-09 (mid $3.61 vs $3.01 modeled; both symbols at 1.20×). Iron-fly numbers use 1.0× VIX (credit received), with 0.8× and 1.2× shown in `equity_v3.png`.

![equity](results/equity_v3.png)

## What was learned

1. **The old engine had no edge.** `edge_scan.py` measures the signed forward SPY/QQQ move after each signal, before any option pricing. The full v2.6 engine was −2.6 bps over 30 minutes OOS on SPY. The PDH/PDL "liquidity sweep" setup was reliably wrong (−17 to −48 bps OOS on QQQ). No exit setting can fix entries like that.
2. **Option pricing assumptions decide everything for long scalps.** The first sweep found nine "profitable" live-engine configs at 1.0× VIX. Every one turned negative in-sample at 1.2×, the realistic level. Don't trust a long-option backtest that prices at VIX.
3. **ORB-failure fade is the long-side edge.** Trigger: a 5-minute bar closes outside the 9:30–9:45 opening range, then the next bar closes back inside. Trade back through the range, between 9:45 and 11:30 ET, on completed bars only.
   - It stays positive under every pricing stress (1.0× / 1.2× / 1.35× / 1.5× IV give PF 2.02 / 2.22 / 1.58 / 1.37) and with triple slippage (PF 2.01).
   - It works on each symbol alone (SPY 1.58, QQQ 2.63) and on both sides.
   - Monthly: August PF 4.1, September 2.1, October 2.1, November 0.79 (−$68 over 6 trades).
   - The exits matter: a wide −60% backstop instead of a tight premium stop, no underlying-invalidation exit, TP +50/+100% with no scale-out, and a 60-minute time stop. About a third of trades hit +100%, and those pay for everything else.
4. **Selling afternoon premium is the strongest edge found.** A SPY ATM iron butterfly opened 12:30–14:00 is profitable at *every* entry time and wing width tested (2–5 points), with PF 2.4–3.3 overall and OOS PF ≥ 1.3. 13:00–13:30 is the most stable. It survives collecting only 80% of the modeled premium (PF 1.87). QQQ is weaker and less stable, so it's SPY only.
   - **Risk:** November so far (6 days, VIX ~21, trending tape) lost on every variant. Trend days are the failure mode. Max loss is capped at about $294/day at 2 units.
5. **Dead ends:** ORB breakouts and opening-range breakouts (negative or regime-flipping), VWAP reclaim, trend-day EMA9 pullbacks, RSI/VWAP-stretch mean reversion, gap fade, 10:30 trend drift, 14:00 momentum. Gap-and-go looked strong OOS but has only 30 trades; treat it as noise until there's more data.

## Honest limits

- 58 days, about 60 long trades, and 58 fly days are small samples. The cache grows each run (`python research/replay.py`), so re-run `sweep.py` monthly.
- Options are modeled with Black-Scholes at prior-day VIX/VXN, without skew. The real tests are the **paper ledgers**: both strategies paper-fill on real chain quotes. The daemon also logs `data/iv_calibration.jsonl` (real 0DTE straddle vs model every 30 minutes) and `data/signal_journal.jsonl` (every decision), so model assumptions can be checked against reality.
- Signals use completed 5-minute bars (live v3 does too, via `completed_bars_only`).

## Paper-trading checkpoint

Run 4 weeks of paper, then compare with the backtest:
- ORB fade: expect about 1 trade per day, PF > 1.3. If it's < 1.0 after 25+ trades, the edge isn't transferring.
- Iron fly: expect about 75% win days, mean IV-calibration ratio ≥ 0.9 at 13:30. If the ratio is < 0.8, credits are thinner than modeled, so re-run `premium.py` with that multiplier.

## Reproduce

```bash
cd zero_dte_scalper/research
../../venv/bin/python replay.py            # live config replay + breakdowns
../../venv/bin/python edge_scan.py         # directional edge of every rule
../../venv/bin/python sweep.py             # rules x exits x pricing walk-forward
../../venv/bin/python premium.py           # iron fly / condor grid
```
