"""
Exit rules for open 0DTE positions, shared by the live daemon (runner.py) and the
research replay so both trade exactly the same way.

decide_exit() is pure: it reads a position dict (already marked to the latest quote)
and returns what to do. The caller applies it.
"""


def decide_exit(pos: dict, config: dict, session_phase: str = "") -> dict:
    """
    Returns {"updates": {field: value}, "action": None | {...}, "log": str | None}
    action = {"kind": "close" | "partial", "qty": int, "reason": str, "tag": str}
    """
    stop_pct = config.get("stop_loss_pct", 0.20)
    tp1_pct = config.get("take_profit_1_pct", 0.30)
    tp2_pct = config.get("take_profit_2_pct", 0.80)
    max_hold = config.get("max_hold_time_minutes", 20)
    stag_min_pct = config.get("stagnation_min_pnl_pct", 10.0)
    confirm_n = config.get("stop_confirm_checks", 2)
    cushion = config.get("breakeven_cushion_usd", 0.02)
    scale_out = config.get("tp1_scale_out", True)
    use_invalidation = config.get("underlying_invalidation_exit", True)

    entry = pos["entry_price"]
    px = pos.get("current_price", entry)
    qty = pos.get("current_quantity", 1)
    pnl_pct = pos.get("unrealized_pnl_pct", (px - entry) / entry * 100.0 if entry else 0.0)
    stop_px = pos.get("stop_loss_price", entry * (1 - stop_pct))
    tp1_px = pos.get("tp1_price", entry * (1 + tp1_pct))
    tp2_px = pos.get("tp2_price", entry * (1 + tp2_pct))
    elapsed = pos.get("elapsed_minutes", 0.0)
    spot = pos.get("current_spot_price", 0.0)
    u_stop = pos.get("underlying_stop", 0.0)

    out = {"updates": {}, "action": None, "log": None}
    close = lambda reason, tag: {"kind": "close", "qty": qty, "reason": reason, "tag": tag}

    # 1. Premium stop, confirmed over N consecutive checks so one bad print can't stop us out
    # stop_mode "underlying": no tight premium stop; the chart level (underlying_stop) is the
    # real stop via step 5, with a wide catastrophic premium stop as a backstop.
    if config.get("stop_mode", "premium") == "underlying" and not pos.get("tp1_hit"):
        stop_px = min(stop_px, entry * (1 - config.get("hard_stop_pct", 0.50)))
    prev_breach = pos.get("stop_breach_count", 0)
    breach = prev_breach + 1 if px <= stop_px else 0
    if breach != prev_breach:
        out["updates"]["stop_breach_count"] = breach
    if breach >= confirm_n:
        label = "BREAKEVEN_STOP" if pos.get("tp1_hit") else f"STOP_LOSS_HIT (-{stop_pct*100:.0f}%)"
        out["action"] = close(label, "STOP_LOSS")
        out["log"] = f"🛑 [{label}] ${px:.2f} <= stop ${stop_px:.2f} ({pnl_pct:.1f}%)"
        return out
    if breach > 0:
        out["log"] = f"⚠️ [STOP WATCH] ${px:.2f} <= stop ${stop_px:.2f}. Confirming ({breach}/{confirm_n})."

    # 2. Target 2
    if px >= tp2_px:
        out["action"] = close(f"TARGET_2_HIT (+{tp2_pct*100:.0f}%)", "TARGET_2")
        out["log"] = f"🎯 [TARGET 2 HIT] ${px:.2f} >= TP2 ${tp2_px:.2f} (+{pnl_pct:.1f}%)"
        return out

    # 3. Target 1: scale out half and lock the stop above entry (or close a single contract)
    if px >= tp1_px and not pos.get("tp1_hit"):
        if scale_out and qty > 1:
            half = qty // 2
            out["action"] = {"kind": "partial", "qty": half, "reason": f"TARGET_1_SCALE_OUT (+{tp1_pct*100:.0f}%)", "tag": "TARGET_1"}
            out["updates"].update(tp1_hit=True, stop_loss_price=round(entry + cushion, 2), stop_breach_count=0)
            out["log"] = f"💰 [TARGET 1 HIT] ${px:.2f}. Scaling out {half}, stop -> ${entry + cushion:.2f}"
        elif scale_out:
            out["action"] = close(f"TARGET_1_FULL (+{tp1_pct*100:.0f}%)", "TARGET_1")
            out["log"] = f"💰 [TARGET 1 SECURED] ${px:.2f} (+{pnl_pct:.1f}%). Closing 1-contract position."
        else:
            # No scale-out: just lock the stop above entry and let it run to TP2
            out["updates"].update(tp1_hit=True, stop_loss_price=round(entry + cushion, 2), stop_breach_count=0)
            out["log"] = f"🔒 [TP1 REACHED] ${px:.2f}. Stop -> ${entry + cushion:.2f}, holding for TP2."
        return out

    # 4. Stagnation / theta defense
    if elapsed >= max_hold and pnl_pct < stag_min_pct:
        out["action"] = close("STAGNATION_TIME_EXIT", "STAGNATION")
        out["log"] = f"⏳ [STAGNATION EXIT] open {elapsed:.1f}m at {pnl_pct:+.1f}%"
        return out

    # 5. Underlying invalidation (spot broke the technical stop from the entry signal)
    if use_invalidation and u_stop > 0 and spot > 0:
        if (pos["action"] == "CALL" and spot < u_stop) or (pos["action"] == "PUT" and spot > u_stop):
            out["action"] = close("UNDERLYING_INVALIDATION_EXIT", "INVALIDATION")
            out["log"] = f"⚠️ [UNDERLYING INVALIDATION] spot ${spot:.2f} through ${u_stop:.2f}"
            return out

    # 6. Mandatory flat before the close
    if session_phase in ("MANDATORY_FLAT", "MANDATORY_EXIT", "CLOSED", "AFTER_HOURS"):
        out["action"] = close("MANDATORY_EOD_EXPIRATION_EXIT", "EOD")
        out["log"] = f"⏰ [MANDATORY EOD EXIT] @ ${px:.2f}"

    return out
