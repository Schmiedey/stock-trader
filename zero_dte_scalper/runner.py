"""
Autonomous 0DTE Options Scalper Daemon.
Continuously scans intraday 5m technicals for SPY and QQQ, enters high-conviction
scalp setups on paper, and manages exits via hard -15% stop-loss and +20%/+40% profit targets.
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
        self.max_concurrent_positions = 2
        self.last_screen_time = 0.0

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
        """Check stop-loss (-15%), profit targets (+20%/+40%), and time exits."""
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
            stop_px = pos.get("stop_loss_price", pos["entry_price"] * 0.85)
            tp1_px = pos.get("tp1_price", pos["entry_price"] * 1.20)
            tp2_px = pos.get("tp2_price", pos["entry_price"] * 1.40)

            # Trigger 1: Hard Stop-Loss (-15% on premium)
            if curr_px <= stop_px or pnl_pct <= -15.0:
                log(f"🛑 [STOP LOSS HIT] {contract_sym}: Quote ${curr_px:.2f} <= Stop ${stop_px:.2f} ({pnl_pct:.1f}% | ${pnl_usd:,.2f})")
                try:
                    close_position(position_id=pos_id, exit_price=curr_px, reason="STOP_LOSS_HIT (-15%)")
                    log(f"[✓] Position {contract_sym} closed on stop loss.")
                except Exception as e:
                    log(f"Error closing stop-loss: {e}")
                continue

            # Trigger 2: Target 2 (+40% on premium)
            if curr_px >= tp2_px or pnl_pct >= 40.0:
                log(f"🎯 [TARGET 2 HIT] {contract_sym}: Quote ${curr_px:.2f} >= TP2 ${tp2_px:.2f} (+{pnl_pct:.1f}% | +${pnl_usd:,.2f})")
                try:
                    close_position(position_id=pos_id, exit_price=curr_px, reason="TARGET_2_HIT (+40%)")
                    log(f"[✓] Position {contract_sym} closed at Target 2.")
                except Exception as e:
                    log(f"Error closing target 2: {e}")
                continue

            # Trigger 3: Target 1 (+20% on premium) scale out / trailing
            if curr_px >= tp1_px and not pos.get("tp1_hit"):
                if pos.get("current_quantity", 1) > 1:
                    half_qty = pos["current_quantity"] // 2
                    log(f"💰 [TARGET 1 HIT] {contract_sym}: Quote ${curr_px:.2f} >= TP1 ${tp1_px:.2f}. Scaling out {half_qty} contracts.")
                    try:
                        close_position(position_id=pos_id, exit_price=curr_px, reason="TARGET_1_SCALE_OUT (+20%)", partial_qty=half_qty)
                        # Move stop to breakeven
                        active_now = load_active_positions()
                        for p in active_now:
                            if p["id"] == pos_id:
                                p["tp1_hit"] = True
                                p["stop_loss_price"] = p["entry_price"]
                        save_active_positions(active_now)
                        log(f"[✓] Stop moved to breakeven for remaining {contract_sym} contracts.")
                    except Exception as e:
                        log(f"Error scaling out at target 1: {e}")
                else:
                    if pnl_pct >= 25.0:
                        log(f"💰 [TARGET 1 SECURED] {contract_sym}: Quote ${curr_px:.2f} (+{pnl_pct:.1f}%). Closing 1-contract position.")
                        try:
                            close_position(position_id=pos_id, exit_price=curr_px, reason="TARGET_1_FULL (+20%)")
                        except Exception as e:
                            log(f"Error closing target 1: {e}")

            # Trigger 4: Mandatory End-Of-Day Expiration Exit (15:50 EST)
            if session_phase == "MANDATORY_EXIT" or session_phase == "CLOSED":
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

        # Check active positions count
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
            return

        active_symbols = {p["symbol"] for p in active}

        for symbol in self.underlyings:
            if symbol in active_symbols:
                continue

            try:
                sig = evaluate_scalp_signals(symbol=symbol, interval="5m")
                action = sig.get("signal")
                conviction = sig.get("conviction")

                # Require high or medium conviction directional trigger
                if action in ["CALL", "PUT"] and conviction in ["HIGH", "MEDIUM"]:
                    log(f"🔍 [SIGNAL DETECTED] {symbol} {action} ({conviction}) | Setup: {sig.get('primary_setup')}")

                    rec = select_best_contract(symbol=symbol, action=action, config=self.config)
                    if not rec or not rec.get("contract"):
                        continue

                    c = rec["contract"]
                    # Ensure bid/ask spread is tight (<= $0.05)
                    if c.get("spread", 0.05) > self.config.get("max_bid_ask_spread_usd", 0.05):
                        log(f"Spread too wide on {c['contract_symbol']}: ${c.get('spread')}")
                        continue

                    entry_px = rec.get("entry_price", c.get("ask", 0.50))
                    qty = max(1, rec.get("sizing", {}).get("suggested_contracts", rec.get("sizing", {}).get("contracts", 1)))

                    t = rec.get("targets", {})
                    stop_px = t.get("stop_loss_price") or t.get("stop_price") or round(entry_px * 0.85, 2)
                    tp1_px = t.get("tp1_price") or t.get("target_1") or round(entry_px * 1.20, 2)
                    tp2_px = t.get("tp2_price") or t.get("target_2") or round(entry_px * 1.40, 2)

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
                        setup_name=sig.get("primary_setup", "Autonomous Scalp")
                    )

                    log("=" * 70)
                    log(f"⚡ [AUTONOMOUS 0DTE ENTRY] {qty}x {c['contract_symbol']} ({action})")
                    log(f"   Entry: ${entry_px:.2f} | Stop: ${stop_px:.2f} (-15%) | TP1: ${tp1_px:.2f} (+20%) | TP2: ${tp2_px:.2f} (+40%)")
                    log("=" * 70)

                    # Update active positions list
                    active_symbols.add(symbol)
                    if len(active_symbols) >= self.max_concurrent_positions:
                        break

            except Exception as e:
                log(f"Error scanning {symbol}: {e}")

    def run_loop(self):
        """Main autonomous scalper loop."""
        log(f"⚡ 0DTE Autonomous Scalper Daemon started. PID={os.getpid()}")
        log(f"Monitoring underlyings: {', '.join(self.underlyings)} | Hard Stop: -15% | Take-Profit: +20% / +40%")

        loop_count = 0
        while True:
            try:
                # 1. Manage current positions (check stop-loss & take-profits)
                self.manage_active_positions()

                # 2. Scan for new entries every 15s during market hours
                if time.time() - self.last_screen_time >= 15.0:
                    self.scan_and_enter_setups()
                    self.last_screen_time = time.time()

                # 3. Update status telemetry every ~10s
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
            print("  0DTE AUTONOMOUS SCALPER TELEMETRY")
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
