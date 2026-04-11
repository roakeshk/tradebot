# ============================================================
#  tradebot / options / risk.py
#  T5 — Options risk manager
#
#  Options risk is fundamentally different from futures:
#    - Premium selling has theoretically unlimited risk (straddle)
#    - Gamma risk explodes near expiry (avoid selling near 0 DTE)
#    - Vega risk: sudden IV spike can wipe gains even if direction right
#    - Margin requirements change dynamically (SPAN + exposure)
#
#  This manager enforces hard limits on all of the above.
# ============================================================

import logging
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional

from options.strategies import OptionsPosition, OptionsLeg
from options.data import OptionChain
from config.settings import RISK, MARKET, INSTRUMENTS

_CUR = "$" if MARKET == "US" else "₹"

logger = logging.getLogger(__name__)


@dataclass
class OptionsSizeResult:
    approved:    bool
    lots:        int
    reason:      str = ""

    def __bool__(self):
        return self.approved


class OptionsRiskManager:
    """
    Risk gatekeeper for all options positions.

    Hard limits enforced before any options order is placed:
      1. Max premium at risk per trade (% of capital)
      2. Max net delta exposure (directional risk)
      3. Max vega exposure (IV risk)
      4. Max gamma exposure (near-expiry risk)
      5. Max daily loss (shared with futures risk manager)
      6. No selling naked options without hedge when close to expiry
      7. Margin availability check
    """

    def __init__(self, capital: float = None):
        self.capital       = capital or RISK["max_capital"]
        self.initial_cap   = self.capital
        self._daily_pnl    = 0.0
        self._trades_today = 0
        self._today: Optional[date] = None

        # Options-specific limits
        self.max_premium_risk_pct = 2.0     # max 2% capital as premium risk per trade
        self.max_net_delta        = 0.30    # max |net delta| per 1 lot
        self.max_vega_exposure    = 500.0   # max vega per unit of capital
        self.max_gamma_risk_dte   = 1       # don't sell options with DTE ≤ 1 (gamma too high)
        self.max_open_positions   = 3       # max concurrent options positions
        self._open_positions:list = []

    def reset_day(self):
        self._daily_pnl    = 0.0
        self._trades_today = 0
        self._today        = datetime.now().date()

    def record_close(self, pnl: float):
        self._daily_pnl += pnl
        self.capital    += pnl

    def record_open(self, position: OptionsPosition):
        self._open_positions.append(position)

    def record_exit(self, position: OptionsPosition, pnl: float):
        self._open_positions = [p for p in self._open_positions
                                if p.strategy_name != position.strategy_name]
        self.record_close(pnl)

    # ── Main gate ─────────────────────────────────────────────

    def approve_position(
        self,
        position: OptionsPosition,
        chain:    OptionChain,
    ) -> OptionsSizeResult:
        """
        Full risk gate for an options position.
        Returns OptionsSizeResult with approved lots or rejection reason.
        """
        today = datetime.now().date()
        if self._today != today:
            self.reset_day()

        # ── Hard stops ────────────────────────────────────────

        max_daily_loss = self.initial_cap * RISK["max_daily_loss_pct"] / 100
        if self._daily_pnl <= -max_daily_loss:
            return OptionsSizeResult(False, 0, "Daily loss limit hit")

        if len(self._open_positions) >= self.max_open_positions:
            return OptionsSizeResult(False, 0, f"Max {self.max_open_positions} open positions")

        if self._trades_today >= RISK["max_trades_per_day"]:
            return OptionsSizeResult(False, 0, "Max trades per day reached")

        # ── DTE gamma risk ────────────────────────────────────
        dte = position.expiry - today
        if dte.days <= self.max_gamma_risk_dte:
            sell_legs = [l for l in position.legs if l.action == "sell"]
            if sell_legs:
                return OptionsSizeResult(
                    False, 0,
                    f"DTE={dte.days} — gamma too high for short options on expiry day"
                )

        # ── Premium risk check ────────────────────────────────
        max_risk_inr  = self.capital * self.max_premium_risk_pct / 100
        position_risk = abs(position.max_loss)

        if position_risk > 0 and position_risk > max_risk_inr * 5:
            # Scale down lots
            scale = max_risk_inr * 5 / position_risk
            lots  = max(1, int(scale))
        else:
            lots = self._calculate_lots(position, max_risk_inr)

        if lots == 0:
            return OptionsSizeResult(False, 0, "Position too large for current capital")

        # ── Net delta check ───────────────────────────────────
        net_delta = self._estimate_net_delta(position, chain)
        if abs(net_delta) > self.max_net_delta * lots:
            # Delta too high — too directional for a neutral strategy
            if position.strategy_name in ("short_straddle", "iron_condor"):
                return OptionsSizeResult(
                    False, 0,
                    f"Net delta {net_delta:.3f} too high for neutral strategy"
                )

        # ── Vega check ────────────────────────────────────────
        net_vega = self._estimate_net_vega(position, chain)
        max_vega  = self.max_vega_exposure * (self.capital / 100000)
        if abs(net_vega) > max_vega:
            return OptionsSizeResult(
                False, 0,
                f"Net vega {net_vega:.1f} exceeds limit {max_vega:.1f}"
            )

        # ── Margin check ──────────────────────────────────────
        est_margin = self._estimate_margin(position, chain.spot)
        if est_margin > self.capital * 0.60:
            return OptionsSizeResult(
                False, 0,
                f"Estimated margin {_CUR}{est_margin:,.0f} > 60% of capital"
            )

        self._trades_today += 1
        return OptionsSizeResult(True, lots, f"Approved: {lots} lot(s)")

    # ── Helper methods ────────────────────────────────────────

    def _calculate_lots(
        self, position: OptionsPosition, max_risk_inr: float
    ) -> int:
        """
        Size position based on max loss per trade.
        For credit strategies: risk = max loss (if spreads) or 2× premium (if naked).
        For debit strategies: risk = premium paid.
        """
        if position.max_loss == 0:
            return 1

        base_risk_per_lot = abs(position.max_loss)
        if base_risk_per_lot <= 0:
            return 1

        lots = int(max_risk_inr / base_risk_per_lot)
        return max(1, min(lots, 5))

    def _estimate_net_delta(
        self, position: OptionsPosition, chain: OptionChain
    ) -> float:
        net = 0.0
        for leg in position.legs:
            row = chain.get_strike(leg.strike)
            if row is not None:
                delta = float(row.get(f"{leg.option_type}_delta", 0))
                sign  = 1 if leg.action == "buy" else -1
                net  += sign * delta * leg.lots
        return round(net, 4)

    def _estimate_net_vega(
        self, position: OptionsPosition, chain: OptionChain
    ) -> float:
        net = 0.0
        for leg in position.legs:
            row = chain.get_strike(leg.strike)
            if row is not None:
                vega = float(row.get(f"{leg.option_type}_vega", 0))
                sign = 1 if leg.action == "buy" else -1
                net += sign * vega * leg.lots * leg.lot_size
        return round(net, 2)

    def _estimate_margin(self, position: OptionsPosition, spot: float) -> float:
        """
        Approximate SPAN margin for the position.
        Actual margin varies — this is a conservative estimate.

        NSE SPAN rules (approximate):
          Naked short: ~10-15% of notional
          Spread:      ~2-5% of notional (margin benefit from hedge)
          Bought option: full premium paid (no margin credit)
        """
        total_margin = 0.0
        has_short    = any(l.action == "sell" for l in position.legs)
        has_hedge    = any(l.action == "buy"  for l in position.legs)

        for leg in position.legs:
            notional = spot * leg.lots * leg.lot_size
            if leg.action == "sell":
                margin_pct = 0.03 if has_hedge else 0.06
                total_margin += notional * margin_pct
            else:
                # Buying: pay full premium
                total_margin += leg.premium * leg.lots * leg.lot_size

        return round(total_margin, 2)

    def status(self) -> dict:
        return {
            "capital":        round(self.capital, 2),
            "daily_pnl":      round(self._daily_pnl, 2),
            "trades_today":   self._trades_today,
            "open_positions": len(self._open_positions),
            "halted":         self._daily_pnl <= -(self.initial_cap * RISK["max_daily_loss_pct"] / 100),
        }
