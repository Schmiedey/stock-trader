"""Technical signal generation for 0DTE scalping setups."""

import pandas as pd
from .market_data import get_intraday_data, calculate_indicators, get_key_levels


def evaluate_scalp_signals(symbol: str = "SPY", interval: str = "5m") -> dict:
    """
    Evaluates intraday technical data and generates actionable 0DTE scalp signals.
    Returns:
        {
            "symbol": str,
            "timestamp": str,
            "current_price": float,
            "vwap": float,
            "ema9": float,
            "ema21": float,
            "rsi": float,
            "atr": float,
            "signal": "CALL" | "PUT" | "NEUTRAL",
            "conviction": "HIGH" | "MEDIUM" | "NEUTRAL",
            "primary_setup": str,
            "active_triggers": list[str],
            "underlying_stop": float,
            "underlying_target": float,
            "reason": str,
            "levels": dict
        }
    """
    raw_df = get_intraday_data(symbol=symbol, interval=interval, period="2d")
    df = calculate_indicators(raw_df)
    levels = get_key_levels(symbol=symbol, intraday_df=df)

    latest_bar = df.iloc[-1]
    prev_bar = df.iloc[-2] if len(df) >= 2 else latest_bar

    price = float(latest_bar["Close"])
    vwap = float(latest_bar["VWAP"])
    ema9 = float(latest_bar["EMA9"])
    ema21 = float(latest_bar["EMA21"])
    rsi = float(latest_bar["RSI14"])
    atr = float(latest_bar["ATR"]) if not pd.isna(latest_bar["ATR"]) else 1.0

    orb_high = levels["orb_15m_high"]
    orb_low = levels["orb_15m_low"]
    pdh = levels["pdh"]
    pdl = levels["pdl"]
    hod = levels["hod"]
    lod = levels["lod"]

    bull_triggers = []
    bear_triggers = []

    # 1. Opening Range Breakout (ORB)
    if price > orb_high:
        bull_triggers.append(f"ORB Bullish Breakout (Above 15m high {orb_high:.2f})")
    elif price < orb_low:
        bear_triggers.append(f"ORB Bearish Breakdown (Below 15m low {orb_low:.2f})")

    # 2. VWAP Relationship & Bounce / Rejection
    vwap_pct_dist = (price - vwap) / vwap
    if vwap_pct_dist > 0.0002 and price > ema9:
        bull_triggers.append(f"Above VWAP ({vwap:.2f}) & EMA9 ({ema9:.2f})")
        # Check VWAP bounce: low was near VWAP within 0.1% and candle closed green
        if abs(latest_bar["Low"] - vwap) / vwap < 0.001 and latest_bar["Close"] > latest_bar["Open"]:
            bull_triggers.append("VWAP Support Bounce Confirmed")
    elif vwap_pct_dist < -0.0002 and price < ema9:
        bear_triggers.append(f"Below VWAP ({vwap:.2f}) & EMA9 ({ema9:.2f})")
        # Check VWAP rejection: high was near VWAP and candle closed red
        if abs(latest_bar["High"] - vwap) / vwap < 0.001 and latest_bar["Close"] < latest_bar["Open"]:
            bear_triggers.append("VWAP Resistance Rejection Confirmed")

    # 3. 9 / 21 EMA Momentum Alignment
    if ema9 > ema21:
        if 52.0 <= rsi <= 72.0:
            bull_triggers.append(f"Bullish EMA 9/21 Ribbon Alignment (RSI {rsi:.1f})")
        elif rsi > 72.0:
            bull_triggers.append(f"Strong Bull Momentum, RSI Extended ({rsi:.1f})")
    elif ema9 < ema21:
        if 28.0 <= rsi <= 48.0:
            bear_triggers.append(f"Bearish EMA 9/21 Ribbon Alignment (RSI {rsi:.1f})")
        elif rsi < 28.0:
            bear_triggers.append(f"Strong Bear Momentum, RSI Oversold ({rsi:.1f})")

    # 4. Key Level Sweeps / Invalidation
    if prev_bar["Low"] < pdl and latest_bar["Close"] > pdl:
        bull_triggers.append(f"Bullish Liquidity Sweep of PDL ({pdl:.2f})")
    if prev_bar["High"] > pdh and latest_bar["Close"] < pdh:
        bear_triggers.append(f"Bearish Liquidity Sweep of PDH ({pdh:.2f})")

    # Scoring Verdict
    bull_score = len(bull_triggers)
    bear_score = len(bear_triggers)

    if bull_score >= 2 and bull_score > bear_score:
        signal = "CALL"
        conviction = "HIGH" if bull_score >= 3 else "MEDIUM"
        primary_setup = bull_triggers[0]
        active_triggers = bull_triggers
        underlying_stop = max(vwap - 0.5 * atr, price - 1.2 * atr)
        underlying_target = price + 1.5 * atr
        reason = f"Bullish momentum aligned across {bull_score} indicators: {'; '.join(bull_triggers)}."
    elif bear_score >= 2 and bear_score > bull_score:
        signal = "PUT"
        conviction = "HIGH" if bear_score >= 3 else "MEDIUM"
        primary_setup = bear_triggers[0]
        active_triggers = bear_triggers
        underlying_stop = min(vwap + 0.5 * atr, price + 1.2 * atr)
        underlying_target = price - 1.5 * atr
        reason = f"Bearish downward pressure across {bear_score} indicators: {'; '.join(bear_triggers)}."
    else:
        signal = "NEUTRAL"
        conviction = "NEUTRAL"
        primary_setup = "Consolidation / Mixed Signals"
        active_triggers = bull_triggers + bear_triggers
        underlying_stop = price - atr
        underlying_target = price + atr
        reason = "Market is consolidating or indicators conflict. Stand aside and preserve capital."

    return {
        "symbol": symbol,
        "timestamp": latest_bar.name.strftime("%Y-%m-%d %H:%M:%S EST") if hasattr(latest_bar.name, 'strftime') else str(latest_bar.name),
        "current_price": round(price, 2),
        "vwap": round(vwap, 2),
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "rsi": round(rsi, 1),
        "atr": round(atr, 2),
        "signal": signal,
        "conviction": conviction,
        "primary_setup": primary_setup,
        "active_triggers": active_triggers,
        "underlying_stop": round(underlying_stop, 2),
        "underlying_target": round(underlying_target, 2),
        "reason": reason,
        "levels": levels
    }
