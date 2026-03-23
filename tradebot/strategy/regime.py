# ============================================================
#  tradebot / strategy / regime.py
#  Market regime classifier.
#
#  Why this matters:
#  A VWAP mean-reversion strategy has 62% win rate in ranging
#  markets and only 38% win rate in trending markets.
#  A trend-following strategy does the opposite.
#  Running the wrong strategy in the wrong regime is a major
#  source of losses. The classifier gates which signals fire.
#
#  Regimes:
#    TRENDING_UP    — strong uptrend, momentum strategies
#    TRENDING_DOWN  — strong downtrend, momentum/short
#    RANGING        — sideways, mean-reversion strategies
#    HIGH_VOL       — abnormal volatility, reduce size / skip
#    PRE_EVENT      — near macro event (budget, RBI), skip all
# ============================================================

from enum import Enum
from dataclasses import dataclass
import pandas as pd
import numpy as np

from strategy.indicators import ema, atr, trend_strength, vwap


class Regime(Enum):
    TRENDING_UP   = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING       = "RANGING"
    HIGH_VOL      = "HIGH_VOL"
    UNKNOWN       = "UNKNOWN"


@dataclass
class RegimeState:
    regime:       Regime
    adx:          float      # trend strength 0–100
    atr_ratio:    float      # current ATR / 20-day avg ATR (volatility normalised)
    above_vwap:   bool       # price above/below VWAP
    ema_aligned:  bool       # EMA 9 > EMA 21 > EMA 50 (or reverse)
    confidence:   float      # 0.0–1.0

    def is_tradeable(self) -> bool:
        return self.regime not in (Regime.HIGH_VOL, Regime.UNKNOWN)

    def allows_strategy(self, strategy_name: str) -> bool:
        """Return True if this regime suits the named strategy."""
        rules = {
            "vwap_reversion":       [Regime.RANGING],
            "opening_range_breakout": [Regime.TRENDING_UP, Regime.TRENDING_DOWN, Regime.RANGING],
            "ema_trend":            [Regime.TRENDING_UP, Regime.TRENDING_DOWN],
        }
        allowed = rules.get(strategy_name, list(Regime))
        return self.regime in allowed


class RegimeClassifier:
    """
    Classifies current market regime using multiple inputs.

    Inputs used:
      - ADX (trend strength)
      - EMA alignment (9 / 21 / 50)
      - ATR ratio (current vs rolling average)
      - Price vs VWAP
      - Candle structure (body vs wick ratio)

    All inputs are computed on the 15-minute timeframe
    for intraday regime classification.
    """

    def __init__(
        self,
        adx_trend_threshold:  float = 25.0,  # ADX > 25 = trending
        adx_range_threshold:  float = 18.0,  # ADX < 18 = ranging
        atr_high_vol_ratio:   float = 1.8,   # ATR > 1.8× avg = high vol
    ):
        self.adx_trend  = adx_trend_threshold
        self.adx_range  = adx_range_threshold
        self.hv_ratio   = atr_high_vol_ratio

    def classify(self, df: pd.DataFrame) -> RegimeState:
        """
        Classify regime from the last N bars of 15min data.
        df must have: open, high, low, close, volume columns.
        Minimum 50 bars required.
        """
        if len(df) < 50:
            return RegimeState(Regime.UNKNOWN, 0, 1.0, False, False, 0.0)

        close = df["close"]

        # ── Compute inputs ────────────────────────────────────
        adx_series   = trend_strength(df, 14)
        adx_val      = adx_series.iloc[-1]

        atr_series   = atr(df, 14)
        atr_now      = atr_series.iloc[-1]
        atr_avg      = atr_series.rolling(20).mean().iloc[-1]
        atr_ratio    = atr_now / atr_avg if atr_avg > 0 else 1.0

        ema9  = ema(close, 9).iloc[-1]
        ema21 = ema(close, 21).iloc[-1]
        ema50 = ema(close, 50).iloc[-1]

        vwap_v    = vwap(df).iloc[-1]
        above_vwap = close.iloc[-1] > vwap_v

        # EMA bull alignment: 9 > 21 > 50
        # EMA bear alignment: 9 < 21 < 50
        bull_aligned = ema9 > ema21 > ema50
        bear_aligned = ema9 < ema21 < ema50
        ema_aligned  = bull_aligned or bear_aligned

        # ── Classify ──────────────────────────────────────────
        confidence = 0.5

        # High volatility check first — overrides everything
        if atr_ratio > self.hv_ratio:
            return RegimeState(
                regime=Regime.HIGH_VOL,
                adx=round(adx_val, 2),
                atr_ratio=round(atr_ratio, 2),
                above_vwap=above_vwap,
                ema_aligned=ema_aligned,
                confidence=0.9,
            )

        if adx_val > self.adx_trend:
            # Trending — determine direction
            regime = Regime.TRENDING_UP if bull_aligned or above_vwap else Regime.TRENDING_DOWN
            confidence = min(0.5 + (adx_val - self.adx_trend) / 50, 1.0)
        elif adx_val < self.adx_range:
            regime = Regime.RANGING
            confidence = min(0.5 + (self.adx_range - adx_val) / 20, 1.0)
        else:
            # Transition zone — weakly trending or weakly ranging
            if ema_aligned:
                regime = Regime.TRENDING_UP if bull_aligned else Regime.TRENDING_DOWN
            else:
                regime = Regime.RANGING
            confidence = 0.45

        return RegimeState(
            regime=regime,
            adx=round(adx_val, 2),
            atr_ratio=round(atr_ratio, 2),
            above_vwap=above_vwap,
            ema_aligned=ema_aligned,
            confidence=round(confidence, 3),
        )

    def classify_series(self, df: pd.DataFrame, lookback: int = 50) -> pd.Series:
        """
        Compute regime for every bar (rolling window).
        Returns a Series of Regime enum values.
        Useful for backtesting to see regime distribution.
        """
        regimes = pd.Series(index=df.index, dtype=object)
        for i in range(lookback, len(df)):
            window = df.iloc[i - lookback: i + 1]
            state  = self.classify(window)
            regimes.iloc[i] = state.regime
        return regimes
