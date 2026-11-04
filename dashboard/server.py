"""
Dedicated Stock Trader & 0DTE Quant Desk Server.
Completely standalone inside /Users/wesleyschmiedeskamp/Documents/stock trader/dashboard/
Provides rich visual telemetry, interactive chart streams, sparklines, and terminal logs.
"""

import sys
import os
import json
import time
import threading
import subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = Path(__file__).resolve().parent.parent
ZERO_DTE_DIR = BASE_DIR / "zero_dte_scalper"
DATA_DIR = BASE_DIR / "data"
HTML_PATH = Path(__file__).resolve().parent / "index.html"

# Add stock trader to python path
sys.path.insert(0, str(BASE_DIR))

# Metadata catalog for index names
COMPANY_NAMES = {
    "INTC": "Intel Corporation · Semiconductors",
    "LITE": "Lumentum Holdings · Photonic & Optical Infra",
    "RDDT": "Reddit, Inc. · Social Media & Communities",
    "MRVL": "Marvell Technology · AI & Cloud Semiconductors",
    "ALAB": "Astera Labs · Connectivity Infrastructure",
    "ILMN": "Illumina, Inc. · Genomics & Biotech",
    "DDOG": "Datadog, Inc. · Cloud Monitoring & Security",
    "SNDK": "Sandisk · Storage Technology",
    "AMD": "Advanced Micro Devices · High-Performance Computing",
    "SHOP": "Shopify Inc. · Global Commerce Platform"
}

# Thread-safe telemetry cache
_cache = {
    "lock": threading.Lock(),
    "last_market_fetch": 0,
    "last_sparkline_fetch": 0,
    "symbols": {"SPY": {}, "QQQ": {}},
    "candles": {"SPY": [], "QQQ": []},
    "sparklines": {},
    "backtest": None
}


def load_backtest_data() -> dict:
    bt_file = DATA_DIR / "backtest_results.json"
    if bt_file.exists():
        try:
            return json.loads(bt_file.read_text())
        except Exception:
            pass
    return {}


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
        for idx, r in df.tail(40).iterrows():
            candles.append({
                "t": idx.strftime("%H:%M") if hasattr(idx, "strftime") else str(idx)[-8:-3],
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


def fetch_sparklines_for_held_positions(tickers: list[str]) -> dict:
    """Fetch 7-day daily close sparklines for held positions."""
    if not tickers:
        return {}
    try:
        import yfinance as yf
        data = yf.download(tickers, period="7d", interval="1d", progress=False)
        closes = data["Close"]
        out = {}
        for t in tickers:
            if t in closes:
                vals = [round(float(v), 2) for v in closes[t].dropna().values]
                out[t] = vals
        return out
    except Exception:
        return {}


def background_market_poller():
    """Continuously refresh SPY and QQQ data and sparklines in background."""
    while True:
        try:
            spy_data = fetch_symbol_telemetry("SPY")
            qqq_data = fetch_symbol_telemetry("QQQ")
            with _cache["lock"]:
                _cache["symbols"]["SPY"] = spy_data
                _cache["symbols"]["QQQ"] = qqq_data
                _cache["candles"]["SPY"] = spy_data.get("candles", [])
                _cache["candles"]["QQQ"] = qqq_data.get("candles", [])
                _cache["last_market_fetch"] = time.time()

            # Refresh sparklines every 60s
            if time.time() - _cache["last_sparkline_fetch"] > 60:
                # Read held tickers from portfolio.json
                p_file = DATA_DIR / "portfolio.json"
                if p_file.exists():
                    p_data = json.loads(p_file.read_text())
                    held = [pos["ticker"] for pos in p_data.get("positions", {}).values()]
                    sparks = fetch_sparklines_for_held_positions(held)
                    with _cache["lock"]:
                        _cache["sparklines"].update(sparks)
                        _cache["last_sparkline_fetch"] = time.time()

        except Exception:
            pass
        time.sleep(4.0)


def get_spinach_state() -> dict:
    """Read live Spinach strategy telemetry and enrich with visual progress metrics."""
    live_status_file = DATA_DIR / "live_status.json"
    portfolio_file = DATA_DIR / "portfolio.json"
    history_file = DATA_DIR / "history.json"
    log_file = DATA_DIR / "runner.log"

    state = {
        "daemon_running": False,
        "account_equity": 100000.0,
        "available_cash": 100000.0,
        "positions": {},
        "pending_sells": [],
        "pending_buys": [],
        "top_candidates": [],
        "recent_history": [],
        "runner_logs": [],
        "backtest": _cache.get("backtest") or load_backtest_data()
    }

    if live_status_file.exists():
        try:
            state.update(json.loads(live_status_file.read_text()))
        except Exception:
            pass

    if portfolio_file.exists():
        try:
            p_data = json.loads(portfolio_file.read_text())
            state["account_equity"] = p_data.get("account_equity", state["account_equity"])
            state["available_cash"] = p_data.get("available_cash", state["available_cash"])
            if not state.get("positions"):
                state["positions"] = p_data.get("positions", {})
        except Exception:
            pass

    if history_file.exists():
        try:
            h_data = json.loads(history_file.read_text())
            state["recent_history"] = list(reversed(h_data[-30:]))
        except Exception:
            pass

    # Read last 35 lines of runner daemon log
    if log_file.exists():
        try:
            lines = log_file.read_text().splitlines()
            state["runner_logs"] = lines[-35:]
        except Exception:
            pass

    # Enrich positions with sparklines, target progress %, and metadata
    with _cache["lock"]:
        spark_dict = dict(_cache["sparklines"])

    enriched_positions = {}
    for slot_key, pos in state.get("positions", {}).items():
        ticker = pos.get("ticker", "")
        entry_px = pos.get("entry_price", 1.0)
        curr_px = pos.get("current_price", entry_px)
        tgt_px = pos.get("target_price", entry_px * 1.03)

        # Progress toward +3% target: 0% at entry, 100% at target
        price_diff_total = tgt_px - entry_px
        if price_diff_total > 0:
            progress_pct = max(0.0, min(100.0, ((curr_px - entry_px) / price_diff_total) * 100.0))
        else:
            progress_pct = 0.0

        p = dict(pos)
        p["company_name"] = COMPANY_NAMES.get(ticker, f"{ticker} Component")
        p["sparkline"] = spark_dict.get(ticker, [round(entry_px * 0.98, 2), round(entry_px, 2), round(curr_px, 2)])
        p["target_progress_pct"] = round(progress_pct, 1)
        p["days_held"] = pos.get("days_held", 0)
        enriched_positions[slot_key] = p

    state["positions"] = enriched_positions
    return state


def get_zero_dte_state() -> dict:
    """Read active 0DTE positions, candles, daemon status, and performance metrics."""
    daemon_info = {"daemon_running": False, "pid": None, "recent_logs": []}
    scalper_status_file = ZERO_DTE_DIR / "data/live_status.json"
    if scalper_status_file.exists():
        try:
            daemon_info = json.loads(scalper_status_file.read_text())
        except Exception:
            pass

    try:
        from zero_dte_scalper.src.tracker import (
            load_active_positions,
            load_trade_history,
            get_today_stats,
            calculate_performance_metrics,
            update_active_positions_quotes
        )
        from zero_dte_scalper.src.config import load_config, get_market_session_state

        config = load_config()
        session = get_market_session_state(config)
        active = update_active_positions_quotes()
        history = load_trade_history()
        today = get_today_stats()
        metrics = calculate_performance_metrics()

        with _cache["lock"]:
            symbols = dict(_cache["symbols"])
            candles = dict(_cache["candles"])

        return {
            "daemon_running": daemon_info.get("daemon_running", False),
            "pid": daemon_info.get("pid"),
            "recent_logs": daemon_info.get("recent_logs", []),
            "session": session,
            "symbols": symbols,
            "candles": candles,
            "active_positions": active,
            "history": list(reversed(history[-20:])),
            "today_stats": today,
            "metrics": metrics
        }
    except Exception as e:
        with _cache["lock"]:
            symbols = dict(_cache["symbols"])
            candles = dict(_cache["candles"])
        return {
            "daemon_running": daemon_info.get("daemon_running", False),
            "pid": daemon_info.get("pid"),
            "recent_logs": daemon_info.get("recent_logs", []),
            "session": {"session_phase": "UNKNOWN", "trading_allowed": True},
            "symbols": symbols,
            "candles": candles,
            "active_positions": [],
            "history": [],
            "today_stats": {"realized_pnl_usd": 0.0, "trades_count": 0, "win_rate_pct": 0.0},
            "metrics": {"total_trades": 0, "win_rate_pct": 0.0},
            "error": str(e)
        }


def full_state() -> dict:
    return {
        "timestamp": time.time(),
        "spinach": get_spinach_state(),
        "zero_dte": get_zero_dte_state()
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.do_GET()

    def do_GET(self):
        url = self.path.split("?")[0]

        if url == "/api/state":
            body = json.dumps(full_state()).encode("utf-8")
            ct = "application/json"

        elif url == "/api/spinach/run":
            try:
                res = subprocess.run([
                    sys.executable,
                    str(BASE_DIR / "main.py"),
                    "run",
                    "-y"
                ], capture_output=True, text=True, timeout=90)
                body = json.dumps({"ok": True, "output": res.stdout}).encode("utf-8")
            except Exception as e:
                body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
            ct = "application/json"

        elif url == "/api/0dte/enter":
            try:
                from zero_dte_scalper.src.tracker import open_position
                from zero_dte_scalper.src.signals import evaluate_scalp_signals
                from zero_dte_scalper.src.options_selector import select_best_contract

                query = self.path.split("?")[1] if "?" in self.path else ""
                params = dict(qp.split("=") for qp in query.split("&") if "=" in qp)
                symbol = params.get("symbol", "SPY").upper()

                sig = evaluate_scalp_signals(symbol=symbol)
                action = sig.get("signal", "CALL")
                if action not in ["CALL", "PUT"]:
                    action = "CALL"

                rec = select_best_contract(symbol=symbol, action=action)
                if not rec or not rec.get("contract"):
                    body = json.dumps({"ok": False, "error": f"No liquid 0DTE contract found for {symbol}"}).encode("utf-8")
                else:
                    c = rec["contract"]
                    entry_px = rec.get("entry_price", c.get("ask", 0.50))
                    qty = max(1, rec.get("sizing", {}).get("suggested_contracts", rec.get("sizing", {}).get("contracts", 1)))
                    t = rec.get("targets", {})
                    stop_px = t.get("stop_loss_price") or t.get("stop_price") or round(entry_px * 0.85, 2)
                    tp1_px = t.get("tp1_price") or t.get("target_1") or round(entry_px * 1.20, 2)
                    tp2_px = t.get("tp2_price") or t.get("target_2") or round(entry_px * 1.40, 2)

                    pos = open_position(
                        symbol=symbol,
                        action=action,
                        contract_symbol=c["contract_symbol"],
                        strike=c["strike"],
                        expiration=rec["expiration"],
                        entry_price=entry_px,
                        quantity=qty,
                        stop_loss_price=stop_px,
                        tp1_price=tp1_px,
                        tp2_price=tp2_px,
                        setup_name=sig.get("primary_setup", "Manual UI trigger")
                    )
                    body = json.dumps({"ok": True, "position": pos}).encode("utf-8")
            except Exception as e:
                body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
            ct = "application/json"

        elif url == "/api/0dte/close":
            try:
                from zero_dte_scalper.src.tracker import close_position, load_active_positions
                query = self.path.split("?")[1] if "?" in self.path else ""
                params = dict(qp.split("=") for qp in query.split("&") if "=" in qp)
                contract_symbol = params.get("contract", "")
                position_id = params.get("id", "")

                active_positions = load_active_positions()
                target_pos = None
                for p in active_positions:
                    if (position_id and p.get("id") == position_id) or (contract_symbol and p.get("contract_symbol") == contract_symbol):
                        target_pos = p
                        break

                if not target_pos:
                    body = json.dumps({"ok": False, "error": f"Position not found for {contract_symbol or position_id}"}).encode("utf-8")
                else:
                    curr_px = target_pos.get("current_price", target_pos.get("entry_price", 0.50))
                    res = close_position(position_id=target_pos["id"], exit_price=curr_px, reason="Manual UI Close")
                    body = json.dumps({"ok": res is not None, "trade": res}).encode("utf-8")
            except Exception as e:
                body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
            ct = "application/json"

        else:
            if HTML_PATH.exists():
                body = HTML_PATH.read_bytes()
            else:
                body = b"<h1>Stock Trader Quant Desk</h1><p>Dashboard HTML not found.</p>"
            ct = "text/html; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def run_server(port: int = 8791, host: str = "0.0.0.0"):
    _cache["backtest"] = load_backtest_data()

    # Pre-fetch initial sparklines for held positions
    p_file = DATA_DIR / "portfolio.json"
    if p_file.exists():
        try:
            p_data = json.loads(p_file.read_text())
            held = [pos["ticker"] for pos in p_data.get("positions", {}).values()]
            _cache["sparklines"] = fetch_sparklines_for_held_positions(held)
            _cache["last_sparkline_fetch"] = time.time()
        except Exception:
            pass

    # Start background market poller
    t = threading.Thread(target=background_market_poller, daemon=True)
    t.start()

    # Pre-populate first fetch
    print("Pre-fetching initial SPY / QQQ market telemetry...")
    with _cache["lock"]:
        _cache["symbols"]["SPY"] = fetch_symbol_telemetry("SPY")
        _cache["symbols"]["QQQ"] = fetch_symbol_telemetry("QQQ")

    env_port = int(os.environ.get("PORT", port))
    for p in [env_port, 8791, 8790, 8795, 8080]:
        try:
            server = ThreadingHTTPServer((host, p), Handler)
            print(f"⚡ Stock Trader Quant Desk listening at: http://{host}:{p}")
            server.serve_forever()
            break
        except OSError:
            print(f"Port {p} occupied, trying next port...")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8791))
    host = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("HOST", "0.0.0.0")
    run_server(port, host)
