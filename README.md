# 0DTE Scalper Desk

Paper-trading 0DTE options scalper for SPY / QQQ, plus a live dashboard.

- Strategy, risk rules, and CLI: [zero_dte_scalper/README.md](zero_dte_scalper/README.md)
- Dashboard: `venv/bin/python dashboard/server.py 8791 127.0.0.1`, then open http://localhost:8791
- Scalper daemon: `venv/bin/python zero_dte_scalper/runner.py _daemon`
- Cloud (Docker / Render): `cloud_entrypoint.py` runs the daemon and the dashboard together.

## Paper ledger persistence (Render)

Render's free disk is wiped on every deploy, so the paper ledgers live on the `paper-ledger` branch:

- On boot, `ledger_sync.py` restores them from the public branch (no token needed).
- `.github/workflows/ledger-sync.yml` pulls `GET /api/ledger` from the app every 10 minutes and commits
  any changes, using GitHub's built-in Actions token. It refuses to commit if the app's history is
  shorter than what's saved (protects against a container that failed to restore).
- Optional: set `GITHUB_TOKEN` on Render to push from the app itself every 5 minutes and on shutdown.

At most ~10 minutes of ledger activity can be lost on a redeploy (less if pushes avoid market hours).

View the cloud ledger: https://github.com/Schmiedey/stock-trader/tree/paper-ledger/zero_dte_scalper/data
