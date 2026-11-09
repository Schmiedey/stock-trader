# 0DTE Scalper Desk

Paper-trading 0DTE options scalper for SPY / QQQ, plus a live dashboard.

- Strategy, risk rules, and CLI: [zero_dte_scalper/README.md](zero_dte_scalper/README.md)
- Dashboard: `venv/bin/python dashboard/server.py 8791 127.0.0.1`, then open http://localhost:8791
- Scalper daemon: `venv/bin/python zero_dte_scalper/runner.py _daemon`
- Cloud (Docker / Render): `cloud_entrypoint.py` runs the daemon and the dashboard together.
