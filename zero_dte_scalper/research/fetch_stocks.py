"""Fetch + cache 5m (60d), 1m (last ~28d) and daily (2y) bars for a liquid, volatile US stock universe (Yahoo)."""
import sys, time
from pathlib import Path
import pandas as pd, yfinance as yf

OUT = Path(__file__).resolve().parent.parent / "data" / "cache" / "stocks"
UNIVERSE = """AAPL MSFT NVDA AMZN GOOGL META TSLA AMD AVGO NFLX CRM ORCL ADBE INTC MU QCOM AMAT LRCX KLAC MRVL SMCI PLTR COIN MSTR
HOOD SOFI RIVN LCID NIO BABA PDD JD UBER LYFT ABNB DASH SHOP PYPL ROKU SNAP PINS RBLX U DKNG CVNA AFRM UPST SNOW NET CRWD ZS PANW
DDOG MDB OKTA TEAM NOW WDAY INTU TXN ADI ON ARM DELL HPQ IBM CSCO BA GE CAT DE F GM XOM CVX OXY SLB COP JPM BAC C WFC GS MS SCHW
V MA AXP UNH LLY PFE MRNA JNJ ABBV MRK BMY GILD AMGN WMT COST TGT HD LOW NKE SBUX MCD DIS CMCSA T VZ KO PEP PG CCL RCL AAL DAL UAL
MARA RIOT CLSK IONQ RGTI SOUN AI PATH GME AMC ENPH FSLR TSM ASML WBD APP RDDT TTD ZM DOCU ETSY EBAY CHWY W PTON BYND SPCE PLUG
FCX NEM AA CLF X NUE VALE MRO DVN HAL LVS WYNN MGM NCLH EXPE BKNG LULU ULTA DG DLTR KR CVS WBA MDT ISRG TMO DHR VRTX REGN BIIB""".split()

def norm(df):
    df = df.dropna(how="all")
    if df.index.tz is not None:
        df.index = df.index.tz_convert("America/New_York")
    return df

def grab(interval, **kw):
    d = yf.download(UNIVERSE, interval=interval, group_by="ticker", progress=False, auto_adjust=False, threads=True, **kw)
    return {t: norm(d[t][["Open", "High", "Low", "Close", "Volume"]]) for t in UNIVERSE if t in d.columns.get_level_values(0) and d[t]["Close"].notna().sum() > 50}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    what = sys.argv[1].split(",") if len(sys.argv) > 1 else ["5m", "1d", "1m"]
    if "5m" in what:
        x = grab("5m", period="60d"); pd.to_pickle(x, OUT / "bars_5m.pkl"); print("5m", len(x), "tickers")
    if "1d" in what:
        x = grab("1d", period="2y"); pd.to_pickle(x, OUT / "bars_1d.pkl"); print("1d", len(x), "tickers")
    if "1m" in what:
        end = pd.Timestamp.now(tz="UTC"); parts = {}
        for i in range(4):
            e = end - pd.Timedelta(days=7 * i); s = e - pd.Timedelta(days=7)
            try:
                for t, df in grab("1m", start=s, end=e).items():
                    parts.setdefault(t, []).append(df)
            except Exception as ex:
                print("chunk", i, "failed", str(ex)[:80])
            time.sleep(1)
        x = {t: pd.concat(v).sort_index() for t, v in parts.items()}
        x = {t: v[~v.index.duplicated()] for t, v in x.items()}
        pd.to_pickle(x, OUT / "bars_1m.pkl"); print("1m", len(x), "tickers", max(len(v) for v in x.values()), "rows max")
