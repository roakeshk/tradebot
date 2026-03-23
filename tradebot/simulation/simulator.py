# ============================================================
#  tradebot / simulation / simulator.py
#  B1 — Realtime market simulator
#
#  Replays historical OHLCV data as if it were happening live.
#  Fires callbacks on each bar exactly like the live engine does.
#  No broker account needed — runs 100% from cached data.
#
#  What it simulates:
#    - Each historical 5-min bar becomes a "live" candle
#    - Option chains rebuilt via Black-Scholes on each bar
#    - All strategies, risk manager, and execution run normally
#    - Slippage and costs applied identically to live trading
#    - Speed control: 1× (real time), 100×, 10000× (fast backtest)
#
#  Modes:
#    PAPER_SIM:   run strategies, paper-fill orders, track P&L
#    BACKTEST:    run as fast as possible, collect full results
#    REPLAY:      1× speed, visual output for analysis
# ============================================================

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Callable
import pandas as pd
import numpy as np

from data.pipeline    import DataPipeline
from options.chain_feed import ChainFeed
from options.pricing  import BSModel

logger = logging.getLogger(__name__)


class SimMode(Enum):
    PAPER_SIM = "paper_sim"   # paper trade at realistic speed
    BACKTEST  = "backtest"    # maximum speed, full results
    REPLAY    = "replay"      # 1× speed, visual output


@dataclass
class SimBar:
    """One bar of simulation state — passed to all callbacks."""
    timestamp:   datetime
    symbol:      str
    open:        float
    high:        float
    low:         float
    close:       float
    volume:      int
    bar_index:   int
    total_bars:  int
    session_pct: float     # 0.0 → 1.0 through the trading session
    iv_estimate: float     # estimated IV for this bar


@dataclass
class SimResult:
    """Aggregated results after simulation completes."""
    symbol:        str
    mode:          str
    from_date:     datetime
    to_date:       datetime
    total_bars:    int = 0
    # Filled by strategies during sim
    futures_trades: list = field(default_factory=list)
    options_trades: list = field(default_factory=list)

    def summary(self) -> dict:
        all_trades = self.futures_trades + self.options_trades
        if not all_trades:
            return {"total_trades": 0, "net_pnl": 0}
        pnls = [t.get("net_pnl", 0) for t in all_trades if isinstance(t, dict)]
        return {
            "total_trades":    len(all_trades),
            "futures_trades":  len(self.futures_trades),
            "options_trades":  len(self.options_trades),
            "net_pnl":         round(sum(pnls), 2),
            "win_rate":        round(sum(1 for p in pnls if p > 0) / max(1, len(pnls)) * 100, 1),
        }


class MarketSimulator:
    """
    Replays historical market data as a live feed.

    Usage:
        sim = MarketSimulator("BANKNIFTY", mode=SimMode.PAPER_SIM)
        sim.on_bar(my_strategy_callback)
        sim.on_bar(my_options_callback)
        result = sim.run(from_date="2025-01-01", to_date="2025-06-30")
        print(result.summary())
    """

    # Historical IV estimates by month (captures seasonal patterns)
    _MONTHLY_IV = {
        1: 0.17, 2: 0.16, 3: 0.18, 4: 0.17,
        5: 0.19, 6: 0.20, 7: 0.18, 8: 0.17,
        9: 0.21, 10: 0.20, 11: 0.18, 12: 0.17,
    }

    def __init__(
        self,
        symbol:      str = "BANKNIFTY",
        timeframe:   str = "5min",
        mode:        SimMode = SimMode.PAPER_SIM,
        speed:       float = 0,           # 0 = max speed, 1.0 = realtime
        capital:     float = 100000.0,
    ):
        self.symbol    = symbol
        self.timeframe = timeframe
        self.mode      = mode
        self.speed     = speed
        self.capital   = capital

        self._dp         = DataPipeline()
        self._chain_feed = ChainFeed()      # no broker — pure BS chains
        self._bs         = BSModel()
        self._bar_callbacks:    list[Callable] = []
        self._chain_callbacks:  list[Callable] = []
        self._session_callbacks: list[Callable] = []
        self._result = SimResult(symbol=symbol, mode=mode.value,
                                 from_date=datetime.now(), to_date=datetime.now())

    # ── Registration ────────────────────────────────────────────

    def on_bar(self, callback: Callable) -> None:
        """Register callback(sim_bar, price_df_so_far) — fires on each bar close."""
        self._bar_callbacks.append(callback)

    def on_chain(self, callback: Callable) -> None:
        """Register callback(sim_bar, chain) — fires with option chain each bar."""
        self._chain_callbacks.append(callback)

    def on_session_end(self, callback: Callable) -> None:
        """Register callback(date, daily_result) — fires at end of each day."""
        self._session_callbacks.append(callback)

    # ── Main run ────────────────────────────────────────────────

    def run(
        self,
        from_date: str | datetime = None,
        to_date:   str | datetime = None,
        n_days:    int = None,
    ) -> SimResult:
        """
        Run simulation over date range.

        Args:
            from_date: start date string "YYYY-MM-DD" or datetime
            to_date:   end date string or datetime
            n_days:    alternatively, just specify number of past days

        Returns SimResult with all trade data collected.
        """
        # ── Load data ─────────────────────────────────────────
        df = self._dp.get(self.symbol, self.timeframe)
        if df.empty:
            logger.error(f"No data for {self.symbol} {self.timeframe}. Run setup.py first.")
            return self._result

        df.index = pd.to_datetime(df.index)

        # Filter by date range
        if n_days:
            cutoff = df.index[-1] - timedelta(days=n_days)
            df = df[df.index >= cutoff]
        else:
            if from_date:
                from_dt = pd.Timestamp(from_date)
                df = df[df.index >= from_dt]
            if to_date:
                to_dt = pd.Timestamp(to_date)
                df = df[df.index <= to_dt]

        if df.empty:
            logger.error("No data in specified date range.")
            return self._result

        self._result.from_date  = df.index[0].to_pydatetime()
        self._result.to_date    = df.index[-1].to_pydatetime()
        self._result.total_bars = len(df)

        logger.info(
            f"Simulator starting | {self.symbol} | {self.mode.value} | "
            f"{len(df):,} bars | {self._result.from_date.date()} → {self._result.to_date.date()}"
        )

        # ── Bar-by-bar replay ──────────────────────────────────
        session_trades = []
        current_date   = None

        for i in range(50, len(df)):   # 50-bar warmup
            row = df.iloc[i]
            ts  = row.name.to_pydatetime()

            # Session boundary
            if current_date and ts.date() != current_date:
                # Fire end-of-session callbacks
                for cb in self._session_callbacks:
                    try:
                        cb(current_date, session_trades)
                    except Exception as e:
                        logger.warning(f"Session callback error: {e}")
                session_trades = []
            current_date = ts.date()

            # Build SimBar
            iv_est  = self._estimate_iv(ts, df.iloc[max(0,i-20):i])
            session_mins = (ts.hour * 60 + ts.minute) - (9 * 60 + 15)
            total_mins   = (15 * 60 + 30) - (9 * 60 + 15)

            sim_bar = SimBar(
                timestamp   = ts,
                symbol      = self.symbol,
                open        = float(row["open"]),
                high        = float(row["high"]),
                low         = float(row["low"]),
                close       = float(row["close"]),
                volume      = int(row.get("volume", 0)),
                bar_index   = i,
                total_bars  = len(df),
                session_pct = max(0, min(1, session_mins / total_mins)),
                iv_estimate = iv_est,
            )

            price_window = df.iloc[max(0, i - 200): i + 1]

            # Fire bar callbacks (futures strategies)
            for cb in self._bar_callbacks:
                try:
                    cb(sim_bar, price_window)
                except Exception as e:
                    logger.warning(f"Bar callback error at {ts}: {e}")

            # Fire chain callbacks (options strategies)
            if self._chain_callbacks:
                chain = self._chain_feed.build_from_spot(
                    self.symbol, float(row["close"]), sigma=iv_est
                )
                for cb in self._chain_callbacks:
                    try:
                        cb(sim_bar, chain, price_window)
                    except Exception as e:
                        logger.warning(f"Chain callback error at {ts}: {e}")

            # Speed control
            if self.speed > 0 and self.mode == SimMode.REPLAY:
                time.sleep(self.speed * 5)   # 5min bar → speed×5s delay

            # Progress every 500 bars
            if (i - 50) % 500 == 0:
                pct = (i - 50) / (len(df) - 50) * 100
                logger.info(f"  Sim progress: {pct:.0f}% | {ts.date()} | bar {i}/{len(df)}")

        logger.info(f"Simulation complete | {self._result.total_bars} bars processed")
        return self._result

    def _estimate_iv(self, ts: datetime, recent_df: pd.DataFrame) -> float:
        """
        Estimate IV for this bar from recent price action.
        Uses realised volatility scaled to match typical IV premium.
        """
        monthly_base = self._MONTHLY_IV.get(ts.month, 0.18)

        if len(recent_df) >= 10:
            ret = recent_df["close"].pct_change().dropna()
            if len(ret) > 0:
                realvol = float(ret.std() * np.sqrt(252 * 78))  # annualised 5min vol
                # IV typically trades at 10-30% premium to realised vol
                iv = realvol * 1.2
                # Blend with monthly average to avoid extreme values
                iv = 0.6 * iv + 0.4 * monthly_base
                return round(float(np.clip(iv, 0.08, 0.60)), 4)

        return monthly_base

    # ── Trade recording (called by paper execution) ─────────────

    def record_futures_trade(self, trade: dict) -> None:
        self._result.futures_trades.append(trade)

    def record_options_trade(self, trade: dict) -> None:
        self._result.options_trades.append(trade)
