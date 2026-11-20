"""Option marking and ledger-integrity tests (v4.0.3)."""
import json, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from zero_dte_scalper.src import tracker as T
from zero_dte_scalper.src.greeks import black_scholes_price as bs


def test_implied_vol_round_trip():
    tte = T.tte_at("2026-11-19 14:00:00 EST")
    iv = T.implied_vol(1.94, 649.66, 649.0, tte, "put")
    assert abs(bs(649.66, 649.0, tte, iv, option_type="put") - 1.94) < 0.005


def test_nov19_put_marks_near_intrinsic_not_31_dollars():
    """The bug: entry 1.94 at spot 649.66 was marked 31.56 at 15:50 with spot 646.58 (intrinsic 2.42)."""
    tte_e, tte_x = T.tte_at("2026-11-19 14:00:00 EST"), T.tte_at("2026-11-19 15:50:00 EST")
    iv = T.implied_vol(1.94, 649.66, 649.0, tte_e, "put")
    px = bs(646.58, 649.0, tte_x, iv, option_type="put")
    assert 2.42 <= px < 3.5


def _ledger(tmp_path, monkeypatch, records):
    f = tmp_path / "trade_history.json"
    f.write_text(json.dumps(records))
    monkeypatch.setattr(T, "TRADE_HISTORY_FILE", f)
    return f


def test_remark_fixes_inflated_exit_and_drops_duplicates(tmp_path, monkeypatch):
    base = dict(id="a", symbol="QQQ", action="PUT", strike=649.0, entry_price=1.94, entry_spot_price=649.66,
                entry_timestamp="2026-11-19 14:00:00 EST", exit_timestamp="2026-11-19 15:50:00 EST",
                exit_price=31.56, exit_spot_price=646.58, quantity_closed=1, realized_pnl_usd=2962.0)
    old = dict(base, id="z", entry_timestamp="2026-11-01 10:00:00 EST", realized_pnl_usd=-5.0)
    f = _ledger(tmp_path, monkeypatch, [old, base, dict(base, exit_timestamp="2026-11-19 15:50:08 EST")])
    assert T.remark_momentum_history() == 2
    h = json.loads(f.read_text())
    assert len(h) == 2 and h[0] == old                       # pre-momentum records untouched
    r = h[1]
    assert r["orig_realized_pnl_usd"] == 2962.0 and r["remarked"]
    assert 2.3 <= r["exit_price"] < 3.5 and r["realized_pnl_usd"] < 200
    assert T.remark_momentum_history() == 0                   # idempotent


def test_dashboard_marking_does_not_resurrect_closed_position(tmp_path, monkeypatch):
    f = tmp_path / "active.json"
    f.write_text("[]")
    monkeypatch.setattr(T, "ACTIVE_POSITIONS_FILE", f)
    monkeypatch.setattr(T, "load_active_positions", lambda: json.loads(f.read_text()))
    assert T.update_active_positions_quotes(save=False) == []
