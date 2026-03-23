# ============================================================
#  tradebot / options / signals.py
#  T4 — Signal engine for options
#
#  Generates entry signals using options-specific indicators:
#    - IV Rank / IV Percentile
#    - Put-Call Ratio (PCR) by OI and volume
#    - OI buildup / unwinding analysis
#    - Max Pain calculation
#    - Theta decay acceleration zones
#    - Skew analysis (put skew vs call skew)
#
#  These signals gate which strategies fire and when.
#  They work alongside the existing regime classifier.
# ============================================================

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

from options.data import OptionChain, OptionsDataPipeline, _get_iv_rank
from options.strategies import OptionsPosition, OptionsStrategyBuilder
from strategy.regime import RegimeClassifier, RegimeState, Regime

logger = logging.getLogger(__name__)


@dataclass
class OptionsSignal:
    """A complete options trade signal."""
    strategy_name:   str
    symbol:          str
    position:        OptionsPosition
    iv_rank:         float
    pcr:             float
    max_pain:        float
    regime:          str
    confidence:      float        # 0.0–1.0
    timestamp:       datetime
    notes:           str = ""

    @property
    def is_valid(self) -> bool:
        return (
            self.position is not None
            and len(self.position.legs) > 0
            and self.confidence >= 0.45
        )

    def __str__(self) -> str:
        legs_str = " + ".join(
            f"{'SELL' if l.action=='sell' else 'BUY'} {l.strike}{l.option_type.upper()}"
            for l in self.position.legs
        )
        return (
            f"[{self.strategy_name.upper()}] {self.symbol} | {legs_str} | "
            f"IV rank={self.iv_rank:.0f} PCR={self.pcr:.2f} "
            f"MaxPain={self.max_pain:.0f} conf={self.confidence:.2f}"
        )


class OptionsSignalEngine:
    """
    Generates options trade signals by combining:
      - Market regime (from existing RegimeClassifier)
      - IV conditions (rank, term structure, skew)
      - Options market microstructure (PCR, OI, max pain)
      - Theta timing (days to expiry, time of day)
    """

    def __init__(self):
        self.builder = OptionsStrategyBuilder()
        self.regime  = RegimeClassifier()

    def generate(
        self,
        chain:       OptionChain,
        price_df:    pd.DataFrame,   # underlying 5min OHLCV for regime
        lots:        int = 1,
    ) -> Optional[OptionsSignal]:
        """
        Main entry point. Returns one signal or None.
        Called once per candle close during trading session.
        """

        # ── 1. Calculate all market conditions ───────────────
        iv_rank  = chain.iv_rank or 50.0
        pcr      = self._calc_pcr(chain)
        max_pain = self._calc_max_pain(chain)
        skew     = self._calc_skew(chain)
        dte      = chain.days_to_expiry

        # ── 2. Classify regime ────────────────────────────────
        regime_state = None
        try:
            if len(price_df) >= 50:
                regime_state = self.regime.classify(price_df.tail(60))
        except Exception:
            pass

        regime_str = regime_state.regime.value if regime_state else "UNKNOWN"

        # ── 3. Session time filter ────────────────────────────
        now = datetime.now()
        hour_min = now.hour * 60 + now.minute
        if hour_min < 9 * 60 + 30 or hour_min > 15 * 60 + 10:
            logger.debug("Outside trading hours")
            return None

        # ── 4. Calculate signal confidence ───────────────────
        confidence, notes = self._score_conditions(
            iv_rank, pcr, max_pain, skew, dte, regime_state, chain
        )

        if confidence < 0.40:
            logger.debug(f"Low confidence {confidence:.2f} — no signal")
            return None

        # ── 5. Select strategy ────────────────────────────────
        position = self.builder.select_best_strategy(chain, regime_str, lots)

        if position is None:
            logger.debug("No suitable strategy for current conditions")
            return None

        signal = OptionsSignal(
            strategy_name=position.strategy_name,
            symbol=chain.symbol,
            position=position,
            iv_rank=iv_rank,
            pcr=pcr,
            max_pain=max_pain,
            regime=regime_str,
            confidence=confidence,
            timestamp=now,
            notes=notes,
        )

        logger.info(f"Options signal: {signal}")
        return signal

    # ── PCR analysis ──────────────────────────────────────────

    def _calc_pcr(self, chain: OptionChain) -> float:
        """
        PCR by OI (Put-Call Ratio).
        PCR > 1.2  → bearish sentiment → market may bounce → good for short put
        PCR < 0.8  → bullish sentiment → market may fall  → good for short call
        PCR 0.9–1.1 → neutral → good for straddle/condor
        """
        return chain.pcr

    def _calc_max_pain(self, chain: OptionChain) -> float:
        """
        Max Pain = strike at which option sellers (writers) lose the least.
        Market tends to gravitate towards max pain at expiry.
        Strong edge: when spot is far from max pain, it often moves towards it.
        """
        if chain.df.empty:
            return chain.spot

        total_pain = {}
        strikes    = chain.df["strike"].tolist()

        for test_strike in strikes:
            pain = 0.0
            for _, row in chain.df.iterrows():
                k       = row["strike"]
                ce_oi   = row.get("ce_oi", 0)
                pe_oi   = row.get("pe_oi", 0)
                # Pain for call writers: sum of (test_strike - k) * OI for k < test_strike
                if test_strike > k:
                    pain += (test_strike - k) * ce_oi
                # Pain for put writers
                if test_strike < k:
                    pain += (k - test_strike) * pe_oi
            total_pain[test_strike] = pain

        return min(total_pain, key=total_pain.get)

    def _calc_skew(self, chain: OptionChain) -> float:
        """
        IV skew = avg OTM Put IV - avg OTM Call IV.
        Positive skew (put > call) is normal (fear premium).
        Extreme skew (>5%) → market is pricing heavy downside.
        Negative skew → complacency → dangerous for put sellers.
        """
        if chain.df.empty:
            return 0.0

        atm       = chain.atm
        step      = 100 if chain.symbol == "BANKNIFTY" else 50
        otm_calls = chain.df[chain.df["strike"] > atm + step * 2]["ce_iv"]
        otm_puts  = chain.df[chain.df["strike"] < atm - step * 2]["pe_iv"]

        if otm_calls.empty or otm_puts.empty:
            return 0.0

        return round(float(otm_puts.mean()) - float(otm_calls.mean()), 2)

    # ── Confidence scoring ────────────────────────────────────

    def _score_conditions(
        self,
        iv_rank:      float,
        pcr:          float,
        max_pain:     float,
        skew:         float,
        dte:          int,
        regime:       Optional[RegimeState],
        chain:        OptionChain,
    ) -> tuple[float, str]:
        """
        Score current conditions 0–1 for premium-selling strategies.
        Returns (score, description_of_conditions).
        """
        score  = 0.0
        notes  = []

        # IV rank (most important)
        if iv_rank >= 70:
            score += 0.30; notes.append(f"IV rank {iv_rank:.0f} (elevated)")
        elif iv_rank >= 55:
            score += 0.20; notes.append(f"IV rank {iv_rank:.0f} (moderate)")
        elif iv_rank >= 40:
            score += 0.10; notes.append(f"IV rank {iv_rank:.0f} (below avg)")
        else:
            score += 0.00; notes.append(f"IV rank {iv_rank:.0f} (low - avoid selling)")

        # DTE timing
        if 3 <= dte <= 7:
            score += 0.25; notes.append(f"DTE {dte} (ideal theta decay)")
        elif 1 <= dte <= 2:
            score += 0.15; notes.append(f"DTE {dte} (expiry week, high gamma risk)")
        elif 8 <= dte <= 15:
            score += 0.15; notes.append(f"DTE {dte} (acceptable)")
        else:
            score += 0.05

        # PCR
        if 0.85 <= pcr <= 1.20:
            score += 0.15; notes.append(f"PCR {pcr:.2f} (neutral)")
        elif pcr > 1.20:
            score += 0.10; notes.append(f"PCR {pcr:.2f} (bearish sentiment)")
        else:
            score += 0.10; notes.append(f"PCR {pcr:.2f} (bullish sentiment)")

        # Max pain proximity
        spot      = chain.spot
        mp_gap_pct = abs(spot - max_pain) / spot * 100
        if mp_gap_pct < 0.5:
            score += 0.15; notes.append(f"Near max pain {max_pain:.0f}")
        elif mp_gap_pct < 1.5:
            score += 0.10; notes.append(f"Max pain {max_pain:.0f} ({mp_gap_pct:.1f}% away)")
        else:
            score += 0.05; notes.append(f"Max pain {max_pain:.0f} ({mp_gap_pct:.1f}% away)")

        # Regime (options-specific interpretation)
        if regime:
            if regime.regime == Regime.HIGH_VOL:
                score -= 0.20; notes.append("HIGH VOL regime — avoid selling")
            elif regime.regime == Regime.RANGING:
                score += 0.15; notes.append("RANGING — ideal for premium selling")
            elif regime.regime in (Regime.TRENDING_UP, Regime.TRENDING_DOWN):
                score += 0.05; notes.append("TRENDING — use directional spreads")

        # Skew filter for put selling
        if skew > 8:
            score -= 0.10; notes.append(f"High put skew {skew:.1f}% — put premium elevated (caution)")

        return round(min(1.0, max(0.0, score)), 3), " | ".join(notes)

    # ── OI analysis ───────────────────────────────────────────

    def get_oi_analysis(self, chain: OptionChain) -> dict:
        """
        OI buildup/unwinding analysis.
        High CE OI at a strike = resistance.
        High PE OI at a strike = support.
        These levels often act as magnets for expiry.
        """
        if chain.df.empty:
            return {}

        top_ce_oi = chain.df.nlargest(3, "ce_oi")[["strike", "ce_oi"]]
        top_pe_oi = chain.df.nlargest(3, "pe_oi")[["strike", "pe_oi"]]

        return {
            "resistance_levels": top_ce_oi["strike"].tolist(),
            "support_levels":    top_pe_oi["strike"].tolist(),
            "max_ce_oi_strike":  float(chain.df.loc[chain.df["ce_oi"].idxmax(), "strike"]),
            "max_pe_oi_strike":  float(chain.df.loc[chain.df["pe_oi"].idxmax(), "strike"]),
            "pcr":               chain.pcr,
            "total_ce_oi":       int(chain.df["ce_oi"].sum()),
            "total_pe_oi":       int(chain.df["pe_oi"].sum()),
        }
