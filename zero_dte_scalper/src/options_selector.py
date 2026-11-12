"""Selection algorithm for 0DTE strikes matching Delta targets and liquidity criteria."""

from .config import load_config
from .market_data import fetch_0dte_chain


def select_best_contract(
    symbol: str = "SPY",
    action: str = "CALL",
    chain_data: dict | None = None,
    config: dict | None = None
) -> dict | None:
    """
    Select the optimal 0DTE option contract based on delta sweet spot (0.35 - 0.55),
    penny spreads, and volume.
    Returns:
        {
            "contract": dict,
            "sizing": dict,
            "targets": dict
        }
    """
    if config is None:
        config = load_config()

    if chain_data is None:
        chain_data = fetch_0dte_chain(symbol)

    action = action.upper()
    is_call = (action == "CALL")
    contracts = chain_data["calls"] if is_call else chain_data["puts"]

    if not contracts:
        return None

    target_delta_min = config.get("target_delta_min", 0.35)
    target_delta_max = config.get("target_delta_max", 0.55)
    optimal_delta = config.get("optimal_target_delta", 0.45)
    max_spread = config.get("max_bid_ask_spread_usd", 0.05)

    # Filter candidates with valid bid/ask
    valid_candidates = [
        c for c in contracts
        if c["ask"] > 0 and c["bid"] > 0
    ]

    if not valid_candidates:
        valid_candidates = [c for c in contracts if c["last_price"] > 0]

    # Filter for target delta range
    delta_filtered = []
    for c in valid_candidates:
        abs_delta = abs(c["delta"])
        if target_delta_min <= abs_delta <= target_delta_max:
            delta_filtered.append(c)

    # Fallback to ATM strikes if delta range is too narrow or missing
    if not delta_filtered:
        spot = chain_data["spot_price"]
        delta_filtered = sorted(valid_candidates, key=lambda c: abs(c["strike"] - spot))[:4]

    # Prioritize: 1) Spread <= max_spread, 2) Proximity to optimal delta, 3) High volume
    def score_candidate(c: dict) -> float:
        abs_delta = abs(c["delta"])
        delta_diff = abs(abs_delta - optimal_delta)
        spread_penalty = max(0.0, c["spread"] - max_spread) * 10.0
        return delta_diff + spread_penalty

    sorted_candidates = sorted(delta_filtered, key=score_candidate)
    best_contract = sorted_candidates[0]

    # Sizing & Risk calculations
    entry_price = best_contract["ask"] if best_contract["ask"] > 0 else best_contract["last_price"]
    stop_loss_pct = config.get("stop_loss_pct", 0.20)
    tp1_pct = config.get("take_profit_1_pct", 0.30)
    tp2_pct = config.get("take_profit_2_pct", 0.80)
    risk_budget = config.get("max_risk_per_trade_usd", 150.0)
    max_outlay = config.get("max_position_outlay_usd", 400.0)
    max_contracts_cap = config.get("max_contracts", 3)

    contract_unit_cost = entry_price * 100.0
    dollar_risk_per_contract = contract_unit_cost * stop_loss_pct

    by_risk_qty = int(risk_budget / dollar_risk_per_contract) if dollar_risk_per_contract > 0 else 1
    by_outlay_qty = int(max_outlay / contract_unit_cost) if contract_unit_cost > 0 else 1

    # Clamp quantity strictly to preserve capital
    suggested_qty = min(by_risk_qty, by_outlay_qty, max_contracts_cap)
    suggested_qty = max(1, suggested_qty)

    total_outlay = round(contract_unit_cost * suggested_qty, 2)
    total_dollar_risk = round(dollar_risk_per_contract * suggested_qty, 2)

    stop_loss_price = round(entry_price * (1.0 - stop_loss_pct), 2)
    tp1_price = round(entry_price * (1.0 + tp1_pct), 2)
    tp2_price = round(entry_price * (1.0 + tp2_pct), 2)

    return {
        "symbol": symbol,
        "expiration": chain_data["expiration"],
        "is_true_0dte": chain_data["is_true_0dte"],
        "action": action,
        "contract": best_contract,
        "entry_price": entry_price,
        "chain_spot": chain_data.get("spot_price", 0.0),
        "chain_source": chain_data.get("source", ""),
        "sizing": {
            "suggested_contracts": suggested_qty,
            "contract_unit_cost": round(contract_unit_cost, 2),
            "total_outlay": total_outlay,
            "max_risk_budget": risk_budget,
            "projected_max_loss": total_dollar_risk,
        },
        "targets": {
            "stop_loss_pct": stop_loss_pct,
            "stop_loss_price": stop_loss_price,
            "stop_price": stop_loss_price,
            "tp1_pct": tp1_pct,
            "tp1_price": tp1_price,
            "target_1": tp1_price,
            "tp2_pct": tp2_pct,
            "tp2_price": tp2_price,
            "target_2": tp2_price,
        }
    }
