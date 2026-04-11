# ============================================================
#  tradebot / risk / manager.py
#  Live risk management layer.
#
#  Called by execution engine before every order.
#  If any check fails, the order is blocked.
#
#  Rules enforced:
#    1. Max risk per trade (% of capital)
#    2. Max daily loss (halt bot for the day)
#    3. Max open positions
#    4. Max trades per day
#    5. Minimum R:R ratio
#    6. Position size calculation (Kelly fraction / fixed fraction)
# ============================================================

import logging
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional

from config.settings import RISK, INSTRUMENTS, MARKET
from strategy.base_strategy import Signal, Direction

logger = logging.getLogger(__name__)

_CUR = "$" if MARKET == "US" else "₹"


@dataclass
class SizeResult:
    approved:    bool
    lots:        int
    reason:      str = ""

    def __bool__(self):
        return self.approved


class RiskManager:
    """
    Stateful risk manager.
    Must be shared across the entire trading session.
    Reset daily via reset_day().
    """

    def __init__(self, capital: float = None):
        self.capital       = capital or RISK["max_capital"]
        self.initial_cap   = self.capital
        self._daily_pnl:   float = 0.0
        self._trades_today: int  = 0
        self._open_count:   int  = 0
        self._today:        Optional[date] = None

    # ── Daily reset ───────────────────────────────────────────

    def reset_day(self) -> None:
        self._daily_pnl    = 0.0
        self._trades_today = 0
        self._today        = datetime.now().date()
        cur = _CUR
        logger.info(f"Risk manager reset for {self._today} | capital={cur}{self.capital:,.0f}")

    # ── Update state ──────────────────────────────────────────

    def record_fill(self, pnl_delta: float = 0.0) -> None:
        """Call after each trade fills."""
        self._trades_today += 1
        self._daily_pnl    += pnl_delta
        self.capital       += pnl_delta

    def record_open(self) -> None:
        self._open_count = max(0, self._open_count + 1)

    def record_close(self, pnl: float = 0.0) -> None:
        self._open_count = max(0, self._open_count - 1)
        self.record_fill(pnl)

    # ── Gate check ────────────────────────────────────────────

    def approve_signal(self, signal: Signal) -> SizeResult:
        """
        Full gate check on a signal.
        Returns SizeResult with approved=True and lot count, or False with reason.
        """
        today = datetime.now().date()
        if self._today != today:
            self.reset_day()

        # ── Hard stops ────────────────────────────────────────
        max_daily_loss = self.initial_cap * RISK["max_daily_loss_pct"] / 100
        if self._daily_pnl <= -max_daily_loss:
            return SizeResult(False, 0, f"Daily loss limit hit ({_CUR}{-self._daily_pnl:,.0f})")

        if self._trades_today >= RISK["max_trades_per_day"]:
            return SizeResult(False, 0, f"Max {RISK['max_trades_per_day']} trades/day reached")

        if self._open_count >= RISK["max_open_positions"]:
            return SizeResult(False, 0, f"Max {RISK['max_open_positions']} open positions")

        if signal.rr_ratio < RISK["min_rr_ratio"]:
            return SizeResult(False, 0, f"R:R {signal.rr_ratio:.2f} < minimum {RISK['min_rr_ratio']}")

        # ── Position sizing ───────────────────────────────────
        lots = self._calculate_lots(signal)
        if lots == 0:
            return SizeResult(False, 0, "Position size too small for current capital")

        return SizeResult(True, lots, f"Approved: {lots} lot(s)")

    def _calculate_lots(self, signal: Signal) -> int:
        """
        Fixed-fraction position sizing.
        Risk max_risk_per_trade_pct% of capital per trade.
        Lot size determined by stop-loss distance × lot_size.
        """
        inst     = INSTRUMENTS.get(signal.symbol, {})
        lot_size = inst.get("lot_size", 1)

        max_risk_amt  = self.capital * RISK["max_risk_per_trade_pct"] / 100
        risk_per_unit = abs(signal.entry_price - signal.stop_loss)

        if risk_per_unit <= 0:
            return 0

        risk_per_lot = risk_per_unit * lot_size
        lots = int(max_risk_amt / risk_per_lot)
        return max(1, min(lots, 5))   # cap at 5 lots

    # ── Status ────────────────────────────────────────────────

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def trades_today(self) -> int:
        return self._trades_today

    def status(self) -> dict:
        max_loss = self.initial_cap * RISK["max_daily_loss_pct"] / 100
        return {
            "capital":       round(self.capital, 2),
            "daily_pnl":     round(self._daily_pnl, 2),
            "daily_loss_pct": round(self._daily_pnl / self.initial_cap * 100, 2),
            "max_daily_loss": round(max_loss, 2),
            "trades_today":  self._trades_today,
            "open_positions": self._open_count,
            "halted":        self._daily_pnl <= -max_loss,
        }
