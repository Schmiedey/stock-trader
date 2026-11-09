"""Market data fetching, technical indicator calculations, and 0DTE options chain processing."""

from datetime import datetime, time, timedelta
import pytz
import numpy as np
import pandas as pd
import yfinance as yf
from .greeks import calculate_greeks, black_scholes_price


def get_ny_now() -> datetime:
    """Current NY timezone datetime."""
    return datetime.now(pytz.timezone("America/New_York"))


def get_intraday_data(symbol: str = "SPY", interval: str = "5m", period: str = "2d") -> pd.DataFrame:
    """
    Fetch intraday OHLCV bars using yfinance.
    Returns cleaned dataframe with DateTime index in US/Eastern timezone.
    """
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, prepost=False)
    if df.empty:
        # Fallback without prepost constraint
        df = ticker.history(period=period, interval=interval)

    if df.empty:
        raise ValueError(f"Could not retrieve intraday data for {symbol}")

    # Convert index to NY timezone
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert("America/New_York")
    else:
        df.index = df.index.tz_convert("America/New_York")

    return df


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute VWAP, EMA 9, EMA 21, EMA 50, RSI 14, ATR, Relative Volume, and MACD.
    """
    df = df.copy()

    # 1. VWAP (Intraday cumulative reset each day)
    df["Date"] = df.index.date
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
    df["VP"] = typical_price * df["Volume"]

    # Group by date for VWAP
    grouped = df.groupby("Date")
    df["Cum_VP"] = grouped["VP"].cumsum()
    df["Cum_Vol"] = grouped["Volume"].cumsum()
    df["VWAP"] = df["Cum_VP"] / df["Cum_Vol"].replace(0, np.nan)
    df["VWAP"] = df["VWAP"].ffill()

    # 2. Exponential Moving Averages (Short, Medium, Macro Trend)
    df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA21"] = df["Close"].ewm(span=21, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    # 3. Relative Strength Index (RSI - 14)
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI14"] = 100 - (100 / (1 + rs))
    df["RSI14"] = df["RSI14"].fillna(50.0)

    # 4. Average True Range (ATR - 14)
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(window=14).mean().ffill()

    # 5. Volume Analysis (SMA20 & Relative Volume)
    df["Vol_SMA20"] = df["Volume"].rolling(window=20).mean()
    df["Rel_Volume"] = (df["Volume"] / df["Vol_SMA20"].replace(0, np.nan)).fillna(1.0)

    # 6. MACD (12, 26, 9)
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]

    return df


def get_key_levels(symbol: str = "SPY", intraday_df: pd.DataFrame | None = None) -> dict:
    """
    Identify Previous Day High/Low/Close (PDH, PDL, PDC), Today's HOD, LOD, Open,
    and Opening Range (first 15 minutes of regular hours).
    """
    ticker = yf.Ticker(symbol)
    daily_df = ticker.history(period="5d", interval="1d")

    pdh = None
    pdl = None
    pdc = None
    if len(daily_df) >= 2:
        prev_day = daily_df.iloc[-2]
        pdh = float(prev_day["High"])
        pdl = float(prev_day["Low"])
        pdc = float(prev_day["Close"])

    if intraday_df is None:
        intraday_df = get_intraday_data(symbol, interval="5m", period="2d")

    return compute_levels(intraday_df, pdh, pdl, pdc, as_of=get_ny_now(), symbol=symbol)


def compute_levels(intraday_df: pd.DataFrame, pdh, pdl, pdc, as_of: datetime, symbol: str = "SPY") -> dict:
    """Pure key-level computation as of a given NY time (shared by live engine and replay)."""
    # Filter to today's bars
    ny_now = as_of
    today_date = ny_now.date()
    today_bars = intraday_df[intraday_df.index.date == today_date]

    if today_bars.empty:
        # Fallback to the latest available day
        latest_date = intraday_df.index.date[-1]
        today_bars = intraday_df[intraday_df.index.date == latest_date]

    hod = float(today_bars["High"].max())
    lod = float(today_bars["Low"].min())
    open_price = float(today_bars["Open"].iloc[0])
    current_price = float(today_bars["Close"].iloc[-1])

    # Opening Range (First 15 minutes: 09:30 to 09:45)
    orb_bars = today_bars.between_time("09:30", "09:44")
    time_ny_str = ny_now.strftime("%H:%M")
    orb_established = (time_ny_str >= "09:45") and (not orb_bars.empty)

    if orb_established:
        orb_high = float(orb_bars["High"].max())
        orb_low = float(orb_bars["Low"].min())
    else:
        orb_high = hod
        orb_low = lod

    return {
        "symbol": symbol,
        "current_price": current_price,
        "open_price": open_price,
        "hod": hod,
        "lod": lod,
        "pdh": pdh if pdh else hod,
        "pdl": pdl if pdl else lod,
        "pdc": pdc if pdc else current_price,
        "orb_15m_high": orb_high,
        "orb_15m_low": orb_low,
        "orb_mid": (orb_high + orb_low) / 2.0,
        "orb_established": orb_established
    }


def get_0dte_expiration(symbol: str = "SPY") -> str:
    """Find the 0DTE expiration date (today if available, or the earliest expiration)."""
    ny_now = get_ny_now()
    today_str = ny_now.strftime("%Y-%m-%d")

    # Default fallback: If weekday, today is always 0DTE for SPY and QQQ
    if ny_now.weekday() < 5:
        fallback = today_str
    else:
        days_ahead = 7 - ny_now.weekday()
        fallback = (ny_now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    try:
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if expirations:
            if today_str in expirations:
                return today_str
            return expirations[0]
    except Exception:
        pass

    return fallback


def generate_synthetic_0dte_chain(symbol: str, spot_price: float, exp_date_str: str) -> dict:
    """Generate synthetic 0DTE options chain when Yahoo Finance blocks or rate-limits cloud IPs."""
    tte_years = calculate_time_to_expiration_years(exp_date_str)
    tte_hours = tte_years * 252.0 * 6.5

    center_strike = round(spot_price)
    strikes = [center_strike + i for i in range(-5, 6)]
    calls = []
    puts = []

    try:
        exp_obj = datetime.strptime(exp_date_str, "%Y-%m-%d")
        exp_suffix = exp_obj.strftime("%y%m%d")
    except Exception:
        exp_suffix = datetime.now().strftime("%y%m%d")

    for strike in strikes:
        iv = 0.16  # standard 0DTE ATM IV
        # Call
        call_px = max(0.05, round(black_scholes_price(spot_price, float(strike), tte_years, iv, option_type="call"), 2))
        call_greeks = calculate_greeks(spot_price, float(strike), tte_years, iv, option_type="call")
        call_sym = f"{symbol}{exp_suffix}C{int(strike * 1000):08d}"
        calls.append({
            "contract_symbol": call_sym,
            "option_type": "call",
            "strike": float(strike),
            "bid": round(max(0.01, call_px - 0.01), 2),
            "ask": round(call_px + 0.01, 2),
            "mid": call_px,
            "last_price": call_px,
            "spread": 0.02,
            "volume": 150000,
            "open_interest": 5000,
            "iv": iv,
            "delta": call_greeks["delta"],
            "gamma": call_greeks["gamma"],
            "theta_day": call_greeks["theta_day"],
            "theta_hour": call_greeks["theta_hour"],
            "vega": call_greeks["vega"]
        })

        # Put
        put_px = max(0.05, round(black_scholes_price(spot_price, float(strike), tte_years, iv, option_type="put"), 2))
        put_greeks = calculate_greeks(spot_price, float(strike), tte_years, iv, option_type="put")
        put_sym = f"{symbol}{exp_suffix}P{int(strike * 1000):08d}"
        puts.append({
            "contract_symbol": put_sym,
            "option_type": "put",
            "strike": float(strike),
            "bid": round(max(0.01, put_px - 0.01), 2),
            "ask": round(put_px + 0.01, 2),
            "mid": put_px,
            "last_price": put_px,
            "spread": 0.02,
            "volume": 150000,
            "open_interest": 5000,
            "iv": iv,
            "delta": put_greeks["delta"],
            "gamma": put_greeks["gamma"],
            "theta_day": put_greeks["theta_day"],
            "theta_hour": put_greeks["theta_hour"],
            "vega": put_greeks["vega"]
        })

    return {
        "symbol": symbol,
        "spot_price": round(spot_price, 2),
        "expiration": exp_date_str,
        "is_true_0dte": True,
        "synthetic": True,
        "tte_years": tte_years,
        "tte_hours": round(tte_hours, 2),
        "calls": sorted(calls, key=lambda x: x["strike"]),
        "puts": sorted(puts, key=lambda x: x["strike"])
    }


def calculate_time_to_expiration_years(expiration_date_str: str) -> float:
    """
    Calculate annual time-to-expiration (TTE) for a 0DTE option expiring at 16:00 EST.
    """
    ny_tz = pytz.timezone("America/New_York")
    ny_now = datetime.now(ny_tz)

    try:
        exp_date = datetime.strptime(expiration_date_str, "%Y-%m-%d").date()
    except Exception:
        exp_date = ny_now.date()
    market_close = ny_tz.localize(datetime.combine(exp_date, time(16, 0, 0)))

    diff_seconds = (market_close - ny_now).total_seconds()
    if diff_seconds <= 60:
        # Minimum clamp: 1 minute remaining
        diff_seconds = 60.0

    # For 0DTE intraday options, use annual trading hours: 252 trading days * 6.5 hours = 1,638 hours
    diff_hours = diff_seconds / 3600.0
    tte_years = diff_hours / (252.0 * 6.5)
    return max(1e-5, tte_years)


_CBOE_CACHE: dict = {}
_OCC = __import__("re").compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def fetch_cboe_chain(symbol: str, exp_date_str: str, max_age_s: float = 60.0) -> dict | None:
    """
    Real 0DTE chain from CBOE's free delayed-quotes feed (~15 min delay, no key).
    Used when Yahoo refuses cookies (fresh cloud containers can't get a Yahoo crumb).
    """
    import time as _t
    import requests
    now = _t.time()
    hit = _CBOE_CACHE.get(symbol)
    if hit and now - hit[0] < max_age_s:
        data = hit[1]
    else:
        r = requests.get(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json",
                         timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = r.json()["data"]
        _CBOE_CACHE[symbol] = (now, data)

    exp_code = datetime.strptime(exp_date_str, "%Y-%m-%d").strftime("%y%m%d")
    spot = float(data.get("current_price") or 0.0)
    calls, puts = [], []
    for o in data.get("options", []):
        m = _OCC.match(o.get("option", ""))
        if not m or m.group(2) != exp_code:
            continue
        strike = int(m.group(4)) / 1000.0
        if spot and abs(strike - spot) > 25:
            continue
        bid, ask = float(o.get("bid") or 0), float(o.get("ask") or 0)
        mid = round((bid + ask) / 2, 3) if bid > 0 and ask > 0 else float(o.get("last_trade_price") or 0)
        row = {"contract_symbol": o["option"], "strike": strike, "bid": bid, "ask": ask, "mid": mid,
               "last_price": float(o.get("last_trade_price") or mid), "spread": round(max(0.0, ask - bid), 3),
               "volume": int(o.get("volume") or 0), "open_interest": int(o.get("open_interest") or 0),
               "iv": float(o.get("iv") or 0.16), "delta": float(o.get("delta") or 0.0),
               "gamma": float(o.get("gamma") or 0.0), "theta": float(o.get("theta") or 0.0)}
        (calls if m.group(3) == "C" else puts).append(row)
    if not calls or not puts:
        return None
    tte_years = calculate_time_to_expiration_years(exp_date_str)
    return {"symbol": symbol, "spot_price": round(spot, 2), "expiration": exp_date_str,
            "is_true_0dte": exp_date_str == get_ny_now().strftime("%Y-%m-%d"),
            "source": "cboe_delayed", "quote_time": data.get("last_trade_time"),
            "tte_years": tte_years, "tte_hours": round(tte_years * 252.0 * 6.5, 2),
            "calls": sorted(calls, key=lambda x: x["strike"]), "puts": sorted(puts, key=lambda x: x["strike"])}


def _cboe_or_synthetic(symbol: str, spot_price: float, exp_date_str: str) -> dict:
    try:
        chain = fetch_cboe_chain(symbol, exp_date_str)
        if chain:
            return chain
    except Exception:
        pass
    return generate_synthetic_0dte_chain(symbol, spot_price, exp_date_str)


def fetch_0dte_chain(symbol: str = "SPY") -> dict:
    """
    Fetch and process the full 0DTE option chain with Delta, Gamma, Theta, and spreads.
    Falls back gracefully to synthetic Black-Scholes chains if Yahoo Finance blocks or rate-limits.
    """
    ticker = yf.Ticker(symbol)
    spot_price = 0.0
    try:
        spot_price = float(ticker.fast_info.last_price or 0.0)
    except Exception:
        pass

    if spot_price <= 0:
        try:
            hist = ticker.history(period="1d", interval="1m")
            if not hist.empty:
                spot_price = float(hist["Close"].iloc[-1])
        except Exception:
            pass

    if spot_price <= 0:
        spot_price = 767.0 if symbol == "SPY" else 693.0

    exp_date_str = get_0dte_expiration(symbol)

    try:
        chain = ticker.option_chain(exp_date_str)
        if chain is None or (len(chain.calls) == 0 and len(chain.puts) == 0):
            return _cboe_or_synthetic(symbol, spot_price, exp_date_str)
    except Exception:
        return _cboe_or_synthetic(symbol, spot_price, exp_date_str)
    ny_now = get_ny_now()
    is_true_0dte = (exp_date_str == ny_now.strftime("%Y-%m-%d"))

    tte_years = calculate_time_to_expiration_years(exp_date_str)
    tte_hours = tte_years * 252.0 * 6.5

    def parse_contracts(df: pd.DataFrame, opt_type: str) -> list[dict]:
        results = []
        for _, row in df.iterrows():
            strike = float(row["strike"])
            
            def safe_float(val, default=0.0):
                try:
                    if pd.isna(val):
                        return default
                    return float(val)
                except Exception:
                    return default

            def safe_int(val, default=0):
                try:
                    if pd.isna(val):
                        return default
                    return int(float(val))
                except Exception:
                    return default

            bid = safe_float(row.get("bid", 0.0), 0.0)
            ask = safe_float(row.get("ask", 0.0), 0.0)
            last = safe_float(row.get("lastPrice", 0.0), 0.0)
            vol = safe_int(row.get("volume", 0), 0)
            oi = safe_int(row.get("openInterest", 0), 0)
            contract_sym = str(row.get("contractSymbol", ""))
            iv = safe_float(row.get("impliedVolatility", 0.20), 0.20)
            if iv <= 0.01:
                iv = 0.20

            mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else last
            spread = (ask - bid) if (bid > 0 and ask > 0) else 0.0

            # Greeks calculation
            greeks = calculate_greeks(
                spot=spot_price,
                strike=strike,
                tte_years=tte_years,
                volatility=iv,
                option_type=opt_type
            )

            results.append({
                "contract_symbol": contract_sym,
                "option_type": opt_type,
                "strike": strike,
                "bid": round(bid, 2),
                "ask": round(ask, 2),
                "mid": round(mid, 2),
                "last_price": round(last, 2),
                "spread": round(spread, 2),
                "volume": vol,
                "open_interest": oi,
                "iv": round(iv, 4),
                "delta": greeks["delta"],
                "gamma": greeks["gamma"],
                "theta_day": greeks["theta_day"],
                "theta_hour": greeks["theta_hour"],
                "vega": greeks["vega"]
            })
        return results

    calls_list = parse_contracts(chain.calls, "call")
    puts_list = parse_contracts(chain.puts, "put")

    return {
        "symbol": symbol,
        "spot_price": round(spot_price, 2),
        "expiration": exp_date_str,
        "is_true_0dte": is_true_0dte,
        "tte_years": tte_years,
        "tte_hours": round(tte_hours, 2),
        "calls": sorted(calls_list, key=lambda x: x["strike"]),
        "puts": sorted(puts_list, key=lambda x: x["strike"])
    }
