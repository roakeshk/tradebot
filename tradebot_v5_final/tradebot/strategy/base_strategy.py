# ============================================================
#  tradebot / strategy / base_strategy.py
#  Abstract base class for all strategies.
#
#  Contract:
#    - generate_signals(df) returns list of Signal objects
#    - Each Signal has entry, stop-loss, target, direction
#    - Signal R:R is checked against min_rr before being returned
#    - Strategy never touches orders or positions — that's execution's job
# ============================================================

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum

import pandas as pd

from config.settings import RISK
from strategy.regime import RegimeState


class Direction(Enum):
    LONG  = "LONG"
    SHORT = "SHORT"


@dataclass
class Signal:
    """A trade signal produced by a strategy."""
    strategy:     str           # which strategy produced this
    symbol:       str
    direction:    Direction
    entry_price:  float
    stop_loss:    float         # hard stop-loss price
    target:       float         # profit target price
    timestamp:    datetime = field(default_factory=datetime.now)
    regime:       Optional[str] = None
    confidence:   float = 1.0   # 0.0–1.0, used for position sizing
    notes:        str = ""

    @property
    def risk_points(self) -> float:
        """Distance from entry to stop in index points."""
        return abs(self.entry_price - self.stop_loss)

    @property
    def reward_points(self) -> float:
        """Distance from entry to target in index points."""
        return abs(self.target - self.entry_price)

    @property
    def rr_ratio(self) -> float:
        """Reward-to-risk ratio. Must be >= RISK['min_rr_ratio']."""
        if self.risk_points == 0:
            return 0.0
        return round(self.reward_points / self.risk_points, 2)

    @property
    def is_valid(self) -> bool:
        """Basic sanity checks on the signal."""
        if self.risk_points == 0 or self.reward_points == 0:
            return False
        if self.rr_ratio < RISK["min_rr_ratio"]:
            return False
        # Directional consistency
        if self.direction == Direction.LONG:
            return self.stop_loss < self.entry_price < self.target
        else:
            return self.target < self.entry_price < self.stop_loss

    def __str__(self) -> str:
        return (
            f"[{self.strategy}] {self.direction.value} {self.symbol} "
            f"@ {self.entry_price:.2f} | SL={self.stop_loss:.2f} "
            f"T={self.target:.2f} | RR={self.rr_ratio:.2f} | "
            f"conf={self.confidence:.2f}"
        )


class StrategyBase(ABC):
    """
    All strategies inherit from this.
    Subclass only needs to implement generate_signals().
    """

    def __init__(self, symbol: str, params: dict = None):
        self.symbol = symbol
        self.params = params or {}
        self.name   = self.__class__.__name__

    @abstractmethod
    def generate_signals(
        self,
        df:     pd.DataFrame,
        regime: Optional[RegimeState] = None,
    ) -> list[Signal]:
        """
        Given OHLCV data with indicators, return valid signals.
        df is the full historical window ending at 'now'.
        Only the last completed bar (iloc[-2]) should be used for
        signal generation — iloc[-1] is the live/incomplete bar.
        """
        ...

    def is_active_in_regime(self, regime: RegimeState) -> bool:
        """Override to restrict strategy to certain regimes."""
        return True

    def _last_complete_bar(self, df: pd.DataFrame) -> pd.Series:
        """Return the last completed (closed) bar. Never use iloc[-1] for signals."""
        return df.iloc[-2]

    def _atr_stop(
        self,
        df:         pd.DataFrame,
        direction:  Direction,
        multiplier: float = 1.5,
    ) -> tuple[float, float]:
        """
        Compute ATR-based stop-loss and target from the last bar.
        Returns (stop_loss_price, target_price).
        """
        from strategy.indicators import atr as calc_atr
        atr_val   = calc_atr(df, 14).iloc[-2]
        bar       = self._last_complete_bar(df)
        entry     = bar["close"]
        stop_dist = multiplier * atr_val
        tgt_dist  = stop_dist * RISK["min_rr_ratio"]

        if direction == Direction.LONG:
            stop   = entry - stop_dist
            target = entry + tgt_dist
        else:
            stop   = entry + stop_dist
            target = entry - tgt_dist

        return round(stop, 2), round(target, 2)
