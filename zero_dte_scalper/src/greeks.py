"""Black-Scholes analytical formulas and Greek calculations for 0DTE options."""

import math
from scipy.stats import norm

RISK_FREE_RATE_DEFAULT = 0.045  # 4.5% annualized risk-free rate


def black_scholes_price(
    spot: float,
    strike: float,
    tte_years: float,
    volatility: float,
    rate: float = RISK_FREE_RATE_DEFAULT,
    option_type: str = "call"
) -> float:
    """Calculate theoretical Black-Scholes option price."""
    if tte_years <= 1e-6:
        # At expiration
        if option_type.lower() == "call":
            return max(0.0, spot - strike)
        else:
            return max(0.0, strike - spot)

    if volatility <= 1e-4:
        volatility = 0.15

    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility ** 2) * tte_years) / (volatility * math.sqrt(tte_years))
    d2 = d1 - volatility * math.sqrt(tte_years)

    if option_type.lower() == "call":
        price = spot * norm.cdf(d1) - strike * math.exp(-rate * tte_years) * norm.cdf(d2)
    else:
        price = strike * math.exp(-rate * tte_years) * norm.cdf(-d2) - spot * norm.cdf(-d1)

    return max(0.0, price)


def calculate_greeks(
    spot: float,
    strike: float,
    tte_years: float,
    volatility: float,
    rate: float = RISK_FREE_RATE_DEFAULT,
    option_type: str = "call"
) -> dict:
    """
    Compute Delta, Gamma, Theta (per day and per hour), and Vega.
    Handles near-zero TTE cleanly for 0DTE options.
    """
    # Clamp minimum TTE to avoid ZeroDivisionError: 1 minute = ~0.0000019 years
    tte_years = max(1e-5, tte_years)
    volatility = max(0.05, volatility)

    sqrt_t = math.sqrt(tte_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility ** 2) * tte_years) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t

    pdf_d1 = norm.pdf(d1)
    is_call = option_type.lower() == "call"

    # Delta
    if is_call:
        delta = float(norm.cdf(d1))
    else:
        delta = float(norm.cdf(d1) - 1.0)

    # Gamma (same for Call and Put)
    gamma = float(pdf_d1 / (spot * volatility * sqrt_t))

    # Theta (per year -> convert to per day and per hour)
    term1 = -(spot * pdf_d1 * volatility) / (2 * sqrt_t)
    if is_call:
        term2 = -rate * strike * math.exp(-rate * tte_years) * norm.cdf(d2)
    else:
        term2 = rate * strike * math.exp(-rate * tte_years) * norm.cdf(-d2)

    theta_annual = term1 + term2
    theta_per_day = theta_annual / 365.0  # dollar decay per day
    theta_per_hour = theta_per_day / 6.5  # dollar decay per trading hour (6.5 hours in US session)

    # Vega (per 1% vol move)
    vega = float(spot * sqrt_t * pdf_d1) / 100.0

    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 4),
        "theta_day": round(theta_per_day, 4),
        "theta_hour": round(theta_per_hour, 4),
        "vega": round(vega, 4),
        "d1": round(d1, 4),
        "d2": round(d2, 4),
    }


def implied_volatility_newton(
    market_price: float,
    spot: float,
    strike: float,
    tte_years: float,
    rate: float = RISK_FREE_RATE_DEFAULT,
    option_type: str = "call",
    max_iter: int = 40,
    tol: float = 1e-4
) -> float:
    """Solve for Implied Volatility using Newton-Raphson method."""
    intrinsic = max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
    if market_price <= intrinsic:
        return 0.15  # Fallback minimum baseline

    sigma = 0.25  # Initial guess
    tte_years = max(1e-5, tte_years)

    for _ in range(max_iter):
        sqrt_t = math.sqrt(tte_years)
        d1 = (math.log(spot / strike) + (rate + 0.5 * sigma ** 2) * tte_years) / (sigma * sqrt_t)
        price_diff = black_scholes_price(spot, strike, tte_years, sigma, rate, option_type) - market_price
        
        if abs(price_diff) < tol:
            return round(sigma, 4)

        vega = spot * sqrt_t * norm.pdf(d1)
        if vega < 1e-6:
            break
        sigma = sigma - price_diff / vega
        if sigma <= 0.01:
            sigma = 0.01
        elif sigma > 3.0:
            sigma = 3.0

    return round(sigma, 4)
