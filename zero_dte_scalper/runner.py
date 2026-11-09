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

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(SCALPER_DIR))

from zero_dte_scalper.src.config import load_config, get_market_session_state
from zero_dte_scalper.src.signals import evaluate_scalp_signals
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
            curr_px = pos.get("current_price", pos["entry_price"])
            pnl_pct = pos.get("unrealized_pnl_pct", 0.0)
            pnl_usd = pos.get("unrealized_pnl_usd", 0.0)
            stop_pct = self.config.get("stop_loss_pct", 0.20)
            tp1_pct = self.config.get("take_profit_1_pct", 0.30)
            tp2_pct = self.config.get("take_profit_2_pct", 0.80)
            stop_px = pos.get("stop_loss_price", pos["entry_price"] * (1 - stop_pct))
            tp1_px = pos.get("tp1_price", pos["entry_price"] * (1 + tp1_pct))
            tp2_px = pos.get("tp2_price", pos["entry_price"] * (1 + tp2_pct))
            elapsed_mins = pos.get("elapsed_minutes", 0.0)
            spot_px = pos.get("current_spot_price", 0.0)
            underlying_stop = pos.get("underlying_stop", 0.0)

            # Trigger 1: Stop-Loss on premium. Requires two consecutive checks below the
            # stop so a single bad print can't stop us out (it did, repeatedly).
            breach_count = pos.get("stop_breach_count", 0)
            if curr_px <= stop_px:
                breach_count += 1
            else:
                breach_count = 0
            if breach_count != pos.get("stop_breach_count", 0):
                active_now = load_active_positions()
                for p in active_now:
                    if p["id"] == pos_id:
                        p["stop_breach_count"] = breach_count
                save_active_positions(active_now)
            if breach_count == 1:
                log(f"⚠️ [STOP WATCH] {contract_sym}: ${curr_px:.2f} <= stop ${stop_px:.2f}. Confirming on next check.")
            if breach_count >= 2:
                label = "BREAKEVEN_STOP" if pos.get("tp1_hit") else f"STOP_LOSS_HIT (-{stop_pct*100:.0f}%)"
                log(f"🛑 [{label}] {contract_sym}: Quote ${curr_px:.2f} <= Stop ${stop_px:.2f} ({pnl_pct:.1f}% | ${pnl_usd:,.2f})")
                try:
                    close_position(position_id=pos_id, exit_price=curr_px, reason=label)
                    self.last_exit_times[pos["symbol"]] = (time.time(), "STOP_LOSS")
                    log(f"[✓] Position {contract_sym} closed on stop loss.")
                except Exception as e:
                    log(f"Error closing stop-loss: {e}")
                continue

            # Trigger 2: Target 2 on premium
            if curr_px >= tp2_px:
                log(f"🎯 [TARGET 2 HIT] {contract_sym}: Quote ${curr_px:.2f} >= TP2 ${tp2_px:.2f} (+{pnl_pct:.1f}% | +${pnl_usd:,.2f})")
                try:
                    close_position(position_id=pos_id, exit_price=curr_px, reason=f"TARGET_2_HIT (+{tp2_pct*100:.0f}%)")
                    self.last_exit_times[pos["symbol"]] = (time.time(), "TARGET_2")
                    log(f"[✓] Position {contract_sym} closed at Target 2.")
                except Exception as e:
                    log(f"Error closing target 2: {e}")
                continue

            # Trigger 3: Target 1 on premium: scale out half, move stop to locked profit
            if curr_px >= tp1_px and not pos.get("tp1_hit"):
                if pos.get("current_quantity", 1) > 1:
                    half_qty = pos["current_quantity"] // 2
                    log(f"💰 [TARGET 1 HIT] {contract_sym}: Quote ${curr_px:.2f} >= TP1 ${tp1_px:.2f}. Scaling out {half_qty} contracts.")
                    try:
                        close_position(position_id=pos_id, exit_price=curr_px, reason=f"TARGET_1_SCALE_OUT (+{tp1_pct*100:.0f}%)", partial_qty=half_qty)
                        # Move stop to entry plus cushion so the runner can't turn into a loss
                        cushion = self.config.get("breakeven_cushion_usd", 0.02)
                        protected_stop = round(pos["entry_price"] + cushion, 2)
                        active_now = load_active_positions()
                        for p in active_now:
                            if p["id"] == pos_id:
                                p["tp1_hit"] = True
                                p["stop_loss_price"] = protected_stop
                                p["stop_breach_count"] = 0
                        save_active_positions(active_now)
                        log(f"[✓] Stop moved to breakeven buffer (${protected_stop:.2f}) for remaining {contract_sym} contracts.")
                    except Exception as e:
                        log(f"Error scaling out at target 1: {e}")
                else:
                    log(f"💰 [TARGET 1 SECURED] {contract_sym}: Quote ${curr_px:.2f} (+{pnl_pct:.1f}%). Closing 1-contract position.")
                    try:
                        close_position(position_id=pos_id, exit_price=curr_px, reason=f"TARGET_1_FULL (+{tp1_pct*100:.0f}%)")
                        self.last_exit_times[pos["symbol"]] = (time.time(), "TARGET_1")
                    except Exception as e:
                        log(f"Error closing target 1: {e}")
                continue  # position changed this tick; re-evaluate next loop

            # Trigger 4: Stagnation / Theta Defense Exit (Held >= 25m without reaching +10%)
            if elapsed_mins >= self.max_hold_time_minutes and pnl_pct < 10.0:
                log(f"⏳ [STAGNATION EXIT] {contract_sym}: Open for {elapsed_mins:.1f}m without momentum (+{pnl_pct:.1f}%). Exiting to eliminate theta decay.")
                try:
                    close_position(position_id=pos_id, exit_price=curr_px, reason="STAGNATION_TIME_EXIT")
                    self.last_exit_times[pos["symbol"]] = (time.time(), "STAGNATION")
                except Exception as e:
                    log(f"Error closing stagnation exit: {e}")
                continue

            # Trigger 5: Underlying Invalidation Exit (Underlying broken beyond key technical stop)
            if underlying_stop > 0 and spot_px > 0:
                is_invalid = False
                if pos["action"] == "CALL" and spot_px < underlying_stop:
                    is_invalid = True
                    log(f"⚠️ [UNDERLYING INVALIDATION] {contract_sym}: Spot ${spot_px:.2f} broke below technical support ${underlying_stop:.2f}.")
                elif pos["action"] == "PUT" and spot_px > underlying_stop:
                    is_invalid = True
                    log(f"⚠️ [UNDERLYING INVALIDATION] {contract_sym}: Spot ${spot_px:.2f} broke above technical resistance ${underlying_stop:.2f}.")

                if is_invalid:
                    try:
                        close_position(position_id=pos_id, exit_price=curr_px, reason="UNDERLYING_INVALIDATION_EXIT")
                        self.last_exit_times[pos["symbol"]] = (time.time(), "INVALIDATION")
                    except Exception as e:
                        log(f"Error closing underlying invalidation: {e}")
                    continue

            # Trigger 6: Mandatory End-Of-Day Expiration Exit (15:50 EST)
            if session_phase in ["MANDATORY_FLAT", "MANDATORY_EXIT", "CLOSED"]:
                log(f"⏰ [MANDATORY EOD EXIT] {contract_sym}: Closing before 4:00 PM EST bell @ ${curr_px:.2f}")
                try:
                    close_position(position_id=pos_id, exit_price=curr_px, reason="MANDATORY_EOD_EXPIRATION_EXIT")
                except Exception as e:
                    log(f"Error closing EOD: {e}")

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

                # Require high or medium conviction directional trigger with zero vetoes
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
        log(f"Monitoring underlyings: {', '.join(self.underlyings)} | Hard Stop: -15% | Take-Profit: +20% / +40% | Stagnation: 25m")

        loop_count = 0
        while True:
            try:
                # 1. Manage current positions (check stop-loss, take-profits, invalidation)
                self.manage_active_positions()

                # 2. Scan for new entries every 15s during market hours
                if time.time() - self.last_screen_time >= 15.0:
                    self.scan_and_enter_setups()
                    self.last_screen_time = time.time()

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
