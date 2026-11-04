"""
Background Daemon & Automation Scheduler for Spinach Three-Day Drop-Bounce.
Monitors NYSE market hours, executes the 2:55 PM CST protocol automatically,
and maintains real-time telemetry in data/live_status.json.
"""

import os
import sys
import time
import json
import signal
import datetime
import traceback
from pathlib import Path
from typing import Dict, Any, Optional

from src.calendar_util import is_trading_day, count_trading_days_held
from src.ledger import Ledger
from src.screener import DropBounceScreener
from src.engine import PureCloneEngine

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PID_FILE = DATA_DIR / "runner.pid"
LOG_FILE = DATA_DIR / "runner.log"
LIVE_STATUS_FILE = DATA_DIR / "live_status.json"


def log(msg: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


class StockTraderDaemon:
    def __init__(self):
        self.ledger = Ledger(
            portfolio_path=str(DATA_DIR / "portfolio.json"),
            history_path=str(DATA_DIR / "history.json"),
            config_path=str(BASE_DIR / "config.json"),
        )
        self.screener = DropBounceScreener()
        self.engine = PureCloneEngine(ledger=self.ledger, screener=self.screener)
        self.last_execution_date: Optional[datetime.date] = None
        self.last_screen_time: float = 0.0
        self.cached_candidates: list = []

    def get_cst_now(self) -> datetime.datetime:
        """Returns current time in US Central Time (America/Chicago)."""
        import pytz
        cst_tz = pytz.timezone("America/Chicago")
        return datetime.datetime.now(cst_tz).replace(tzinfo=None)

    def calculate_next_run(self) -> Dict[str, Any]:
        """Calculates exact time until the next 2:55 PM CST window."""
        now = self.get_cst_now()
        today = now.date()

        # Target time is 14:55 CST
        target_today = datetime.datetime.combine(today, datetime.time(14, 55, 0))

        if is_trading_day(today) and now < target_today and self.last_execution_date != today:
            next_run_dt = target_today
            note = "Today at 2:55 PM CST"
        else:
            # Move to next trading day
            cur = today + datetime.timedelta(days=1)
            while not is_trading_day(cur):
                cur += datetime.timedelta(days=1)
            next_run_dt = datetime.datetime.combine(cur, datetime.time(14, 55, 0))
            note = f"{cur.strftime('%A, %b %d')} at 2:55 PM CST"

        diff_seconds = max(0, int((next_run_dt - now).total_seconds()))
        hours = diff_seconds // 3600
        mins = (diff_seconds % 3600) // 60
        secs = diff_seconds % 60
        countdown_str = f"{hours}h {mins}m {secs}s" if hours > 0 else f"{mins}m {secs}s"

        return {
            "target_datetime": next_run_dt.isoformat(),
            "countdown_seconds": diff_seconds,
            "countdown_str": countdown_str,
            "note": note,
            "is_trading_day": is_trading_day(today),
        }

    def update_telemetry(self, forced: bool = False):
        """Updates data/live_status.json for dashboard consumers."""
        now = self.get_cst_now()
        today = now.date()

        # 1. Update holdings valuations first (fast: < 0.5s)
        held = self.ledger.get_active_tickers()
        if held:
            try:
                prices = self.screener.get_current_prices(held)
                self.ledger.update_portfolio_valuation(prices, today)
            except Exception as e:
                log(f"Warning: valuation update failed: {e}")

        # 2. Update screener cache every 3 minutes during market hours, or if forced
        if forced or (time.time() - self.last_screen_time > 180):
            try:
                self.cached_candidates = self.screener.scan_3day_drops(
                    held_tickers=self.ledger.get_active_tickers(),
                    limit=10
                )
                self.last_screen_time = time.time()
            except Exception as e:
                log(f"Warning: screener telemetry update failed: {e}")

        # Prepare audit / signals preview
        pending_sells = []
        pending_buys = []
        for slot_id in range(1, self.ledger.max_positions + 1):
            pos = self.ledger.positions.get(slot_id)
            if pos:
                pnl = pos.get("unrealized_pnl_pct", 0.0)
                days = pos.get("days_held", 0)
                if pnl >= 3.0:
                    pending_sells.append({"slot": slot_id, "ticker": pos["ticker"], "reason": "Target Hit (+3%)", "pnl": pnl})
                elif days >= 5:
                    pending_sells.append({"slot": slot_id, "ticker": pos["ticker"], "reason": "Day 5 Time Exit", "pnl": pnl})

        open_slots = self.ledger.get_open_slots()
        open_count = len(open_slots) + len(pending_sells)
        if open_count > 0 and self.cached_candidates:
            # Estimate allocation
            budget = min(self.ledger.account_equity * 0.20, self.ledger.available_cash / max(1, open_count))
            for i in range(min(open_count, len(self.cached_candidates))):
                cand = self.cached_candidates[i]
                price = cand["current_price"]
                shares = int(budget // price) if price > 0 else 0
                pending_buys.append({
                    "ticker": cand["ticker"],
                    "name": cand["name"],
                    "shares": shares,
                    "price": price,
                    "return_3d_pct": cand["return_3d_pct"],
                })

        next_run = self.calculate_next_run()

        # Read recent history
        recent_history = []
        if (DATA_DIR / "history.json").exists():
            try:
                with open(DATA_DIR / "history.json") as f:
                    recent_history = json.load(f)[-10:][::-1]
            except Exception:
                pass

        payload = {
            "daemon_running": True,
            "pid": os.getpid(),
            "updated_at": now.isoformat(),
            "current_time_cst": now.strftime("%I:%M:%S %p CST"),
            "today_date": today.isoformat(),
            "is_trading_day": is_trading_day(today),
            "next_run": next_run,
            "last_execution_date": self.last_execution_date.isoformat() if self.last_execution_date else None,
            "account_equity": self.ledger.account_equity,
            "available_cash": self.ledger.available_cash,
            "positions": self.ledger.positions,
            "pending_sells": pending_sells,
            "pending_buys": pending_buys,
            "top_candidates": self.cached_candidates[:8],
            "recent_history": recent_history,
        }

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LIVE_STATUS_FILE, "w") as f:
            json.dump(payload, f, indent=2)

    def check_and_execute(self):
        """Checks if current time is within the 2:55 PM - 3:00 PM CST execution window."""
        now = self.get_cst_now()
        today = now.date()

        if not is_trading_day(today):
            return

        if self.last_execution_date == today:
            return

        # Target window: 14:55:00 to 14:59:45 CST
        if now.hour == 14 and now.minute >= 55 and now.minute <= 59:
            log("=" * 70)
            log(">>> TRIGGERING 2:55 PM CST DAILY EXECUTION PROTOCOL <<<")
            log("=" * 70)

            try:
                audit = self.engine.run_daily_audit(as_of_date=today)
                sells = audit.get("sell_orders", [])
                buys = audit.get("buy_orders", [])

                log(f"Holding Audit: {len(sells)} sells triggered, {audit.get('open_slot_count', 0)} open slots.")
                for s in sells:
                    log(f"SELL Slot {s['slot']}: {s['shares']} {s['ticker']} @ ~${s['est_price']} ({s['reason']})")
                for b in buys:
                    log(f"BUY Slot {b['slot']}: {b['shares']} {b['ticker']} @ ~${b['est_price']} (3D Drop: {b['return_3d_pct']}%)")

                # Commit execution to ledger
                res = self.engine.execute_plan(audit, execution_date=today)
                self.last_execution_date = today

                log(f"[✓] Daily execution completed. Cash: ${res['final_cash']:,.2f}, Equity: ${res['final_equity']:,.2f}")
                log("=" * 70)

                # Update live telemetry immediately
                self.update_telemetry(forced=True)

            except Exception as e:
                log(f"[ERROR] Execution failed with exception: {e}")
                log(traceback.format_exc())

    def run_loop(self):
        """Main daemon loop."""
        log(f"Stock Trader Daemon started. PID={os.getpid()}")
        log("Monitoring NYSE market hours for 2:55 PM CST closing execution window...")

        # Write initial telemetry immediately
        self.update_telemetry(forced=True)

        loop_count = 0
        while True:
            try:
                self.check_and_execute()

                # Update telemetry every ~15 seconds
                if loop_count % 1 == 0:
                    self.update_telemetry()

                loop_count += 1
                time.sleep(15)

            except KeyboardInterrupt:
                log("Daemon stopping on KeyboardInterrupt...")
                break
            except Exception as e:
                log(f"Daemon loop exception: {e}")
                time.sleep(15)


def start_daemon():
    """Starts the daemon process in background."""
    import subprocess
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            print(f"Daemon already running (PID {pid})")
            return
        except OSError:
            pass

    # Launch daemon cleanly without fork deadlocks
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "_daemon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    PID_FILE.write_text(str(proc.pid))
    print(f"[✓] Stock Trader daemon successfully started in background (PID {proc.pid})")
    print(f"    Logging to: {LOG_FILE}")


def stop_daemon():
    """Stops the running daemon."""
    if not PID_FILE.exists():
        print("No runner.pid file found.")
        return

    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"[✓] Stopped daemon (PID {pid})")
        PID_FILE.unlink(missing_ok=True)
    except Exception as e:
        print(f"Failed to stop daemon: {e}")


def status_daemon():
    """Prints current status of the daemon."""
    if LIVE_STATUS_FILE.exists():
        try:
            with open(LIVE_STATUS_FILE) as f:
                data = json.load(f)
            print("\n" + "=" * 70)
            print("  STOCK TRADER DAEMON TELEMETRY")
            print("=" * 70)
            print(f" Status: {'RUNNING' if data.get('daemon_running') else 'STOPPED'} (PID {data.get('pid')})")
            print(f" Time: {data.get('current_time_cst')} | Today: {data.get('today_date')} (Trading Day: {data.get('is_trading_day')})")
            next_run = data.get("next_run", {})
            print(f" Next Execution: {next_run.get('note')} (in {next_run.get('countdown_str')})")
            print(f" Account Equity: ${data.get('account_equity', 0):,.2f} | Cash: ${data.get('available_cash', 0):,.2f}")
            print(f" Active Positions: {len(data.get('positions', {}))}/5 Slots")
            print("=" * 70 + "\n")
            return
        except Exception:
            pass

    print("No live telemetry file found. Run 'python3 runner.py start' to initiate.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        start_daemon()
    elif cmd == "_daemon":
        d = StockTraderDaemon()
        d.run_loop()
    elif cmd == "stop":
        stop_daemon()
    elif cmd == "status":
        status_daemon()
    elif cmd == "run-now":
        d = StockTraderDaemon()
        d.update_telemetry(forced=True)
        audit = d.engine.run_daily_audit()
        print(json.dumps(audit, indent=2))
    else:
        print("Usage: python3 runner.py {start|stop|status|run-now}")
