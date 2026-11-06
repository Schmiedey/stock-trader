"""Technical signal generation for 0DTE scalping setups."""

import pandas as pd
from .market_data import get_intraday_data, calculate_indicators, get_key_levels


def evaluate_scalp_signals(symbol: str = "SPY", interval: str = "5m") -> dict:
    """
    Evaluates intraday technical data and generates actionable 0DTE scalp signals.
    Implements institutional-grade filters:
    1. Trend & VWAP alignment
    2. True 15-minute Opening Range Breakouts (after 09:45 EST)
    3. Exhaustion & Extension Vetoes (no buying tops or shorting bottoms)
    4. Candle confirmation and Relative Volume surge
    """
    raw_df = get_intraday_data(symbol=symbol, interval=interval, period="2d")
    df = calculate_indicators(raw_df)
    levels = get_key_levels(symbol=symbol, intraday_df=df)

    latest_bar = df.iloc[-1]
    prev_bar = df.iloc[-2] if len(df) >= 2 else latest_bar

    price = float(latest_bar["Close"])
    open_bar = float(latest_bar["Open"])
    high_bar = float(latest_bar["High"])
    low_bar = float(latest_bar["Low"])

    vwap = float(latest_bar["VWAP"])
    ema9 = float(latest_bar["EMA9"])
    ema21 = float(latest_bar["EMA21"])
    ema50 = float(latest_bar["EMA50"]) if "EMA50" in df.columns else (ema9 + ema21) / 2.0
    rsi = float(latest_bar["RSI14"])
    prev_rsi = float(prev_bar["RSI14"])
    atr = float(latest_bar["ATR"]) if not pd.isna(latest_bar["ATR"]) else 1.0
    rel_vol = float(latest_bar["Rel_Volume"]) if "Rel_Volume" in df.columns else 1.0
    macd_hist = float(latest_bar["MACD_Hist"]) if "MACD_Hist" in df.columns else 0.0
    prev_macd_hist = float(prev_bar["MACD_Hist"]) if "MACD_Hist" in df.columns else 0.0

    orb_high = levels["orb_15m_high"]
    orb_low = levels["orb_15m_low"]
    orb_established = levels.get("orb_established", False)
    pdh = levels["pdh"]
    pdl = levels["pdl"]
    hod = levels["hod"]
    lod = levels["lod"]

    bull_triggers = []
    bear_triggers = []
    veto_reasons = []

    is_green_candle = price > open_bar
    is_red_candle = price < open_bar

    # =========================================================================
    # 1. EXHAUSTION & OVEREXTENSION VETOES (Capital Preservation First)
    # =========================================================================
    if rsi >= 70.0:
        veto_reasons.append(f"RSI Overbought ({rsi:.1f} >= 70). High reversal risk on CALLs.")
    if rsi <= 30.0:
        veto_reasons.append(f"RSI Oversold ({rsi:.1f} <= 30). Extreme exhaustion risk on PUTs; bounce probable.")

    if rel_vol < 0.75:
        veto_reasons.append(f"Low relative volume ({rel_vol:.2f}x < 0.75x). Market in low-liquidity drift.")

    vwap_dist = price - vwap
    if vwap_dist > 2.0 * atr:
        veto_reasons.append(f"Price is +{vwap_dist:.2f} (>2 ATR) above VWAP. Overextended for long entries.")
    elif vwap_dist < -2.0 * atr:
        veto_reasons.append(f"Price is {vwap_dist:.2f} (>2 ATR) below VWAP. Overextended for short entries.")

    # =========================================================================
    # 2. SETUP A: TRUE 15-MINUTE ORB BREAKOUT / BREAKDOWN (09:45 - 11:30 EST)
    # =========================================================================
    if orb_established:
        if price > orb_high and is_green_candle and price > vwap and ema9 > ema21 and price > ema50:
            if 50.0 <= rsi <= 68.0 and rel_vol >= 1.05:
                bull_triggers.append(f"Confirmed ORB Bullish Breakout above {orb_high:.2f} (RelVol: {rel_vol:.1f}x)")
        elif price < orb_low and is_red_candle and price < vwap and ema9 < ema21 and price < ema50:
            if 32.0 <= rsi <= 50.0 and rel_vol >= 1.05:
                bear_triggers.append(f"Confirmed ORB Bearish Breakdown below {orb_low:.2f} (RelVol: {rel_vol:.1f}x)")

    # =========================================================================
    # 3. SETUP B: VWAP & EMA TREND PULLBACK CONTINUATION
    # =========================================================================
    # Bullish Trend Pullback: Price > EMA50, EMA9 > EMA21, price tests VWAP or EMA9 and bounces
    if price > ema50 and ema9 > ema21:
        tested_support = (low_bar <= vwap * 1.002 and price >= vwap) or (low_bar <= ema9 * 1.001 and price >= ema9)
        if tested_support and is_green_candle and 45.0 <= rsi <= 68.0:
            bull_triggers.append(f"Bullish Trend Bounce off VWAP ({vwap:.2f}) / EMA9 ({ema9:.2f})")

    # Bearish Trend Relief Rally: Price < EMA50, EMA9 < EMA21, price tests VWAP or EMA9 and rejects
    if price < ema50 and ema9 < ema21:
        tested_resistance = (high_bar >= vwap * 0.998 and price <= vwap) or (high_bar >= ema9 * 0.999 and price <= ema9)
        if tested_resistance and is_red_candle and 32.0 <= rsi <= 55.0:
            bear_triggers.append(f"Bearish Trend Rejection off VWAP ({vwap:.2f}) / EMA9 ({ema9:.2f})")

    # =========================================================================
    # 4. SETUP C: LIQUIDITY SWEEP & MEAN REVERSION
    # =========================================================================
    if prev_bar["Low"] < pdl and price > pdl and is_green_candle and rsi > 30.0 and rsi > prev_rsi:
        bull_triggers.append(f"Liquidity Sweep Reversal of PDL ({pdl:.2f}) with Bullish Candle")
    if prev_bar["High"] > pdh and price < pdh and is_red_candle and rsi < 70.0 and rsi < prev_rsi:
        bear_triggers.append(f"Liquidity Sweep Rejection of PDH ({pdh:.2f}) with Bearish Candle")

    # =========================================================================
    # 5. MOMENTUM CONFIRMATION (MACD & EMA Ribbon Expansion)
    # =========================================================================
    if ema9 > ema21 and macd_hist > 0 and macd_hist >= prev_macd_hist:
        if 50.0 <= rsi <= 68.0:
            bull_triggers.append(f"Bullish Momentum: EMA9 > EMA21 with MACD Histogram Expansion")
    elif ema9 < ema21 and macd_hist < 0 and macd_hist <= prev_macd_hist:
        if 32.0 <= rsi <= 50.0:
            bear_triggers.append(f"Bearish Momentum: EMA9 < EMA21 with MACD Histogram Expansion")

    # =========================================================================
    # 6. SCORING & FINAL CONVICTION VERDICT
    # =========================================================================
    bull_score = len(bull_triggers)
    bear_score = len(bear_triggers)

    # Check vetoes
    call_vetoed = any("CALL" in v or "above VWAP" in v for v in veto_reasons)
    put_vetoed = any("PUT" in v or "below VWAP" in v for v in veto_reasons)

    # Require minimum score of 2, clean edge, and zero vetoes
    if bull_score >= 2 and bull_score > bear_score and not call_vetoed:
        signal = "CALL"
        conviction = "HIGH" if bull_score >= 3 else "MEDIUM"
        primary_setup = bull_triggers[0]
        active_triggers = bull_triggers
        underlying_stop = max(vwap - 0.5 * atr, price - 1.0 * atr)
        underlying_target = price + 1.5 * atr
        reason = f"Bullish alignment across {bull_score} factors: {'; '.join(bull_triggers)}."
    elif bear_score >= 2 and bear_score > bull_score and not put_vetoed:
        signal = "PUT"
        conviction = "HIGH" if bear_score >= 3 else "MEDIUM"
        primary_setup = bear_triggers[0]
        active_triggers = bear_triggers
        underlying_stop = min(vwap + 0.5 * atr, price + 1.0 * atr)
        underlying_target = price - 1.5 * atr
        reason = f"Bearish alignment across {bear_score} factors: {'; '.join(bear_triggers)}."
    else:
        signal = "NEUTRAL"
        conviction = "NEUTRAL"
        primary_setup = "Preserve Capital / Wait for Setup"
        active_triggers = bull_triggers + bear_triggers
        underlying_stop = price - atr
        underlying_target = price + atr

        reasons = []
        if veto_reasons:
            reasons.append("Active Vetoes: " + "; ".join(veto_reasons))
        if not bull_triggers and not bear_triggers:
            reasons.append("No actionable setups detected. Market in chop/consolidation.")
        elif (call_vetoed and bull_score >= 2) or (put_vetoed and bear_score >= 2):
            reasons.append("Setup invalid due to overextension/exhaustion.")
        else:
            reasons.append(f"Insufficient conviction (Bull: {bull_score}, Bear: {bear_score}). Stand aside.")

        reason = " | ".join(reasons)

    return {
        "symbol": symbol,
        "timestamp": latest_bar.name.strftime("%Y-%m-%d %H:%M:%S EST") if hasattr(latest_bar.name, 'strftime') else str(latest_bar.name),
        "current_price": round(price, 2),
        "vwap": round(vwap, 2),
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "ema50": round(ema50, 2),
        "rsi": round(rsi, 1),
        "atr": round(atr, 2),
        "rel_vol": round(rel_vol, 2),
        "signal": signal,
        "conviction": conviction,
        "primary_setup": primary_setup,
        "active_triggers": active_triggers,
        "veto_reasons": veto_reasons,
        "underlying_stop": round(underlying_stop, 2),
        "underlying_target": round(underlying_target, 2),
        "reason": reason,
        "levels": levels
    }
