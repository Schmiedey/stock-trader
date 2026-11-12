"""Does noise-band momentum work across instruments? Hourly bars, ~2.9 years (Yahoo 1h), checks at each hourly close.
Net of 2bp per trade. A broad effect means a basket can diversify; a QQQ-only effect is a warning sign."""
import sys, warnings
from pathlib import Path
import numpy as np, pandas as pd, yfinance as yf
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import hourly_scan as HS
CACHE = HS.CACHE
TICKERS = ("QQQ SPY IWM DIA SMH XLK XLF XLE XLV XLI XLY XLP XLU ARKK EEM FXI EWZ GLD SLV TLT HYG USO UNG "
           "NVDA TSLA AAPL MSFT AMZN META GOOGL AMD AVGO NFLX PLTR COIN MSTR JPM XOM BA").split()

def get(t):
    p = CACHE / "stocks" / f"{t}_1h.pkl"
    if p.exists(): return pd.read_pickle(p)
    d = yf.download(t, period="730d", interval="1h", progress=False, auto_adjust=False)
    if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.get_level_values(0)
    d = d[["Open", "High", "Low", "Close", "Volume"]].dropna(); d.index = d.index.tz_convert("America/New_York")
    d = d.between_time("09:30", "15:59"); d.to_pickle(p); return d

HS.COST = 2.0
rows, daily = [], {}
for t in TICKERS:
    try:
        b = get(t); b.to_pickle(CACHE / f"_b_1h.pkl")
        o, c, h, l = HS.day_table("_b")
        r = HS.noise_band(o, c, h, l, mult=1.0, exit_mode="band")
    except Exception as e:
        print(t, "fail", str(e)[:60]); continue
    full = r.reindex(c.index[15:]).fillna(0); daily[t] = full
    k = len(full) // 2
    sh = lambda x: x.mean() / x.std() * np.sqrt(252) if x.std() > 0 else 0
    rows.append((t, len(r), r.mean(), sh(full), sh(full.iloc[:k]), sh(full.iloc[k:]), full.sum() / 100 / (len(full) / 252)))
df = pd.DataFrame(rows, columns=["ticker", "trade_days", "bp_per_day", "sharpe", "sharpe_h1", "sharpe_h2", "pct_per_yr"]).sort_values("sharpe", ascending=False)
print(df.round(2).to_string(index=False))
print(f"\npositive Sharpe: {(df.sharpe > 0).sum()}/{len(df)} | both halves positive: {((df.sharpe_h1 > 0) & (df.sharpe_h2 > 0)).sum()} | median Sharpe {df.sharpe.median():.2f}")
M = pd.DataFrame(daily).fillna(0)
for name, cols in (("all", list(M.columns)), ("ETFs only", [c for c in M.columns if c in TICKERS[:23]]), ("stocks only", [c for c in M.columns if c in TICKERS[23:]])):
    x = M[cols].mean(axis=1); k = len(x) // 2
    print(f"equal-weight basket {name:<12}: Sharpe {x.mean() / x.std() * np.sqrt(252):5.2f} (halves {x.iloc[:k].mean() / x.iloc[:k].std() * np.sqrt(252):5.2f} / {x.iloc[k:].mean() / x.iloc[k:].std() * np.sqrt(252):5.2f}), avg pairwise corr {M[cols].corr().values[np.triu_indices(len(cols), 1)].mean():.2f}")
M.to_pickle(CACHE / "stocks" / "breadth_daily.pkl")
