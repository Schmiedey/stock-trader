"""
0DTE Scalper Desk - Dashboard Server.
Serves the live scalper dashboard and a JSON state API for SPY / QQQ 0DTE scalping.
"""

import sys
import os
import json
import time
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = Path(__file__).resolve().parent.parent
ZERO_DTE_DIR = BASE_DIR / "zero_dte_scalper"
HTML_PATH = Path(__file__).resolve().parent / "index.html"
POLL_SECONDS = 10.0

sys.path.insert(0, str(BASE_DIR))

_cache = {
    "lock": threading.Lock(),
    "last_market_fetch": 0,
    "symbols": {"SPY": {}, "QQQ": {}},
}

def fetch_symbol_telemetry(symbol: str) -> dict:
    """Fetch live technicals, signals, and recommended contract for SPY / QQQ."""
    from zero_dte_scalper.src.signals import evaluate_scalp_signals
    from zero_dte_scalper.src.options_selector import select_best_contract
    from zero_dte_scalper.src.market_data import fetch_0dte_chain, get_intraday_data, calculate_indicators

    # 1. Technical Evaluation (Price, VWAP, EMA, RSI)
    try:
        sig = evaluate_scalp_signals(symbol=symbol, interval="5m")
    except Exception as e:
        default_px = 767.50 if symbol == "SPY" else 693.50
        sig = {
            "symbol": symbol,
            "current_price": default_px,
            "vwap": default_px,
            "ema9": default_px,
            "ema21": default_px,
            "rsi": 50.0,
            "atr": 0.50,
            "signal": "NEUTRAL",
            "conviction": "NEUTRAL",
            "primary_setup": "Consolidation / Mixed Signals",
            "active_triggers": [],
            "underlying_stop": default_px - 0.50,
            "underlying_target": default_px + 0.50
        }

    curr_price = float(sig.get("current_price") or (767.50 if symbol == "SPY" else 693.50))

    # 2. Intraday 5m Candles for Charting
    candles = []
    try:
        df = get_intraday_data(symbol, interval="5m", period="1d")
        df = calculate_indicators(df)
        for idx, r in df.tail(90).iterrows():
            candles.append({
                "t": idx.strftime("%H:%M") if hasattr(idx, "strftime") else str(idx)[-8:-3],
                "ts": int(idx.timestamp()) if hasattr(idx, "timestamp") else None,
                "o": round(float(r["Open"]), 2),
                "h": round(float(r["High"]), 2),
                "l": round(float(r["Low"]), 2),
                "c": round(float(r["Close"]), 2),
                "vwap": round(float(r["VWAP"]), 2),
                "ema9": round(float(r["EMA9"]), 2),
                "ema21": round(float(r["EMA21"]), 2),
                "v": int(r["Volume"])
            })
    except Exception:
        pass

    # 3. Option Chain & Strike Recommendation
    calls = []
    puts = []
    rec = None
    try:
        if sig.get("signal") in ["CALL", "PUT"]:
            rec = select_best_contract(symbol=symbol, action=sig["signal"])

        chain_data = fetch_0dte_chain(symbol)
        ref_px = curr_price if curr_price > 0 else chain_data.get("spot_price", 0.0)
        calls = sorted(chain_data.get("calls", []), key=lambda x: abs(x["strike"] - ref_px))[:5]
        puts = sorted(chain_data.get("puts", []), key=lambda x: abs(x["strike"] - ref_px))[:5]
        calls.sort(key=lambda x: x["strike"])
        puts.sort(key=lambda x: x["strike"])
    except Exception:
        pass

    return {
        "symbol": symbol,
        "current_price": round(curr_price, 2),
        "vwap": round(float(sig.get("vwap", curr_price)), 2),
        "ema9": round(float(sig.get("ema9", curr_price)), 2),
        "ema21": round(float(sig.get("ema21", curr_price)), 2),
        "rsi": round(float(sig.get("rsi", 50.0)), 1),
        "atr": round(float(sig.get("atr", 0.50)), 2),
        "signal": sig.get("signal", "NEUTRAL"),
        "conviction": sig.get("conviction", "NONE"),
        "primary_setup": sig.get("primary_setup", "Consolidation / Mixed Signals"),
        "active_triggers": sig.get("active_triggers", []),
        "underlying_stop": round(float(sig.get("underlying_stop", curr_price - 0.50)), 2),
        "underlying_target": round(float(sig.get("underlying_target", curr_price + 0.50)), 2),
        "recommendation": rec,
        "chain": {"calls": calls, "puts": puts},
        "candles": candles,
        "updated_at": time.time()
    }


def background_market_poller():
    """Continuously refresh SPY and QQQ telemetry in the background."""
    while True:
        for sym in ("SPY", "QQQ"):
            try:
                data = fetch_symbol_telemetry(sym)
                with _cache["lock"]:
                    _cache["symbols"][sym] = data
                    _cache["last_market_fetch"] = time.time()
            except Exception:
                pass
        time.sleep(POLL_SECONDS)


def _risk_config(config: dict) -> dict:
    keys = [
        "paper_mode", "account_equity", "max_concurrent_positions", "max_risk_per_trade_usd",
        "max_position_outlay_usd", "max_daily_loss_usd", "max_trades_per_day", "max_contracts",
        "stop_loss_pct", "take_profit_1_pct", "take_profit_2_pct", "max_hold_time_minutes",
        "optimal_target_delta", "version", "strategy", "stop_mode", "hard_stop_pct", "research_note",
    ]
    return {k: config.get(k) for k in keys}


def full_state() -> dict:
    """Active positions, history, daemon status, market telemetry and performance metrics."""
    daemon_info = {"daemon_running": False, "pid": None, "recent_logs": []}
    status_file = ZERO_DTE_DIR / "data/live_status.json"
    if status_file.exists():
        try:
            daemon_info = json.loads(status_file.read_text())
        except Exception:
            pass

    # The status file can be stale if the daemon died; verify the PID is alive.
    running = bool(daemon_info.get("daemon_running"))
    pid = daemon_info.get("pid")
    if running and pid:
        try:
            os.kill(int(pid), 0)
        except Exception:
            running = False

    with _cache["lock"]:
        symbols = dict(_cache["symbols"])
        last_fetch = _cache["last_market_fetch"]

    state = {
        "timestamp": time.time(),
        "market_updated_at": last_fetch,
        "daemon_running": running,
        "daemon_updated_at": daemon_info.get("updated_at"),
        "pid": pid,
        "recent_logs": daemon_info.get("recent_logs", [])[-40:],
        "symbols": symbols,
    }
    try:
        from zero_dte_scalper.src.tracker import (
            load_trade_history, get_today_stats, calculate_performance_metrics,
            update_active_positions_quotes,
        )
        from zero_dte_scalper.src.config import load_config, get_market_session_state

        config = load_config()
        from zero_dte_scalper.src.premium_seller import load_positions as prem_pos, load_history as prem_hist
        state["premium"] = {"config": config.get("premium_seller", {}), "positions": prem_pos(), "history": prem_hist()}
        state.update({
            "session": get_market_session_state(config),
            "config": _risk_config(config),
            "active_positions": update_active_positions_quotes(),
            "history": load_trade_history(),
            "today_stats": get_today_stats(),
            "metrics": calculate_performance_metrics(),
        })
    except Exception as e:
        state.update({
            "session": {"session_phase": "UNKNOWN", "trading_allowed": False},
            "config": {}, "active_positions": [], "history": [],
            "today_stats": {"realized_pnl_usd": 0.0, "trades_count": 0},
            "metrics": {"total_trades": 0, "win_rate_pct": 0.0},
            "error": str(e),
        })
    return state


def manual_enter(symbol: str) -> dict:
    from zero_dte_scalper.src.tracker import open_position
    from zero_dte_scalper.src.signals import evaluate_scalp_signals
    from zero_dte_scalper.src.options_selector import select_best_contract

    sig = evaluate_scalp_signals(symbol=symbol)
    action = sig.get("signal")
    if action not in ("CALL", "PUT"):
        return {"ok": False, "error": f"No active {symbol} signal (currently {action}). Not entering."}

    rec = select_best_contract(symbol=symbol, action=action)
    if not rec or not rec.get("contract"):
        return {"ok": False, "error": f"No liquid 0DTE contract found for {symbol}"}

    c = rec["contract"]
    entry_px = rec.get("entry_price", c.get("ask", 0.50))
    sizing = rec.get("sizing", {})
    qty = max(1, sizing.get("suggested_contracts", sizing.get("contracts", 1)))
    t = rec.get("targets", {})
    pos = open_position(
        symbol=symbol,
        action=action,
        contract_symbol=c["contract_symbol"],
        strike=c["strike"],
        expiration=rec["expiration"],
        entry_price=entry_px,
        quantity=qty,
        stop_loss_price=t.get("stop_loss_price") or t.get("stop_price") or round(entry_px * 0.80, 2),
        tp1_price=t.get("tp1_price") or t.get("target_1") or round(entry_px * 1.30, 2),
        tp2_price=t.get("tp2_price") or t.get("target_2") or round(entry_px * 1.80, 2),
        setup_name=sig.get("primary_setup", "Manual dashboard entry"),
        entry_spot_price=sig.get("current_price", 0.0),
        iv=c.get("iv", 0.16),
    )
    return {"ok": True, "position": pos}


def manual_close(position_id: str) -> dict:
    from zero_dte_scalper.src.tracker import close_position, load_active_positions
    target = next((p for p in load_active_positions() if p.get("id") == position_id
                   or p.get("contract_symbol") == position_id), None)
    if not target:
        return {"ok": False, "error": f"Position not found: {position_id}"}
    px = target.get("current_price", target.get("entry_price", 0.50))
    res = close_position(position_id=target["id"], exit_price=px, reason="Manual UI Close")
    return {"ok": res is not None, "trade": res}


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ct: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(json.dumps(obj, default=str).encode("utf-8"), "application/json", status)

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/0dte/enter":
                return self._json(manual_enter(q.get("symbol", "SPY").upper()))
            if u.path == "/api/0dte/close":
                return self._json(manual_close(q.get("id") or q.get("contract", "")))
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 500)
        self._json({"ok": False, "error": "not found"}, 404)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            return self._json(full_state())
        if path == "/api/ledger":
            sys.path.insert(0, str(BASE_DIR))
            import ledger_sync
            return self._json(ledger_sync.snapshot())
        if path.startswith("/api/"):
            return self._json({"ok": False, "error": "use POST for actions"}, 405)
        body = HTML_PATH.read_bytes() if HTML_PATH.exists() else b"<h1>Dashboard HTML not found.</h1>"
        self._send(body, "text/html; charset=utf-8")

    def log_message(self, *args):
        pass


def run_server(port: int = 8791, host: str = "0.0.0.0"):
    print("Pre-fetching initial SPY / QQQ market telemetry...")
    for sym in ("SPY", "QQQ"):
        try:
            _cache["symbols"][sym] = fetch_symbol_telemetry(sym)
        except Exception as e:
            print(f"  {sym} prefetch failed: {e}")
    _cache["last_market_fetch"] = time.time()
    threading.Thread(target=background_market_poller, daemon=True).start()

    for p in [port, 8791, 8790, 8795, 8080]:
        try:
            server = ThreadingHTTPServer((host, p), Handler)
            print(f"0DTE Scalper Desk listening at: http://{host}:{p}")
            server.serve_forever()
            break
        except OSError:
            print(f"Port {p} occupied, trying next port...")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8791))
    host = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("HOST", "0.0.0.0")
    run_server(port, host)
