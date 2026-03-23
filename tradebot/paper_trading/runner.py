# ============================================================
#  tradebot / paper_trading / runner.py
#  Paper trading runner.
#
#  Connects to live market data (via broker API or websocket),
#  runs strategies on each new candle, fires paper orders,
#  and logs everything for analysis.
#
#  Run this for 3+ months before touching real capital.
#  Gate: <15% divergence from backtest results to proceed.
#
#  Usage:
#    python -m paper_trading.runner
#    python -m paper_trading.runner --symbol NIFTY
# ============================================================

import logging
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

from broker.paper_broker import PaperBroker
from broker.base import Order, OrderSide, OrderType
from data.pipeline import DataPipeline
from strategy.strategies import build_strategies
from strategy.regime import RegimeClassifier
from strategy.base_strategy import Direction
from risk.manager import RiskManager
from risk.cost_model import CostModel
from utils.logger import setup_logging
from config.settings import RISK, SESSION, PRIMARY_TF

logger = logging.getLogger(__name__)


class PaperTradingRunner:
    """
    Live paper trading engine.

    On each new candle close:
      1. Update price data window
      2. Classify market regime
      3. Run all active strategies
      4. Risk-check each signal
      5. Place paper orders
      6. Check open positions for exit
      7. Log everything
    """

    def __init__(
        self,
        symbol:      str = "BANKNIFTY",
        timeframe:   str = "5min",
        capital:     float = None,
        broker_name: str = "zerodha",
    ):
        self.symbol     = symbol
        self.timeframe  = timeframe
        self.broker_name = broker_name

        self.paper    = PaperBroker(capital, cost_model=broker_name)
        self.dp       = DataPipeline()
        self.regime   = RegimeClassifier()
        self.risk     = RiskManager(capital)
        self.strategies = build_strategies(symbol)
        self.cost     = CostModel(broker_name)

        self._running = False
        self._df_window: pd.DataFrame = pd.DataFrame()
        self._open_signals: dict = {}   # order_id → signal

        # Paper trade log
        self._log_path = Path("data/processed") / f"paper_{symbol}_{datetime.now():%Y%m%d}.csv"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"PaperRunner | {symbol} {timeframe} | capital=₹{self.risk.capital:,.0f}")

    # ── Main loop ─────────────────────────────────────────────

    def start(self) -> None:
        """
        Start paper trading.
        Polls for new candle data every 30 seconds.
        In production, replace with websocket callback.
        """
        self._running = True
        self.risk.reset_day()

        # Load historical window for indicator warm-up
        logger.info("Loading warm-up data...")
        self._df_window = self.dp.get(self.symbol, self.timeframe, n_bars=500)
        if self._df_window.empty:
            logger.info("Fetching fresh data for warm-up...")
            self.dp.fetch_and_store(self.symbol, self.timeframe)
            self._df_window = self.dp.get(self.symbol, self.timeframe, n_bars=500)

        logger.info(f"Warm-up complete | {len(self._df_window)} bars loaded")
        logger.info("Paper trading started. Press Ctrl+C to stop.")

        try:
            self._poll_loop()
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        self._running = False
        self._save_session_report()
        logger.info("Paper trading stopped.")

    def _poll_loop(self) -> None:
        """Poll for new candles every 30 seconds."""
        last_bar_time = self._df_window.index[-1] if not self._df_window.empty else None

        while self._running:
            try:
                now = datetime.now()
                if not self._is_market_hours(now):
                    time.sleep(60)
                    continue

                # Fetch latest bar
                latest = self.dp.get_latest_candle(self.symbol, self.timeframe)
                if latest is None:
                    time.sleep(30)
                    continue

                bar_time = pd.to_datetime(latest["timestamp"])
                if bar_time == last_bar_time:
                    time.sleep(30)
                    continue

                # New bar arrived
                last_bar_time = bar_time
                self._on_new_candle(latest, bar_time)

            except Exception as e:
                logger.error(f"Poll loop error: {e}", exc_info=True)
                time.sleep(30)

    def _on_new_candle(self, candle: pd.Series, ts: datetime) -> None:
        """Called on every new completed candle."""

        # Append to window
        new_row = pd.DataFrame([{
            "open": candle["open"], "high": candle["high"],
            "low": candle["low"],   "close": candle["close"],
            "volume": candle["volume"], "oi": candle.get("oi", 0),
        }], index=[ts])
        self._df_window = pd.concat([self._df_window, new_row]).tail(500)

        # Update paper broker price
        self.paper.update_price(self.symbol, candle["close"])

        logger.info(
            f"[BAR] {ts.strftime('%H:%M')} O={candle['open']:.0f} "
            f"H={candle['high']:.0f} L={candle['low']:.0f} C={candle['close']:.0f} "
            f"V={int(candle['volume']):,}"
        )

        # Check open position exits
        self._check_exits(candle, ts)

        # Skip signal generation if already have a position
        positions = self.paper.get_positions()
        if len(positions) >= RISK["max_open_positions"]:
            return

        # Classify regime
        regime_state = None
        try:
            regime_state = self.regime.classify(self._df_window.tail(60))
            if not regime_state.is_tradeable():
                logger.info(f"  Regime={regime_state.regime.value} — skipping signals")
                return
        except Exception as e:
            logger.warning(f"Regime classify error: {e}")

        # Generate signals
        for strat in self.strategies:
            try:
                signals = strat.generate_signals(self._df_window, regime_state)
                for sig in signals:
                    result = self.risk.approve_signal(sig)
                    if not result:
                        logger.info(f"  Signal blocked: {result.reason}")
                        continue

                    # Place paper order
                    order = Order(
                        symbol=self.symbol,
                        side=OrderSide.BUY if sig.direction == Direction.LONG else OrderSide.SELL,
                        order_type=OrderType.MARKET,
                        quantity=result.lots * 15,   # lots × lot_size
                        tag=sig.strategy[:20],
                    )
                    order_id = self.paper.place_order(order)
                    self._open_signals[order_id] = {
                        "signal": sig, "lots": result.lots,
                        "sl": sig.stop_loss, "target": sig.target,
                    }
                    self.risk.record_open()

                    logger.info(
                        f"  [SIGNAL] {sig.strategy} {sig.direction.value} "
                        f"entry={sig.entry_price:.0f} SL={sig.stop_loss:.0f} "
                        f"T={sig.target:.0f} RR={sig.rr_ratio:.2f} "
                        f"lots={result.lots}"
                    )
                    break   # one trade at a time per strategy type

            except Exception as e:
                logger.warning(f"Strategy {strat.name} error: {e}")

    def _check_exits(self, candle: pd.Series, ts: datetime) -> None:
        """Check if any open position should be exited."""
        positions = self.paper.get_positions()
        for pos in positions:
            for order_id, info in list(self._open_signals.items()):
                sig = info["signal"]
                sl  = info["sl"]
                tgt = info["target"]
                hit_sl  = candle["low"]  <= sl  if sig.direction == Direction.LONG  else candle["high"] >= sl
                hit_tgt = candle["high"] >= tgt if sig.direction == Direction.LONG  else candle["low"]  <= tgt
                eod     = ts.hour * 60 + ts.minute >= 15 * 60 + 15

                exit_reason = None
                exit_price  = None
                if hit_sl:
                    exit_reason, exit_price = "STOP_LOSS", sl
                elif hit_tgt:
                    exit_reason, exit_price = "TARGET",    tgt
                elif eod:
                    exit_reason, exit_price = "EOD",       candle["close"]

                if exit_reason:
                    close_order = Order(
                        symbol=self.symbol,
                        side=OrderSide.SELL if sig.direction == Direction.LONG else OrderSide.BUY,
                        order_type=OrderType.MARKET,
                        quantity=pos.quantity,
                        tag="exit",
                    )
                    self.paper.place_order(close_order)
                    pnl = pos.pnl
                    self.risk.record_close(pnl)
                    del self._open_signals[order_id]
                    logger.info(f"  [EXIT] {exit_reason} @ {exit_price:.0f} | PnL=₹{pnl:,.0f}")
                    break

    def _is_market_hours(self, now: datetime) -> bool:
        h, m = now.hour, now.minute
        total = h * 60 + m
        return 9 * 60 + 15 <= total <= 15 * 60 + 30

    def _save_session_report(self) -> None:
        summary = self.paper.get_daily_summary()
        trades  = self.paper.get_trade_log()
        logger.info(f"Session summary: {summary}")
        if not trades.empty:
            trades.to_csv(self._log_path, index=False)
            logger.info(f"Trade log saved: {self._log_path}")


if __name__ == "__main__":
    import sys
    import argparse
    sys.path.insert(0, str(Path(__file__).parent.parent))
    setup_logging("paper_trading")

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol",   default="BANKNIFTY")
    parser.add_argument("--capital",  default=100000, type=float)
    parser.add_argument("--broker",   default="zerodha")
    args = parser.parse_args()

    runner = PaperTradingRunner(args.symbol, capital=args.capital, broker_name=args.broker)
    runner.start()
