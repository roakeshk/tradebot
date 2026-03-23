# ============================================================
#  tradebot / options / pricing.py
#  T2 — Options pricing engine
#
#  Implements:
#    - Black-Scholes price for European options (CE/PE)
#    - Full Greeks: delta, gamma, theta, vega, rho
#    - Implied Volatility solver (Newton-Raphson)
#    - Binomial model (American-style approximation)
#    - P&L simulation at expiry and before expiry
# ============================================================

import math
from dataclasses import dataclass
from typing import Literal


def _norm_cdf(x: float) -> float:
    """Standard normal CDF using error function."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


@dataclass
class Greeks:
    delta: float   # rate of change of option price w.r.t. underlying
    gamma: float   # rate of change of delta
    theta: float   # time decay per day (negative for long options)
    vega:  float   # sensitivity to 1% change in IV
    rho:   float   # sensitivity to 1% change in interest rate

    def __str__(self):
        return (
            f"Delta={self.delta:+.4f}  Gamma={self.gamma:.6f}  "
            f"Theta={self.theta:+.2f}/day  Vega={self.vega:.2f}/1%IV  "
            f"Rho={self.rho:+.4f}"
        )


class BSModel:
    """
    Black-Scholes pricing model for NSE index options.

    Parameters:
        S:    spot price of underlying
        K:    strike price
        T:    time to expiry in years (use ExpiryManager.time_to_expiry())
        r:    risk-free rate (RBI repo rate ~6.5% → use 0.065)
        sigma: implied volatility (annualised, e.g. 0.18 = 18%)
        option_type: "ce" (call) or "pe" (put)

    NSE context:
        Options are European style (settled at expiry, no early exercise).
        Black-Scholes is accurate for index options on NSE.
        Always use continuous compounding — NSE uses daily settlement.
    """

    def _d1_d2(
        self,
        S: float, K: float, T: float,
        r: float, sigma: float
    ) -> tuple[float, float]:
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return 0.0, 0.0
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return d1, d2

    def price(
        self,
        S: float, K: float, T: float,
        r: float, sigma: float,
        option_type: str = "ce",
    ) -> float:
        """Black-Scholes option price."""
        if T <= 0:
            # Intrinsic value at expiry
            if option_type == "ce":
                return max(0.0, S - K)
            else:
                return max(0.0, K - S)

        d1, d2 = self._d1_d2(S, K, T, r, sigma)

        if option_type == "ce":
            return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
        else:
            return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)

    def greeks(
        self,
        S: float, K: float, T: float,
        r: float, sigma: float,
        option_type: str = "ce",
    ) -> dict:
        """Calculate all five Greeks."""
        if T <= 0 or sigma <= 0:
            return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

        d1, d2 = self._d1_d2(S, K, T, r, sigma)
        disc   = math.exp(-r * T)
        sqrt_T = math.sqrt(T)

        # Delta
        if option_type == "ce":
            delta = _norm_cdf(d1)
        else:
            delta = _norm_cdf(d1) - 1.0

        # Gamma (same for CE and PE)
        gamma = _norm_pdf(d1) / (S * sigma * sqrt_T)

        # Theta (per day — divide annual theta by 365)
        theta_annual = (
            -(S * _norm_pdf(d1) * sigma) / (2 * sqrt_T)
            - r * K * disc * _norm_cdf(d2 if option_type == "ce" else -d2)
            * (1 if option_type == "ce" else -1)
        )
        theta = theta_annual / 365  # per calendar day

        # Vega (per 1% change in IV → divide by 100)
        vega = S * sqrt_T * _norm_pdf(d1) / 100

        # Rho (per 1% change in r → divide by 100)
        if option_type == "ce":
            rho = K * T * disc * _norm_cdf(d2) / 100
        else:
            rho = -K * T * disc * _norm_cdf(-d2) / 100

        return {
            "delta": round(delta, 6),
            "gamma": round(gamma, 8),
            "theta": round(theta, 4),
            "vega":  round(vega, 4),
            "rho":   round(rho, 6),
        }

    def implied_volatility(
        self,
        market_price: float,
        S: float, K: float, T: float,
        r: float,
        option_type: str = "ce",
        max_iter: int = 100,
        tolerance: float = 1e-6,
    ) -> float:
        """
        Newton-Raphson IV solver.
        Returns annualised IV (e.g. 0.18 = 18%).
        Returns 0.0 if no solution found (deep ITM/OTM with no time value).
        """
        if T <= 0 or market_price <= 0:
            return 0.0

        # Intrinsic value check
        intrinsic = max(0.0, S - K) if option_type == "ce" else max(0.0, K - S)
        if market_price < intrinsic:
            return 0.0

        sigma = 0.3   # initial guess: 30%

        for _ in range(max_iter):
            price = self.price(S, K, T, r, sigma, option_type)
            diff  = price - market_price
            if abs(diff) < tolerance:
                break
            vega = self.greeks(S, K, T, r, sigma, option_type)["vega"] * 100
            if abs(vega) < 1e-10:
                break
            sigma -= diff / vega
            sigma  = max(0.001, min(sigma, 5.0))   # clamp to [0.1%, 500%]

        return round(max(0.0, sigma), 6)

    def pnl_at_expiry(
        self,
        legs: list[dict],
        spot_range: list[float],
    ) -> list[float]:
        """
        Calculate total strategy P&L at expiry for a range of spot prices.

        legs format:
            [{"type": "ce"/"pe", "strike": K, "premium": p,
              "qty": n, "action": "buy"/"sell"}, ...]

        Returns list of P&L values corresponding to spot_range.
        """
        pnls = []
        for spot in spot_range:
            total = 0.0
            for leg in legs:
                intrinsic = max(0.0, spot - leg["strike"]) if leg["type"] == "ce" \
                            else max(0.0, leg["strike"] - spot)
                sign = 1 if leg["action"] == "buy" else -1
                total += sign * (intrinsic - leg["premium"]) * leg["qty"]
            pnls.append(round(total, 2))
        return pnls

    def pnl_now(
        self,
        legs: list[dict],
        S: float, T: float,
        r: float = 0.065,
        sigma: float = 0.18,
    ) -> float:
        """Current mark-to-market P&L of a multi-leg position."""
        total = 0.0
        for leg in legs:
            current_price = self.price(S, leg["strike"], T, r, sigma, leg["type"])
            sign          = 1 if leg["action"] == "buy" else -1
            total        += sign * (current_price - leg["premium"]) * leg["qty"]
        return round(total, 2)

    def max_profit_loss(
        self,
        legs: list[dict],
        spot: float,
        wide: float = 5000,
    ) -> dict:
        """
        Estimate max profit and max loss for a strategy.
        Scans a wide range of spot prices at expiry.
        """
        spot_range  = [spot - wide + i * 50 for i in range(int(wide * 2 / 50) + 1)]
        pnls        = self.pnl_at_expiry(legs, spot_range)
        max_profit  = max(pnls)
        max_loss    = min(pnls)
        breakevens  = []
        for i in range(1, len(pnls)):
            if (pnls[i - 1] < 0) != (pnls[i] < 0):
                be = spot_range[i - 1] + 50 * abs(pnls[i - 1]) / (abs(pnls[i - 1]) + abs(pnls[i]))
                breakevens.append(round(be))
        return {
            "max_profit":  round(max_profit, 2),
            "max_loss":    round(max_loss, 2),
            "breakevens":  breakevens,
            "risk_reward": round(abs(max_profit / max_loss), 3) if max_loss != 0 else 0,
        }
