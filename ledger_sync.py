"""
Persist the paper-trading ledgers to a GitHub branch so they survive Render redeploys
(the free tier's disk is wiped on every deploy and restart).

- restore(): on container start, download each ledger file from the branch (if present).
- sync():    commit every changed ledger file to the branch in ONE commit (Git Data API).
- Without GITHUB_TOKEN: restore() reads the public branch anonymously, and the
  .github/workflows/ledger-sync.yml Action pulls /api/ledger and commits it (no secrets needed).
  sync() is then a no-op.

Env:
  GITHUB_TOKEN   fine-grained PAT with "Contents: read and write" on the repo
  GITHUB_REPO    owner/name (default Schmiedey/stock-trader)
  LEDGER_BRANCH  branch to store ledgers on (default paper-ledger)
"""
from __future__ import annotations

import os
import json
import base64
import hashlib
import ssl
import urllib.request
import urllib.error
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA = "zero_dte_scalper/data"
FILES = [
    f"{DATA}/active_positions.json",
    f"{DATA}/trade_history.json",
    f"{DATA}/premium_positions.json",
    f"{DATA}/premium_history.json",
    f"{DATA}/signal_journal.jsonl",
    f"{DATA}/iv_calibration.jsonl",
]
API = "https://api.github.com"

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL = ssl.create_default_context()

_last_hash: dict[str, str] = {}


def _cfg():
    return (os.environ.get("GITHUB_TOKEN", "").strip(),
            os.environ.get("GITHUB_REPO", "Schmiedey/stock-trader"),
            os.environ.get("LEDGER_BRANCH", "paper-ledger"))


def enabled() -> bool:
    return bool(_cfg()[0])


def _req(method: str, path: str, body: dict | None = None, raw: bool = False):
    token, _, _ = _cfg()
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.raw+json" if raw else "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "scalper-ledger-sync")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
        data = r.read()
        return data if raw else (json.loads(data) if data else {})


def _branch_head() -> str | None:
    _, repo, branch = _cfg()
    try:
        return _req("GET", f"/repos/{repo}/git/ref/heads/{branch}")["object"]["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _ensure_branch() -> str:
    """Return the branch head sha, creating the branch (as an orphan-free child of main) if missing."""
    head = _branch_head()
    if head:
        return head
    _, repo, branch = _cfg()
    main_sha = _req("GET", f"/repos/{repo}/git/ref/heads/main")["object"]["sha"]
    _req("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": main_sha})
    return main_sha


def _hash(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def _restore_public(log=print) -> int:
    """Anonymous restore from raw.githubusercontent.com (public repo, no token)."""
    _, repo, branch = _cfg()
    n = 0
    for rel in FILES:
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/{rel}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "scalper-ledger-sync"})
            with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
        p = BASE_DIR / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        n += 1
    log(f"[LEDGER] restored {n} ledger file(s) from public {repo}@{branch} (GitHub Action keeps it updated)")
    return n


def snapshot() -> dict:
    """Current ledger files as {path: text}, served at /api/ledger for the sync Action."""
    out = {}
    for rel in FILES:
        p = BASE_DIR / rel
        if p.exists():
            out[rel] = p.read_text()
    return out


def restore(log=print) -> int:
    """Download ledgers from the branch into the working tree. Returns files restored."""
    if not enabled():
        return _restore_public(log)
    _, repo, branch = _cfg()
    if _branch_head() is None:
        log(f"[LEDGER] branch '{branch}' not found yet; it will be created on first sync")
        return 0
    n = 0
    for rel in FILES:
        try:
            data = _req("GET", f"/repos/{repo}/contents/{rel}?ref={branch}", raw=True)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
        p = BASE_DIR / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        _last_hash[rel] = _hash(data)
        n += 1
    log(f"[LEDGER] restored {n} ledger file(s) from {repo}@{branch}")
    return n


def sync(log=print, message: str = "paper ledger sync") -> int:
    """Commit changed ledger files to the branch in one commit. Returns files committed."""
    if not enabled():
        return 0
    changed = {}
    for rel in FILES:
        p = BASE_DIR / rel
        if not p.exists():
            continue
        b = p.read_bytes()
        h = _hash(b)
        if _last_hash.get(rel) != h:
            changed[rel] = (b, h)
    if not changed:
        return 0
    _, repo, branch = _cfg()
    head = _ensure_branch()
    base_tree = _req("GET", f"/repos/{repo}/git/commits/{head}")["tree"]["sha"]
    tree = []
    for rel, (b, _) in changed.items():
        blob = _req("POST", f"/repos/{repo}/git/blobs",
                    {"content": base64.b64encode(b).decode(), "encoding": "base64"})
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    new_tree = _req("POST", f"/repos/{repo}/git/trees", {"base_tree": base_tree, "tree": tree})["sha"]
    commit = _req("POST", f"/repos/{repo}/git/commits",
                  {"message": f"{message} ({len(changed)} file(s))", "tree": new_tree, "parents": [head]})["sha"]
    _req("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", {"sha": commit, "force": False})
    for rel, (_, h) in changed.items():
        _last_hash[rel] = h
    log(f"[LEDGER] synced {len(changed)} file(s) to {repo}@{branch} ({commit[:7]})")
    return len(changed)
