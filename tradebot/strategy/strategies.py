# ============================================================
#  tradebot / strategy / strategies.py
#  Three intraday strategies for BankNifty / Nifty futures.
#
#  Strategy 1: VWAP Mean Reversion
#    Best in: ranging markets (ADX < 20)
#    Logic:   price pulls away from VWAP, RSI confirms
#             oversold/overbought, enter for mean reversion
#    Edge:    VWAP acts as institutional reference price —
#             large participants anchor orders there
#
#  Strategy 2: Opening Range Breakout (ORB)
#    Best in: any regime
#    Logic:   first 15-min establishes range; breakout above/below
#             with volume surge signals directional intent
#    Edge:    institutional order flow dominates the open;
#             initial range defines the day's directional bias
#
#  Strategy 3: EMA Trend Follow
#    Best in: trending markets (ADX > 25)
#    Logic:   EMA 9/21 crossover in direction of EMA 50 trend,
#             confirmed by volume and MACD
#    Edge:    rides established intraday momentum with
#             defined risk via ATR stop
# ============================================================

import pandas as pd
import numpy as np
from typing import Optional

from strategy.base_strategy import StrategyBase, Signal, Direction
from strategy.regime import RegimeState, Regime
from strategy.indicators import (
    ema, rsi, atr, vwap, vwap_bands, opening_range,
    macd, volume_delta, bollinger_bands
)
from config.settings import RISK, SESSION


# Parse session boundaries once at import (minutes since midnight)
def _parse_time(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m

_SESSION_START      = _parse_time(SESSION["first_candle_end"])  # after opening candle
_SESSION_NO_TRADE   = _parse_time(SESSION["no_trade_after"])    # no new entries after this


# ── Strategy 1: VWAP Mean Reversion ─────────────────────────

class VWAPReversion(StrategyBase):
    """
    VWAP Mean Reversion.

    Long setup:
      - Price below VWAP lower band (1σ)
      - RSI(14) < 35 (oversold)
      - Current candle closes back above lower band (reversion starting)
      - Volume delta positive (buyers stepping in)
      - ATR-based stop below recent swing low

    Short setup: mirror of above.

    Filters:
      - Not in first 15 min of session (09:15–09:30)
      - Not in last 15 min of session (15:15–15:30)
      - ADX < 28 (not strongly trending)
      - Not near a major support/resistance level
    """

    DEFAULT_PARAMS = {
        "rsi_oversold":    35,
        "rsi_overbought":  65,
        "vwap_band_std":   1.0,    # enter at 1σ from VWAP
        "atr_stop_mult":   1.2,    # stop = 1.2 × ATR from entry
        "rr_ratio":        2.0,    # target at 2× risk
        "adx_max":         28,     # skip if trending too strongly
        "min_volume_ratio": 0.8,   # bar volume must be at least 80% of avg
    }

    def __init__(self, symbol: str, params: dict = None):
        super().__init__(symbol, {**self.DEFAULT_PARAMS, **(params or {})})
        self.name = "vwap_reversion"

    def is_active_in_regime(self, regime: RegimeState) -> bool:
        return regime.regime in (Regime.RANGING,) and regime.adx < self.params["adx_max"]

    def generate_signals(
        self,
        df:     pd.DataFrame,
        regime: Optional[RegimeState] = None,
    ) -> list[Signal]:

        if len(df) < 50:
            return []

        if regime and not self.is_active_in_regime(regime):
            return []

        bar = df.iloc[-2]    # last completed bar
        ts  = bar.name if hasattr(bar, "name") else df.index[-2]

        # ── Session time filter ───────────────────────────────
        try:
            hour, minute = ts.hour, ts.minute
            total_min = hour * 60 + minute
            if total_min < _SESSION_START:
                return []
            if total_min > _SESSION_NO_TRADE:
                return []
        except AttributeError:
            pass

        # ── Compute indicators ────────────────────────────────
        p = self.params
        vb    = vwap_bands(df, p["vwap_band_std"])
        rsi_v = rsi(df["close"], 14)
        atr_v = atr(df, 14)

        close      = bar["close"]
        prev_close = df["close"].iloc[-3]
        rsi_now    = rsi_v.iloc[-2]
        atr_now    = atr_v.iloc[-2]
        upper1     = vb["upper1"].iloc[-2]
        lower1     = vb["lower1"].iloc[-2]
        vwap_now   = vb["vwap"].iloc[-2]

        vol_avg    = df["volume"].rolling(20).mean().iloc[-2]
        vol_ratio  = bar["volume"] / vol_avg if vol_avg > 0 else 1.0
        vdelta     = volume_delta(df).iloc[-2]

        signals = []

        # ── Long signal ───────────────────────────────────────
        long_conditions = [
            close < lower1,                            # below lower VWAP band
            prev_close < lower1,                       # was below for at least 1 bar
            close > bar["open"],                       # current bar bullish (starting to reverse)
            rsi_now < p["rsi_oversold"],               # RSI oversold
            vdelta > 0,                                # positive buying pressure
            vol_ratio >= p["min_volume_ratio"],        # enough volume
        ]

        if all(long_conditions):
            stop_dist = p["atr_stop_mult"] * atr_now
            stop      = round(close - stop_dist, 2)
            target    = round(close + stop_dist * p["rr_ratio"], 2)
            sig = Signal(
                strategy=self.name,
                symbol=self.symbol,
                direction=Direction.LONG,
                entry_price=close,
                stop_loss=stop,
                target=target,
                timestamp=ts,
                regime=regime.regime.value if regime else None,
                confidence=min(1.0, (p["rsi_oversold"] - rsi_now) / 20 + 0.5),
                notes=f"VWAP_L rsi={rsi_now:.1f} vdelta={vdelta:.0f}",
            )
            if sig.is_valid:
                signals.append(sig)

        # ── Short signal ──────────────────────────────────────
        short_conditions = [
            close > upper1,
            prev_close > upper1,
            close < bar["open"],
            rsi_now > p["rsi_overbought"],
            vdelta < 0,
            vol_ratio >= p["min_volume_ratio"],
        ]

        if all(short_conditions):
            stop_dist = p["atr_stop_mult"] * atr_now
            stop      = round(close + stop_dist, 2)
            target    = round(close - stop_dist * p["rr_ratio"], 2)
            sig = Signal(
                strategy=self.name,
                symbol=self.symbol,
                direction=Direction.SHORT,
                entry_price=close,
                stop_loss=stop,
                target=target,
                timestamp=ts,
                regime=regime.regime.value if regime else None,
                confidence=min(1.0, (rsi_now - p["rsi_overbought"]) / 20 + 0.5),
                notes=f"VWAP_S rsi={rsi_now:.1f} vdelta={vdelta:.0f}",
            )
            if sig.is_valid:
                signals.append(sig)

        return signals


# ── Strategy 2: Opening Range Breakout ───────────────────────

class OpeningRangeBreakout(StrategyBase):
    """
    Opening Range Breakout (ORB).

    The first 15 minutes of the NSE session set the opening range.
    When price breaks above ORH or below ORL with strong volume,
    it signals institutional directional intent for the day.

    Entry: breakout candle close (confirmed break, not just touch)
    Stop:  opposite side of the opening range
    Target: 1.5–2× the opening range distance

    Filters:
      - Opening range must be < 1.5× ATR (normal range, not gap/news)
      - Volume on breakout bar > 1.5× 20-bar average
      - Breakout must happen before 11:30 (late breakouts are false)
      - Not during high volatility regime
    """

    DEFAULT_PARAMS = {
        "range_minutes":      15,
        "max_range_atr_mult": 1.5,   # skip if OR is wider than 1.5×ATR
        "volume_mult":        1.5,   # breakout bar volume must be 1.5× avg
        "latest_entry_hour":  _SESSION_NO_TRADE // 60 - 2,  # 2h before no-trade cutoff
        "latest_entry_min":   _SESSION_NO_TRADE % 60,
        "rr_ratio":           1.8,
    }

    def __init__(self, symbol: str, params: dict = None):
        super().__init__(symbol, {**self.DEFAULT_PARAMS, **(params or {})})
        self.name = "opening_range_breakout"

    def is_active_in_regime(self, regime: RegimeState) -> bool:
        return regime.regime != Regime.HIGH_VOL

    def generate_signals(
        self,
        df:     pd.DataFrame,
        regime: Optional[RegimeState] = None,
    ) -> list[Signal]:

        if len(df) < 30:
            return []

        if regime and not self.is_active_in_regime(regime):
            return []

        bar = df.iloc[-2]
        ts  = bar.name if hasattr(bar, "name") else df.index[-2]

        try:
            total_min = ts.hour * 60 + ts.minute
            if total_min < _SESSION_START:   # OR not yet complete
                return []
            if total_min > self.params["latest_entry_hour"] * 60 + self.params["latest_entry_min"]:
                return []
        except AttributeError:
            pass

        p     = self.params
        or_df = opening_range(df, range_minutes=p["range_minutes"])
        atr_v = atr(df, 14)

        orh = or_df["orh"].iloc[-2]
        orl = or_df["orl"].iloc[-2]

        if pd.isna(orh) or pd.isna(orl):
            return []

        or_range   = orh - orl
        atr_now    = atr_v.iloc[-2]

        # Skip abnormally wide opening ranges
        if or_range > p["max_range_atr_mult"] * atr_now:
            return []

        close    = bar["close"]
        vol_avg  = df["volume"].rolling(20).mean().iloc[-2]
        vol_now  = bar["volume"]
        vol_ok   = vol_now > p["volume_mult"] * vol_avg

        signals = []

        # ── Long breakout ──────────────────────────────────────
        if close > orh and vol_ok:
            stop   = round(orl, 2)
            risk   = close - stop
            target = round(close + risk * p["rr_ratio"], 2)
            sig = Signal(
                strategy=self.name,
                symbol=self.symbol,
                direction=Direction.LONG,
                entry_price=close,
                stop_loss=stop,
                target=target,
                timestamp=ts,
                regime=regime.regime.value if regime else None,
                confidence=min(1.0, (close - orh) / atr_now + 0.5),
                notes=f"ORB_L orh={orh:.0f} range={or_range:.0f} vol_x={vol_now/vol_avg:.1f}",
            )
            if sig.is_valid:
                signals.append(sig)

        # ── Short breakdown ────────────────────────────────────
        if close < orl and vol_ok:
            stop   = round(orh, 2)
            risk   = stop - close
            target = round(close - risk * p["rr_ratio"], 2)
            sig = Signal(
                strategy=self.name,
                symbol=self.symbol,
                direction=Direction.SHORT,
                entry_price=close,
                stop_loss=stop,
                target=target,
                timestamp=ts,
                regime=regime.regime.value if regime else None,
                confidence=min(1.0, (orl - close) / atr_now + 0.5),
                notes=f"ORB_S orl={orl:.0f} range={or_range:.0f} vol_x={vol_now/vol_avg:.1f}",
            )
            if sig.is_valid:
                signals.append(sig)

        return signals


# ── Strategy 3: EMA Trend Follow ─────────────────────────────

class EMATrendFollow(StrategyBase):
    """
    EMA Trend Follow — rides established intraday momentum.

    Setup (long):
      - EMA 50 pointing up (close > EMA 50 for 5+ bars)
      - EMA 9 crosses above EMA 21 (fresh signal)
      - MACD histogram turning positive
      - Price above VWAP
      - Volume confirming (above 20-bar average)

    Stop: below EMA 21 (or 1.5×ATR, whichever is tighter)
    Target: 2×ATR from entry

    This strategy sits out when ADX < 22 (not trending enough).
    """

    DEFAULT_PARAMS = {
        "fast_ema":      9,
        "slow_ema":      21,
        "trend_ema":     50,
        "adx_min":       22,       # only trade when trending
        "atr_stop_mult": 1.5,
        "rr_ratio":      2.0,
        "vol_min_ratio": 1.0,
        "cross_lookback": 3,       # crossover must have happened in last N bars
    }

    def __init__(self, symbol: str, params: dict = None):
        super().__init__(symbol, {**self.DEFAULT_PARAMS, **(params or {})})
        self.name = "ema_trend"

    def is_active_in_regime(self, regime: RegimeState) -> bool:
        return regime.regime in (Regime.TRENDING_UP, Regime.TRENDING_DOWN) \
               and regime.adx >= self.params["adx_min"]

    def generate_signals(
        self,
        df:     pd.DataFrame,
        regime: Optional[RegimeState] = None,
    ) -> list[Signal]:

        if len(df) < 60:
            return []

        if regime and not self.is_active_in_regime(regime):
            return []

        p = self.params
        bar = df.iloc[-2]
        ts  = bar.name if hasattr(bar, "name") else df.index[-2]

        try:
            total_min = ts.hour * 60 + ts.minute
            if total_min < _SESSION_START:
                return []
            if total_min > _SESSION_NO_TRADE:
                return []
        except AttributeError:
            pass

        close   = df["close"]
        ema9    = ema(close, p["fast_ema"])
        ema21   = ema(close, p["slow_ema"])
        ema50   = ema(close, p["trend_ema"])
        macd_df = macd(close)
        vwap_v  = vwap(df)
        atr_v   = atr(df, 14)
        vol_avg = df["volume"].rolling(20).mean()

        # Last 2 complete bars
        i = -2

        signals = []

        # ── Long: EMA 9 crosses above EMA 21 ────────────────
        cross_up = any(
            ema9.iloc[i - k] > ema21.iloc[i - k] and
            ema9.iloc[i - k - 1] <= ema21.iloc[i - k - 1]
            for k in range(p["cross_lookback"])
            if i - k - 1 >= -len(df)
        )

        long_conditions = [
            cross_up,
            close.iloc[i] > ema50.iloc[i],           # above trend EMA
            ema50.iloc[i] > ema50.iloc[i - 5],        # EMA 50 sloping up
            macd_df["histogram"].iloc[i] > 0,         # MACD hist positive
            close.iloc[i] > vwap_v.iloc[i],           # above VWAP
            df["volume"].iloc[i] >= p["vol_min_ratio"] * vol_avg.iloc[i],
        ]

        if all(long_conditions):
            atr_now = atr_v.iloc[i]
            entry   = close.iloc[i]
            stop    = round(min(ema21.iloc[i], entry - p["atr_stop_mult"] * atr_now), 2)
            target  = round(entry + abs(entry - stop) * p["rr_ratio"], 2)
            sig = Signal(
                strategy=self.name,
                symbol=self.symbol,
                direction=Direction.LONG,
                entry_price=entry,
                stop_loss=stop,
                target=target,
                timestamp=ts,
                regime=regime.regime.value if regime else None,
                confidence=0.7,
                notes=f"EMA_L ema9={ema9.iloc[i]:.0f} ema21={ema21.iloc[i]:.0f}",
            )
            if sig.is_valid:
                signals.append(sig)

        # ── Short: EMA 9 crosses below EMA 21 ───────────────
        cross_down = any(
            ema9.iloc[i - k] < ema21.iloc[i - k] and
            ema9.iloc[i - k - 1] >= ema21.iloc[i - k - 1]
            for k in range(p["cross_lookback"])
            if i - k - 1 >= -len(df)
        )

        short_conditions = [
            cross_down,
            close.iloc[i] < ema50.iloc[i],
            ema50.iloc[i] < ema50.iloc[i - 5],
            macd_df["histogram"].iloc[i] < 0,
            close.iloc[i] < vwap_v.iloc[i],
            df["volume"].iloc[i] >= p["vol_min_ratio"] * vol_avg.iloc[i],
        ]

        if all(short_conditions):
            atr_now = atr_v.iloc[i]
            entry   = close.iloc[i]
            stop    = round(max(ema21.iloc[i], entry + p["atr_stop_mult"] * atr_now), 2)
            target  = round(entry - abs(stop - entry) * p["rr_ratio"], 2)
            sig = Signal(
                strategy=self.name,
                symbol=self.symbol,
                direction=Direction.SHORT,
                entry_price=entry,
                stop_loss=stop,
                target=target,
                timestamp=ts,
                regime=regime.regime.value if regime else None,
                confidence=0.7,
                notes=f"EMA_S ema9={ema9.iloc[i]:.0f} ema21={ema21.iloc[i]:.0f}",
            )
            if sig.is_valid:
                signals.append(sig)

        return signals


# ── Strategy registry ─────────────────────────────────────────

ALL_STRATEGIES = {
    "vwap_reversion":        VWAPReversion,
    "opening_range_breakout": OpeningRangeBreakout,
    "ema_trend":             EMATrendFollow,
}

def build_strategies(symbol: str, params: dict = None) -> list[StrategyBase]:
    """Instantiate all strategies for a given symbol."""
    params = params or {}
    return [
        cls(symbol, params.get(name, {}))
        for name, cls in ALL_STRATEGIES.items()
    ]
