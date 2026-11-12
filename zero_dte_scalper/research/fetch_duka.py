"""Download multi-year 1-minute bars (Dukascopy public datafeed, ETF CFD bid candles) -> data/cache/{SYM}_1m_long.pkl.
Resumable: per-day raw files are cached in data/cache/duka/ so reruns never refetch."""
import sys, lzma, struct, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np, pandas as pd, requests

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
RAW = CACHE / "duka"
MAP = {"SPY": "SPYUSUSD", "QQQ": "QQQUSUSD", "IWM": "IWMUSUSD"}
_s = requests.Session(); _s.headers["User-Agent"] = "Mozilla/5.0"


def get_day(sym, d):
    f = RAW / sym / f"{d:%Y%m%d}.bi5"
    if f.exists():
        return f.read_bytes()
    u = f"https://datafeed.dukascopy.com/datafeed/{MAP[sym]}/{d.year}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5"
    for a in range(4):
        try:
            r = _s.get(u, timeout=20)
            if r.status_code == 200:
                f.write_bytes(r.content); return r.content
            if r.status_code == 404:
                f.write_bytes(b""); return b""
        except Exception:
            pass
        time.sleep(1.5 * (a + 1))
    return None


def parse(d, blob):
    if not blob:
        return None
    raw = lzma.decompress(blob)
    a = np.frombuffer(raw, dtype=np.dtype([("t", ">u4"), ("o", ">u4"), ("c", ">u4"), ("l", ">u4"), ("h", ">u4"), ("v", ">f4")]))
    a = a[a["v"] > 0]
    if len(a) < 100:
        return None
    idx = (pd.Timestamp(d, tz="UTC") + pd.to_timedelta(a["t"].astype(np.int64), unit="s")).tz_convert("America/New_York")
    return pd.DataFrame({"Open": a["o"] / 1000.0, "High": a["h"] / 1000.0, "Low": a["l"] / 1000.0,
                         "Close": a["c"] / 1000.0, "Volume": a["v"].astype(float)}, index=idx)


def main(sym, start):
    (RAW / sym).mkdir(parents=True, exist_ok=True)
    days = [d for d in pd.bdate_range(start, pd.Timestamp.now().normalize() - pd.Timedelta(days=1))]
    with ThreadPoolExecutor(6) as ex:
        blobs = list(ex.map(lambda d: get_day(sym, d), days))
    fail = sum(b is None for b in blobs)
    parts = [p for d, b in zip(days, blobs) if b and (p := parse(d, b)) is not None]
    df = pd.concat(parts).between_time("09:30", "15:59")
    df.to_pickle(CACHE / f"{sym}_1m_long.pkl")
    print(sym, df.shape, df.index[0], df.index[-1], "days", df.index.normalize().nunique(), "failed", fail)


if __name__ == "__main__":
    for s in sys.argv[1].split(","):
        main(s, sys.argv[2] if len(sys.argv) > 2 else "2021-01-01")
