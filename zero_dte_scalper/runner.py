"""
Autonomous 0DTE Options Scalper Daemon v2.0.
Continuously scans intraday technicals for SPY and QQQ, enters high-conviction
scalp setups on paper, and manages exits via hard -15% stop-loss, +20%/+40% profit targets,
underlying technical invalidation, and theta stagnation exits.
"""

import os
import sys
import time
import json
import signal
import datetime
import traceback
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
SCALPER_DIR = Path(__file__).resolve().parent
DATA_DIR = SCALPER_DIR / "data"
PID_FILE = DATA_DIR / "runner.pid"
LOG_FILE = DATA_DIR / "runner.log"
STATUS_FILE = DATA_DIR / "live_status.json"
JOURNAL_FILE = DATA_DIR / "signal_journal.jsonl"
IV_CAL_FILE = DATA_DIR / "iv_calibration.jsonl"


def journal(record: dict, path: Path = JOURNAL_FILE):
    """Append one JSON line. Used to diff live decisions against the research replay."""
    try:
        record = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), **record}
        with open(path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


def log_iv_calibration(symbol: str):
    """Record real 0DTE ATM straddle price vs Black-Scholes at VIX/VXN (validates research pricing)."""
    import yfinance as yf
    from zero_dte_scalper.src.greeks import black_scholes_price
    t = yf.Ticker(symbol)
    exp = t.options[0]
    spot = float(t.fast_info.last_price)
    ch = t.option_chain(exp)
    k = min(ch.calls.strike, key=lambda x: abs(x - spot))
    c = ch.calls[ch.calls.strike == k].iloc[0]
    p = ch.puts[ch.puts.strike == k].iloc[0]
    straddle = (c.bid + c.ask) / 2 + (p.bid + p.ask) / 2
    vol = float(yf.Ticker("^VIX" if symbol == "SPY" else "^VXN").fast_info.last_price) / 100
    # calendar-time TTE to 16:00 ET, the same convention research/replay.py prices with
    now = datetime.datetime.now(datetime.timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
    close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    tte = max(60.0, (close - now).total_seconds()) / (365.25 * 86400.0)
    model = black_scholes_price(spot, k, tte, vol, option_type="call") + black_scholes_price(spot, k, tte, vol, option_type="put")
    journal({"symbol": symbol, "expiration": exp, "spot": round(spot, 2), "strike": float(k),
             "straddle_mid": round(straddle, 3), "model_at_index": round(model, 3),
             "ratio": round(straddle / model, 3) if model > 0 else None, "index_iv": round(vol, 4)}, IV_CAL_FILE)

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(SCALPER_DIR))

from zero_dte_scalper.src.config import load_config, get_market_session_state
from zero_dte_scalper.src.signals import evaluate_scalp_signals
from zero_dte_scalper.src.exits import decide_exit
from zero_dte_scalper.src.premium_seller import tick as premium_tick
from zero_dte_scalper.src.options_selector import select_best_contract
from zero_dte_scalper.src.risk_manager import check_trading_permissions
from zero_dte_scalper.src.tracker import (
    load_active_positions,
    save_active_positions,
    open_position,
    close_position,
    update_active_positions_quotes,
    get_today_stats,
    calculate_performance_metrics,
    get_current_timestamp_ny
)


def log(msg: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


class ZeroDteScalperDaemon:
    def __init__(self):
        self.config = load_config()
        self.underlyings = self.config.get("underlyings", ["SPY", "QQQ"])
        # Strict single position limit: never hold correlated SPY & QQQ simultaneously
        self.max_concurrent_positions = self.config.get("max_concurrent_positions", 1)
        self.last_screen_time = 0.0
        self.last_exit_times = {}  # {symbol: (exit_timestamp, reason_str)}
        self.reentry_cooldown_seconds = self.config.get("reentry_cooldown_seconds", 300)
        self.max_hold_time_minutes = self.config.get("max_hold_time_minutes", 20)
        self.circuit_breaker_logged = False

    def update_telemetry(self):
        """Write live daemon status and telemetry."""
        try:
            today_stats = get_today_stats()
            session = get_market_session_state(self.config)
            active = load_active_positions()
            metrics = calculate_performance_metrics()

            # Read recent logs
            recent_logs = []
            if LOG_FILE.exists():
                recent_logs = LOG_FILE.read_text().splitlines()[-30:]

            status_payload = {
                "daemon_running": True,
                "pid": os.getpid(),
                "updated_at": datetime.datetime.now().isoformat(),
                "session": session,
                "active_positions_count": len(active),
                "active_positions": active,
                "today_stats": today_stats,
                "metrics": metrics,
                "recent_logs": recent_logs
            }
            STATUS_FILE.write_text(json.dumps(status_payload, indent=2))
        except Exception as e:
            log(f"Failed to write telemetry: {e}")

    def manage_active_positions(self):
        """Check stop-loss, profit targets, stagnation, and invalidation (levels from config)."""
        try:
            positions = update_active_positions_quotes()
        except Exception as e:
            log(f"Error updating quotes: {e}")
            positions = load_active_positions()

        if not positions:
            return

        session = get_market_session_state(self.config)
        session_phase = session.get("session_phase", "")

        for pos in list(positions):
            pos_id = pos["id"]
            contract_sym = pos["contract_symbol"]
            decision = decide_exit(pos, self.config, session_phase)

            if decision["log"]:
                log(f"{decision['log']} | {contract_sym}")

            if decision["updates"]:
                active_now = load_active_positions()
                for p in active_now:
                    if p["id"] == pos_id:
                        p.update(decision["updates"])
                save_active_positions(active_now)

            act = decision["action"]
            if not act:
                continue
            try:
                curr_px = pos.get("current_price", pos["entry_price"])
                if act["kind"] == "partial":
                    close_position(position_id=pos_id, exit_price=curr_px, reason=act["reason"], partial_qty=act["qty"])
                else:
                    close_position(position_id=pos_id, exit_price=curr_px, reason=act["reason"])
                    if act["tag"] != "EOD":
                        self.last_exit_times[pos["symbol"]] = (time.time(), act["tag"])
                log(f"[✓] {contract_sym}: {act['reason']} ({act['kind']} x{act['qty']})")
            except Exception as e:
                log(f"Error executing exit {act['reason']} for {contract_sym}: {e}")

    def scan_and_enter_setups(self):
        """Scan for high-conviction 0DTE scalp setups during prime sessions."""
        session = get_market_session_state(self.config)
        if not session.get("trading_allowed", False):
            return

        # Check active positions count (Strict max 1 position rule)
        active = load_active_positions()
        if len(active) >= self.max_concurrent_positions:
            return

        # Check daily risk permissions
        today_stats = get_today_stats()
        allowed, reason = check_trading_permissions(
            daily_realized_pnl=today_stats.get("realized_pnl_usd", 0.0),
            daily_trades_count=today_stats.get("trades_count", 0),
            config=self.config
        )
        if not allowed:
            if not self.circuit_breaker_logged:
                log(f"🛡️ [RISK LOCK]: {reason}")
                self.circuit_breaker_logged = True
            return
        else:
            self.circuit_breaker_logged = False

        active_symbols = {p["symbol"] for p in active}

        for symbol in self.underlyings:
            if symbol in active_symbols:
                continue

            # Re-entry Cooldown check (5 min after stop loss, 3 min after TP)
            if symbol in self.last_exit_times:
                last_exit_t, last_reason = self.last_exit_times[symbol]
                elapsed = time.time() - last_exit_t
                cooldown_dur = self.reentry_cooldown_seconds if last_reason in ["STOP_LOSS", "INVALIDATION"] else 180
                if elapsed < cooldown_dur:
                    continue

            try:
                sig = evaluate_scalp_signals(symbol=symbol, interval="5m")
                action = sig.get("signal")
                conviction = sig.get("conviction")
                journal({"symbol": symbol, "signal": action, "conviction": conviction,
                         "setup": sig.get("primary_setup"), "price": sig.get("current_price"),
                         "rsi": sig.get("rsi"), "rel_vol": sig.get("rel_vol"),
                         "vetoes": sig.get("veto_reasons", []), "reason": sig.get("reason", "")[:200]})

                # One entry per signal bar: don't re-enter the same completed-bar signal after an exit
                bar_key = (symbol, sig.get("timestamp"))
                if action in ["CALL", "PUT"] and bar_key == getattr(self, "last_entry_bar", {}).get(symbol):
                    continue

                # Require a directional trigger with zero vetoes
                if action in ["CALL", "PUT"] and conviction in ["HIGH", "MEDIUM"]:
                    log(f"🔍 [SIGNAL DETECTED] {symbol} {action} ({conviction}) | Setup: {sig.get('primary_setup')}")

                    rec = select_best_contract(symbol=symbol, action=action, config=self.config)
                    if not rec or not rec.get("contract"):
                        continue

                    c = rec["contract"]
                    # Ensure bid/ask spread is tight (<= $0.04)
                    max_spr = self.config.get("max_bid_ask_spread_usd", 0.04)
                    if c.get("spread", 0.04) > max_spr:
                        log(f"Spread too wide on {c['contract_symbol']}: ${c.get('spread')} > ${max_spr}")
                        continue

                    entry_px = rec.get("entry_price", c.get("ask", 0.50))
                    qty = max(1, rec.get("sizing", {}).get("suggested_contracts", rec.get("sizing", {}).get("contracts", 1)))

                    t = rec.get("targets", {})
                    stop_px = t.get("stop_loss_price") or t.get("stop_price") or round(entry_px * 0.80, 2)
                    tp1_px = t.get("tp1_price") or t.get("target_1") or round(entry_px * 1.30, 2)
                    tp2_px = t.get("tp2_price") or t.get("target_2") or round(entry_px * 1.80, 2)

                    # Open position on paper
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
                        setup_name=sig.get("primary_setup", "Autonomous Scalp"),
                        entry_spot_price=sig.get("current_price", 0.0),
                        iv=c.get("iv", 0.16)
                    )

                    # Attach underlying stop for invalidation monitoring
                    active_now = load_active_positions()
                    for p in active_now:
                        if p["id"] == pos["id"]:
                            p["underlying_stop"] = sig.get("underlying_stop", 0.0)
                    save_active_positions(active_now)

                    log("=" * 70)
                    log(f"⚡ [AUTONOMOUS 0DTE ENTRY] {qty}x {c['contract_symbol']} ({action})")
                    if not hasattr(self, "last_entry_bar"):
                        self.last_entry_bar = {}
                    self.last_entry_bar[symbol] = bar_key
                    journal({"symbol": symbol, "event": "ENTRY", "action": action, "contract": c["contract_symbol"],
                             "qty": qty, "entry_price": entry_px, "setup": sig.get("primary_setup")})
                    log(f"   Spot: ${sig.get('current_price', 0.0):.2f} | Entry: ${entry_px:.2f} | Stop: ${stop_px:.2f} | TP1: ${tp1_px:.2f} | TP2: ${tp2_px:.2f}")
                    log(f"   Max Risk: ${rec.get('sizing', {}).get('projected_max_loss', 0.0):.2f} | Outlay: ${rec.get('sizing', {}).get('total_outlay', 0.0):.2f}")
                    log("=" * 70)

                    # Single position rule: exit loop immediately
                    break

            except Exception as e:
                log(f"Error scanning {symbol}: {e}")

    def run_loop(self):
        """Main autonomous scalper loop."""
        log(f"⚡ 0DTE Autonomous Scalper Daemon v2.0 started. PID={os.getpid()}")
        log(f"Monitoring underlyings: {', '.join(self.underlyings)} | Stop: -{self.config.get('stop_loss_pct', 0.2)*100:.0f}% | TP: +{self.config.get('take_profit_1_pct', 0.3)*100:.0f}% / +{self.config.get('take_profit_2_pct', 0.8)*100:.0f}% | Max hold: {self.max_hold_time_minutes}m")

        loop_count = 0
        while True:
            try:
                # 1. Manage current positions (check stop-loss, take-profits, invalidation)
                self.manage_active_positions()

                # 2. Scan for new entries every 15s during market hours
                if time.time() - self.last_screen_time >= 15.0:
                    self.scan_and_enter_setups()
                    self.last_screen_time = time.time()

                # Paper iron-fly premium seller (independent ledger)
                try:
                    premium_tick(self.config, log=log)
                except Exception as e:
                    log(f"[IRON FLY] tick error: {e}")

                # IV calibration sample every 30 min while the market is open
                if time.time() - getattr(self, "last_iv_cal", 0) >= 1800:
                    if get_market_session_state(self.config).get("is_market_open"):
                        for sym in self.underlyings:
                            try:
                                log_iv_calibration(sym)
                            except Exception as e:
                                log(f"IV calibration failed for {sym}: {e}")
                    self.last_iv_cal = time.time()

                # 3. Update status telemetry every ~8s
                if loop_count % 1 == 0:
                    self.update_telemetry()

                loop_count += 1
                time.sleep(8)

            except KeyboardInterrupt:
                log("0DTE Scalper daemon stopping on KeyboardInterrupt...")
                break
            except Exception as e:
                log(f"Daemon exception: {e}")
                log(traceback.format_exc())
                time.sleep(10)


def start_daemon():
    """Starts the 0DTE scalper daemon in background."""
    import subprocess
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            print(f"0DTE Scalper daemon already running (PID {pid})")
            return
        except OSError:
            pass

    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "_daemon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    PID_FILE.write_text(str(proc.pid))
    print(f"[✓] 0DTE Autonomous Scalper daemon successfully started (PID {proc.pid})")
    print(f"    Logging to: {LOG_FILE}")


def stop_daemon():
    """Stops the 0DTE scalper daemon."""
    if not PID_FILE.exists():
        print("No 0DTE runner.pid file found.")
        return

    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"[✓] Stopped 0DTE Scalper daemon (PID {pid})")
        PID_FILE.unlink(missing_ok=True)
    except Exception as e:
        print(f"Failed to stop 0DTE daemon: {e}")


def status_daemon():
    """Prints current status of the 0DTE scalper daemon."""
    if STATUS_FILE.exists():
        try:
            with open(STATUS_FILE) as f:
                data = json.load(f)
            print("\n" + "=" * 70)
            print("  0DTE AUTONOMOUS SCALPER TELEMETRY v2.0")
            print("=" * 70)
            print(f" Status: {'RUNNING' if data.get('daemon_running') else 'STOPPED'} (PID {data.get('pid')})")
            print(f" Session: {data.get('session', {}).get('session_phase')}")
            print(f" Active Positions: {data.get('active_positions_count', 0)}")
            print(f" Today's Realized P&L: ${data.get('today_stats', {}).get('realized_pnl_usd', 0.0):,.2f}")
            print("=" * 70 + "\n")
            return
        except Exception:
            pass
    print("0DTE Scalper daemon status unavailable.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "start":
            start_daemon()
        elif cmd == "stop":
            stop_daemon()
        elif cmd == "status":
            status_daemon()
        elif cmd == "_daemon":
            daemon = ZeroDteScalperDaemon()
            daemon.run_loop()
        else:
            print(f"Unknown command: {cmd}")
            print("Usage: python3 runner.py [start|stop|status]")
    else:
        # Default start
        start_daemon()
