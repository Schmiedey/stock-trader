"""Fetch + cache longer intraday history for edge research (Yahoo limits: 1h=730d, 5m=60d, 1m=30d in 7d chunks)."""
import sys, time
from pathlib import Path
import pandas as pd, yfinance as yf

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"

def _ny(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.index = df.index.tz_convert("America/New_York")
    return df.between_time("09:30", "15:59")

def fetch(sym, interval):
    path = CACHE / f"{sym}_{interval}.pkl"
    old = pd.read_pickle(path) if path.exists() else None
    parts = []
    if interval == "1m":
        end = pd.Timestamp.now(tz="UTC")
        for i in range(4):
            e = end - pd.Timedelta(days=7 * i); s = e - pd.Timedelta(days=7)
            try:
                parts.append(_ny(yf.download(sym, start=s, end=e, interval="1m", progress=False, auto_adjust=False)))
            except Exception as ex:
                print("  chunk fail", sym, i, str(ex)[:80])
            time.sleep(0.5)
    else:
        period = {"1h": "730d", "5m": "60d", "15m": "60d", "30m": "60d"}[interval]
        parts.append(_ny(yf.download(sym, period=period, interval=interval, progress=False, auto_adjust=False)))
    if old is not None:
        parts.append(old)
    df = pd.concat([p for p in parts if len(p)]).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df.to_pickle(path)
    print(sym, interval, df.shape, df.index[0], df.index[-1])

if __name__ == "__main__":
    syms = sys.argv[1].split(",") if len(sys.argv) > 1 else ["SPY", "QQQ"]
    ivs = sys.argv[2].split(",") if len(sys.argv) > 2 else ["1h", "5m", "1m"]
    for s in syms:
        for iv in ivs:
            try:
                fetch(s, iv)
            except Exception as ex:
                print("FAIL", s, iv, str(ex)[:120])
