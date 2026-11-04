"""
Cloud Container Entrypoint Supervisor.
Runs the complete Stock Trader Quant Desk 24/7 on any cloud platform:
1. Spinach 3D Drop Automation Daemon (runner.py)
2. 0DTE Options Scalper Automation Daemon (zero_dte_scalper/runner.py)
3. Quant Terminal Dashboard Server (dashboard/server.py)
"""

import os
import sys
import time
import signal
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SCALPER_DATA_DIR = BASE_DIR / "zero_dte_scalper/data"

DATA_DIR.mkdir(parents=True, exist_ok=True)
SCALPER_DATA_DIR.mkdir(parents=True, exist_ok=True)

processes = {}


def log(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [CLOUD-SUPERVISOR] {msg}", flush=True)


def start_process(name: str, cmd: list) -> subprocess.Popen:
    log(f"Starting {name}: {' '.join(cmd)}")
    p = subprocess.Popen(cmd, cwd=str(BASE_DIR))
    processes[name] = {"process": p, "cmd": cmd}
    return p


def shutdown_handler(signum, frame):
    log(f"Received signal {signum}. Initiating graceful shutdown of all trading services...")
    for name, item in processes.items():
        proc = item["process"]
        if proc.poll() is None:
            log(f"Terminating {name} (PID {proc.pid})...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    log("All cloud trading services stopped cleanly.")
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    port = os.environ.get("PORT", "8791")
    host = os.environ.get("HOST", "0.0.0.0")

    log("=" * 70)
    log("  STOCK TRADER 24/7 CLOUD QUANT TERMINAL INITIALIZING")
    log(f"  Public Binding: http://{host}:{port}")
    log("=" * 70)

    # 1. Start Spinach Equities Daemon
    start_process(
        "Spinach_Equities_Daemon",
        [sys.executable, str(BASE_DIR / "runner.py"), "_daemon"]
    )

    # 2. Start 0DTE Scalper Daemon
    start_process(
        "0DTE_Scalper_Daemon",
        [sys.executable, str(BASE_DIR / "zero_dte_scalper/runner.py"), "_daemon"]
    )

    # 3. Start Dashboard Server
    start_process(
        "Dashboard_Server",
        [sys.executable, str(BASE_DIR / "dashboard/server.py"), port, host]
    )

    # Supervisor loop
    while True:
        for name, item in list(processes.items()):
            proc = item["process"]
            ret = proc.poll()
            if ret is not None:
                log(f"WARNING: Service {name} exited with code {ret}. Restarting in 3 seconds...")
                time.sleep(3)
                start_process(name, item["cmd"])
        time.sleep(5)


if __name__ == "__main__":
    main()
