# 0DTE Scalper Desk

Paper-trading 0DTE options scalper for SPY / QQQ, plus a live dashboard.

- Strategy, risk rules, and CLI: [zero_dte_scalper/README.md](zero_dte_scalper/README.md)
- Dashboard: `venv/bin/python dashboard/server.py 8791 127.0.0.1`, then open http://localhost:8791
- Scalper daemon: `venv/bin/python zero_dte_scalper/runner.py _daemon`
- Cloud (Docker / Render): `cloud_entrypoint.py` runs the daemon and the dashboard together.

## Paper ledger persistence (Render)

Render's free disk is wiped on every deploy. `ledger_sync.py` restores the paper ledgers from the
`paper-ledger` branch on startup and commits changes back every 5 minutes and on shutdown.

Setup: in Render → the service → Environment, add `GITHUB_TOKEN` = a fine-grained GitHub token
scoped to `Schmiedey/stock-trader` with **Contents: Read and write**. Without it the bot still runs,
but ledgers reset on each deploy (the log says so at startup).

View the cloud ledger: https://github.com/Schmiedey/stock-trader/tree/paper-ledger/zero_dte_scalper/data
