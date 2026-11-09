"""
Paper 0DTE iron-butterfly seller (research v3: research/RESULTS.md, "premium selling").

Once per day at the configured entry time, sell an ATM iron butterfly on the real 0DTE chain:
  short ATM call + short ATM put, long call K+W, long put K-W   (defined risk = W - credit)
Fills are conservative natural prices (sell at bid, buy at ask). The position is marked on the
real chain each tick and closed at the configured close time (default 15:50 ET) at natural prices.

Never opens on a synthetic chain (no fake fills). State lives in its own files so it can't
interfere with the long-option scalper ledger.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .config import get_current_ny_time
from .market_data import fetch_0dte_chain

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
POS_FILE = DATA_DIR / "premium_positions.json"
HIST_FILE = DATA_DIR / "premium_history.json"

DEFAULTS = {"enabled": False, "symbol": "SPY", "entry_time": "13:30", "latest_entry_time": "13:45",
            "close_time": "15:50", "wing_width": 3, "units": 1, "min_credit": 0.30}


def _load(path: Path) -> list:
    try:
        return json.loads(path.read_text()) if path.exists() else []
    except Exception:
        return []


def _save(path: Path, data: list):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def load_positions() -> list:
    return _load(POS_FILE)


def load_history() -> list:
    return _load(HIST_FILE)


def _leg_quotes(chain: dict, legs: list[dict]) -> list[dict] | None:
    out = []
    for leg in legs:
        book = chain["calls"] if leg["kind"] == "call" else chain["puts"]
        q = next((c for c in book if abs(c["strike"] - leg["strike"]) < 1e-6), None)
        if not q or q.get("bid", 0) <= 0 or q.get("ask", 0) <= 0:
            return None
        out.append(q)
    return out


def _close_cost(chain: dict, legs: list[dict]) -> float | None:
    """Debit to close at natural prices: buy back shorts at ask, sell longs at bid."""
    qs = _leg_quotes(chain, legs)
    if qs is None:
        return None
    return sum((q["ask"] if leg["side"] == "short" else -q["bid"]) for leg, q in zip(legs, qs))


def _settlement_cost(spot: float, legs: list[dict]) -> float:
    """Intrinsic value owed at expiration (fallback when quotes are unusable)."""
    total = 0.0
    for leg in legs:
        intr = max(0.0, spot - leg["strike"]) if leg["kind"] == "call" else max(0.0, leg["strike"] - spot)
        total += intr if leg["side"] == "short" else -intr
    return total


def tick(config: dict, log=print) -> None:
    """Call every daemon loop. Opens, marks, and closes the daily iron fly."""
    cfg = {**DEFAULTS, **config.get("premium_seller", {})}
    if not cfg["enabled"]:
        return
    now = get_current_ny_time()
    if now.weekday() >= 5:
        return
    hm = now.strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")
    positions = load_positions()

    # ---- mark / close open structures
    if positions:
        try:
            chain = fetch_0dte_chain(cfg["symbol"])
        except Exception as e:
            log(f"[IRON FLY] chain fetch failed: {e}")
            return
        changed = False
        for p in list(positions):
            cost = None if chain.get("synthetic") else _close_cost(chain, p["legs"])
            spot = chain.get("spot_price", 0.0)
            if cost is None and spot > 0:
                cost = _settlement_cost(spot, p["legs"])
            if cost is not None:
                p["mark_cost"] = round(cost, 3)
                p["unrealized_pnl_usd"] = round((p["credit"] - cost) * 100 * p["units"], 2)
                p["spot"] = spot
                changed = True
            if hm >= cfg["close_time"] or p["entry_date"] != today:
                exit_cost = cost if cost is not None else p["width"]  # worst case if no data at all
                pnl = round((p["credit"] - exit_cost) * 100 * p["units"], 2)
                rec = {**p, "exit_time": now.strftime("%Y-%m-%d %H:%M:%S"), "exit_cost": round(exit_cost, 3),
                       "realized_pnl_usd": pnl, "exit_reason": "TIME_CLOSE" if p["entry_date"] == today else "STALE_CLOSE"}
                hist = load_history()
                hist.append(rec)
                _save(HIST_FILE, hist)
                positions.remove(p)
                changed = True
                log(f"🦋 [IRON FLY CLOSED] {p['symbol']} {p['center']} credit ${p['credit']:.2f} -> paid ${exit_cost:.2f} | P&L ${pnl:+,.2f}")
        if changed:
            _save(POS_FILE, positions)
        return

    # ---- open once per day inside the entry window
    if not (cfg["entry_time"] <= hm <= cfg["latest_entry_time"]):
        return
    if any(h.get("entry_date") == today for h in load_history()):
        return
    try:
        chain = fetch_0dte_chain(cfg["symbol"])
    except Exception as e:
        log(f"[IRON FLY] chain fetch failed: {e}")
        return
    if chain.get("synthetic") or not chain.get("is_true_0dte", False):
        log("[IRON FLY] skipped: no real same-day chain available (won't paper-fill on synthetic quotes)")
        return
    spot = chain["spot_price"]
    strikes = sorted({c["strike"] for c in chain["calls"]})
    k = min(strikes, key=lambda x: abs(x - spot))
    w = cfg["wing_width"]
    legs = [{"kind": "call", "strike": k, "side": "short"}, {"kind": "put", "strike": k, "side": "short"},
            {"kind": "call", "strike": k + w, "side": "long"}, {"kind": "put", "strike": k - w, "side": "long"}]
    qs = _leg_quotes(chain, legs)
    if qs is None:
        log(f"[IRON FLY] skipped: missing quotes for {k}±{w}")
        return
    credit = sum((q["bid"] if leg["side"] == "short" else -q["ask"]) for leg, q in zip(legs, qs))
    if credit < cfg["min_credit"]:
        log(f"[IRON FLY] skipped: credit ${credit:.2f} < min ${cfg['min_credit']:.2f}")
        return
    pos = {"id": uuid.uuid4().hex[:8], "symbol": cfg["symbol"], "entry_date": today,
           "entry_time": now.strftime("%Y-%m-%d %H:%M:%S"), "entry_spot": spot, "center": k, "width": w,
           "units": cfg["units"], "legs": legs, "credit": round(credit, 3),
           "max_loss_usd": round((w - credit) * 100 * cfg["units"], 2),
           "max_profit_usd": round(credit * 100 * cfg["units"], 2), "unrealized_pnl_usd": 0.0,
           "mark_cost": round(credit, 3), "spot": spot, "opened_epoch": time.time()}
    _save(POS_FILE, [pos])
    log(f"🦋 [IRON FLY OPENED] {cfg['units']}x {cfg['symbol']} {k - w}/{k}/{k + w} credit ${credit:.2f} "
        f"| max profit ${pos['max_profit_usd']:.0f} / max loss ${pos['max_loss_usd']:.0f}")
