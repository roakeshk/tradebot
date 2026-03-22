# ============================================================
#  tradebot / backtest / engine.py
#  Walk-forward backtesting engine.
#
#  What is walk-forward testing?
#  ─────────────────────────────
#  Simple backtesting: optimise on ALL data → great numbers,
#  useless in live trading (you used future data to pick params).
#
#  Walk-forward:
#    Split data into rolling windows, e.g.:
#      Window 1: train on months 1–6, test on month 7
#      Window 2: train on months 2–7, test on month 8
#      Window 3: train on months 3–8, test on month 9
#      ...
#    Only the OUT-OF-SAMPLE (test) results matter.
#    If out-of-sample results hold up, the strategy has real edge.
#
#  This engine simulates bar-by-bar execution with full cost model.
#  It never looks ahead — on bar N, it only knows bars 0..N.
# ============================================================

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np

from strategy.base_strategy import StrategyBase, Signal, Direction
from strategy.regime import RegimeClassifier
from risk.cost_model import CostModel
from config.settings import RISK, INSTRUMENTS

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Represents a completed trade in the backtest."""
    strategy:    str
    symbol:      str
    direction:   Direction
    entry_time:  datetime
    entry_price: float
    exit_time:   Optional[datetime]
    exit_price:  float
    exit_reason: str          # "target" | "stop_loss" | "time_exit" | "end_of_data"
    lots:        int
    gross_pnl:   float        # before costs
    cost:        float        # round-trip transaction cost
    net_pnl:     float        # gross - cost
    regime:      str = ""
    signal_rr:   float = 0.0  # intended R:R from signal
    actual_rr:   float = 0.0  # realised R:R


@dataclass
class BacktestResult:
    """Full results of one backtest run."""
    symbol:         str
    strategy:       str
    from_date:      datetime
    to_date:        datetime
    is_oos:         bool      # True = out-of-sample window
    trades:         list[Trade] = field(default_factory=list)

    # Metrics (computed after run)
    total_trades:   int   = 0
    win_rate:       float = 0.0
    profit_factor:  float = 0.0
    sharpe:         float = 0.0
    max_drawdown:   float = 0.0
    total_net_pnl:  float = 0.0
    avg_win:        float = 0.0
    avg_loss:       float = 0.0
    expectancy:     float = 0.0   # avg net pnl per trade
    passed_gate:    bool  = False

    def compute_metrics(self) -> None:
        if not self.trades:
            return

        pnls = [t.net_pnl for t in self.trades]
        wins  = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        self.total_trades  = len(self.trades)
        self.win_rate      = round(len(wins) / len(pnls) * 100, 2)
        self.total_net_pnl = round(sum(pnls), 2)
        self.avg_win       = round(np.mean(wins)   if wins   else 0, 2)
        self.avg_loss      = round(np.mean(losses) if losses else 0, 2)
        self.expectancy    = round(np.mean(pnls), 2)

        gross_wins  = sum(p for p in pnls if p > 0)
        gross_losses = abs(sum(p for p in pnls if p < 0))
        self.profit_factor = round(gross_wins / gross_losses, 3) if gross_losses > 0 else 999.0

        # Sharpe (annualised, using daily pnl grouping)
        daily_pnl = pd.Series(pnls)
        if daily_pnl.std() > 0:
            self.sharpe = round(daily_pnl.mean() / daily_pnl.std() * np.sqrt(252), 3)

        # Max drawdown
        cumulative = pd.Series(pnls).cumsum()
        roll_max   = cumulative.cummax()
        drawdown   = (cumulative - roll_max)
        self.max_drawdown = round(abs(drawdown.min()), 2)

        # Gate check — minimum requirements to proceed to paper trading
        self.passed_gate = (
            self.total_trades >= 200 and       # enough sample size
            self.win_rate >= 55.0 and          # minimum win rate
            self.profit_factor >= 1.4 and      # profit factor
            self.max_drawdown < 12000 and      # max drawdown < ₹12,000
            self.expectancy > 0                # positive expectancy
        )

    def summary(self) -> str:
        gate = "PASS" if self.passed_gate else "FAIL"
        oos  = "OOS" if self.is_oos else "IS "
        return (
            f"[{oos}] {self.strategy:30s} | "
            f"Trades:{self.total_trades:4d} | "
            f"WR:{self.win_rate:5.1f}% | "
            f"PF:{self.profit_factor:5.2f} | "
            f"Sharpe:{self.sharpe:5.2f} | "
            f"MaxDD:₹{self.max_drawdown:8,.0f} | "
            f"NetPnL:₹{self.total_net_pnl:9,.0f} | "
            f"Expect:₹{self.expectancy:6.0f} | "
            f"[{gate}]"
        )


class BacktestEngine:
    """
    Bar-by-bar backtesting engine with full cost model.

    Usage:
        engine = BacktestEngine(symbol="BANKNIFTY", lots=1)
        from strategy.strategies import VWAPReversion
        strategy = VWAPReversion("BANKNIFTY")
        result = engine.run(df, strategy)
        print(result.summary())
    """

    def __init__(
        self,
        symbol:     str,
        lots:       int = 1,
        broker:     str = "zerodha",
        warmup_bars: int = 50,   # bars needed before first signal attempt
    ):
        self.symbol      = symbol
        self.lots        = lots
        self.cost_model  = CostModel(broker)
        self.warmup      = warmup_bars
        self.regime_clf  = RegimeClassifier()
        self.inst        = INSTRUMENTS.get(symbol, {})
        self.lot_size    = self.inst.get("lot_size", 1)

    def run(
        self,
        df:       pd.DataFrame,
        strategy: StrategyBase,
        is_oos:   bool = False,
    ) -> BacktestResult:
        """
        Run strategy bar-by-bar on df.
        Returns BacktestResult with all trades and metrics.
        """
        result = BacktestResult(
            symbol=self.symbol,
            strategy=strategy.name,
            from_date=df.index[0],
            to_date=df.index[-1],
            is_oos=is_oos,
        )

        active_trade: Optional[dict] = None
        trades_today: int = 0
        last_date:    Optional[datetime] = None

        for i in range(self.warmup, len(df) - 1):
            bar     = df.iloc[i]
            next_bar = df.iloc[i + 1]     # used for fill simulation
            window  = df.iloc[:i + 1]     # everything up to and including this bar

            # Reset daily trade counter
            try:
                cur_date = bar.name.date()
                if cur_date != last_date:
                    trades_today = 0
                    last_date = cur_date
            except AttributeError:
                cur_date = None

            # ── Manage open trade ─────────────────────────────
            if active_trade is not None:
                trade = self._manage_trade(active_trade, bar, next_bar)
                if trade is not None:
                    result.trades.append(trade)
                    active_trade = None
                    trades_today += 1
                continue   # only one position at a time

            # ── Daily limits ──────────────────────────────────
            if trades_today >= RISK["max_trades_per_day"]:
                continue

            # ── Check daily loss ──────────────────────────────
            daily_pnl = sum(
                t.net_pnl for t in result.trades
                if hasattr(t.entry_time, "date") and t.entry_time.date() == cur_date
            )
            if daily_pnl < -(RISK["max_capital_inr"] * RISK["max_daily_loss_pct"] / 100):
                continue   # halt for the day

            # ── Classify regime (every 15 bars for speed) ─────
            regime = None
            if i % 15 == 0 or i == self.warmup:
                try:
                    regime = self.regime_clf.classify(window.tail(50))
                except Exception:
                    pass

            # ── Generate signals ──────────────────────────────
            try:
                signals = strategy.generate_signals(window, regime)
            except Exception as e:
                logger.debug(f"Signal generation error at bar {i}: {e}")
                continue

            for sig in signals:
                if not sig.is_valid:
                    continue
                # Enter on next bar open (realistic — no same-bar fill)
                entry_price = next_bar["open"]
                active_trade = {
                    "signal":       sig,
                    "entry_price":  entry_price,
                    "entry_time":   next_bar.name,
                    "stop_loss":    sig.stop_loss,
                    "target":       sig.target,
                    "direction":    sig.direction,
                    "regime":       sig.regime or "",
                }
                break   # one signal at a time

        # Close any open trade at end of data
        if active_trade is not None:
            last_bar = df.iloc[-1]
            trade = self._force_close(active_trade, last_bar)
            if trade:
                result.trades.append(trade)

        result.compute_metrics()
        return result

    def _manage_trade(
        self,
        active: dict,
        bar:    pd.Series,
        next_bar: pd.Series,
    ) -> Optional[Trade]:
        """
        Check if trade exits on this bar.
        Checks (in order): stop-loss, target, end-of-session.
        Returns completed Trade or None if still open.
        """
        direction = active["direction"]
        sl        = active["stop_loss"]
        tgt       = active["target"]
        entry_p   = active["entry_price"]
        entry_t   = active["entry_time"]

        exit_price  = None
        exit_reason = None

        if direction == Direction.LONG:
            # Check stop-loss first (worst case)
            if bar["low"] <= sl:
                exit_price  = sl
                exit_reason = "stop_loss"
            elif bar["high"] >= tgt:
                exit_price  = tgt
                exit_reason = "target"
        else:  # SHORT
            if bar["high"] >= sl:
                exit_price  = sl
                exit_reason = "stop_loss"
            elif bar["low"] <= tgt:
                exit_price  = tgt
                exit_reason = "target"

        # End-of-session exit (15:15 IST)
        if exit_price is None:
            try:
                h, m = bar.name.hour, bar.name.minute
                if h * 60 + m >= 15 * 60 + 15:
                    exit_price  = bar["close"]
                    exit_reason = "time_exit"
            except AttributeError:
                pass

        if exit_price is None:
            return None   # trade still open

        return self._build_trade(active, exit_price, exit_reason, bar.name)

    def _force_close(self, active: dict, bar: pd.Series) -> Optional[Trade]:
        return self._build_trade(active, bar["close"], "end_of_data", bar.name)

    def _build_trade(
        self,
        active:      dict,
        exit_price:  float,
        exit_reason: str,
        exit_time:   datetime,
    ) -> Trade:
        direction   = active["direction"]
        entry_p     = active["entry_price"]
        qty         = self.lots * self.lot_size

        if direction == Direction.LONG:
            gross_pnl = (exit_price - entry_p) * qty
        else:
            gross_pnl = (entry_p - exit_price) * qty

        _, _, cost = self.cost_model.round_trip_cost(
            self.symbol, self.lots, entry_p, exit_price,
            is_long=(direction == Direction.LONG),
        )
        net_pnl = round(gross_pnl - cost, 2)

        risk = abs(entry_p - active["stop_loss"]) * qty
        actual_rr = round(gross_pnl / risk, 2) if risk > 0 else 0.0

        return Trade(
            strategy=active["signal"].strategy,
            symbol=self.symbol,
            direction=direction,
            entry_time=active["entry_time"],
            entry_price=round(entry_p, 2),
            exit_time=exit_time,
            exit_price=round(exit_price, 2),
            exit_reason=exit_reason,
            lots=self.lots,
            gross_pnl=round(gross_pnl, 2),
            cost=round(cost, 2),
            net_pnl=net_pnl,
            regime=active.get("regime", ""),
            signal_rr=active["signal"].rr_ratio,
            actual_rr=actual_rr,
        )


class WalkForwardRunner:
    """
    Runs walk-forward analysis across rolling time windows.

    For each window:
      - in_sample months: used to confirm strategy is working (no optimisation in this version)
      - oos_months:       strict out-of-sample test

    Aggregate OOS results give the true expected performance.
    """

    def __init__(
        self,
        symbol:      str,
        lots:        int = 1,
        broker:      str = "zerodha",
        in_sample_months:  int = 6,
        oos_months:        int = 1,
    ):
        self.symbol = symbol
        self.lots   = lots
        self.broker = broker
        self.in_months  = in_sample_months
        self.oos_months = oos_months

    def run(
        self,
        df:        pd.DataFrame,
        strategies: list[StrategyBase],
    ) -> dict[str, list[BacktestResult]]:
        """
        Run walk-forward on all strategies.
        Returns dict: strategy_name → list of OOS BacktestResult objects.
        """
        df.index = pd.to_datetime(df.index)
        engine   = BacktestEngine(self.symbol, self.lots, self.broker)
        results  = {s.name: [] for s in strategies}

        # Generate windows
        windows = self._build_windows(df)
        logger.info(f"Walk-forward: {len(windows)} windows × {len(strategies)} strategies")

        for w_idx, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
            oos_df = df[oos_start:oos_end]
            if len(oos_df) < 50:
                continue

            for strat in strategies:
                try:
                    result = engine.run(oos_df, strat, is_oos=True)
                    result.from_date = oos_start
                    result.to_date   = oos_end
                    results[strat.name].append(result)
                    logger.info(f"  W{w_idx+1} {result.summary()}")
                except Exception as e:
                    logger.warning(f"  W{w_idx+1} {strat.name} error: {e}")

        return results

    def _build_windows(self, df: pd.DataFrame) -> list[tuple]:
        windows = []
        start = df.index[0]
        end   = df.index[-1]
        is_delta  = pd.DateOffset(months=self.in_months)
        oos_delta = pd.DateOffset(months=self.oos_months)

        is_start = start
        while True:
            is_end    = is_start + is_delta
            oos_start = is_end
            oos_end   = oos_start + oos_delta
            if oos_end > end:
                break
            windows.append((is_start, is_end, oos_start, oos_end))
            is_start = is_start + oos_delta   # slide forward by 1 OOS period

        return windows

    def aggregate_oos(self, results: dict[str, list[BacktestResult]]) -> pd.DataFrame:
        """
        Aggregate all OOS windows into summary statistics per strategy.
        This is the number that matters — not IS performance.
        """
        rows = []
        for strat_name, res_list in results.items():
            if not res_list:
                continue
            all_trades = [t for r in res_list for t in r.trades]
            if not all_trades:
                continue

            # Build a synthetic combined result
            combined = BacktestResult(
                symbol=self.symbol,
                strategy=strat_name,
                from_date=res_list[0].from_date,
                to_date=res_list[-1].to_date,
                is_oos=True,
                trades=all_trades,
            )
            combined.compute_metrics()

            rows.append({
                "strategy":      strat_name,
                "windows":       len(res_list),
                "total_trades":  combined.total_trades,
                "win_rate":      combined.win_rate,
                "profit_factor": combined.profit_factor,
                "sharpe":        combined.sharpe,
                "max_drawdown":  combined.max_drawdown,
                "net_pnl":       combined.total_net_pnl,
                "expectancy":    combined.expectancy,
                "passed_gate":   combined.passed_gate,
            })

        df = pd.DataFrame(rows)
        if df.empty or "profit_factor" not in df.columns:
            return df
        return df.sort_values("profit_factor", ascending=False)
