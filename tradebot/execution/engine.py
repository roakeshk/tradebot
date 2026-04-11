# ============================================================
#  tradebot / execution / engine.py
#  Live execution engine — the final assembly.
#
#  Signal flow on each candle close:
#    Data → Indicators → Regime → Engine A (algo signals)
#      → Engine B (AI confidence score)
#        → Risk manager (position sizing, daily limits)
#          → Broker (place order)
#            → Position monitor (SL/target/EOD exit)
#              → Logger + Alerts
#
#  This engine works with ANY broker via the factory.
#  Switch ACTIVE_BROKER in settings.py to move between
#  paper, zerodha, and shoonya without changing this file.
# ============================================================

import logging
import time
import threading
from datetime import datetime, date
from typing import Optional
from pathlib import Path

import pandas as pd

from broker.factory import get_broker, get_data_source
from broker.base import Order, OrderSide, OrderType, OrderStatus
from data.pipeline import DataPipeline
from strategy.strategies import build_strategies
from strategy.regime import RegimeClassifier
from strategy.base_strategy import Signal, Direction
from risk.manager import RiskManager
from risk.cost_model import CostModel
from ai.classifier import MultiStrategyClassifier
from alerts.notifier import Notifier
from utils.railway_push import get_pusher
from utils.logger import setup_logging
from config.settings import RISK, SESSION, PRIMARY_TF, INSTRUMENTS, ACTIVE_BROKER, MARKET


# Parse session times once at import
def _parse_session_time(time_str: str) -> int:
    """Convert 'HH:MM' to minutes-since-midnight."""
    h, m = map(int, time_str.split(":"))
    return h * 60 + m

_MARKET_OPEN  = _parse_session_time(SESSION["market_open"])
_MARKET_CLOSE = _parse_session_time(SESSION["market_close"])
_NO_TRADE     = _parse_session_time(SESSION["no_trade_after"])
_FIRST_END    = _parse_session_time(SESSION["first_candle_end"])

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """
    Main live trading loop.

    Supports both paper trading and live trading.
    Engine B (AI) is optional — gracefully degrades to
    algo-only if classifiers are not yet trained.
    """

    def __init__(
        self,
        symbol:       str   = None,
        timeframe:    str   = "5min",
        capital:      float = None,
        use_ai:       bool  = True,
        ai_threshold: float = 0.55,
        broker_name:  str   = None,
    ):
        _def_sym = list(INSTRUMENTS.keys())[0] if INSTRUMENTS else "SPY"
        self.symbol     = symbol or _def_sym
        self.timeframe  = timeframe
        self.use_ai     = use_ai
        self.broker_name = broker_name or ACTIVE_BROKER

        # Core components
        self.broker     = get_broker(self.broker_name)
        self.data_src   = get_data_source()
        self.dp         = DataPipeline(broker=self.data_src)
        self.strategies = build_strategies(symbol)
        self.regime_clf = RegimeClassifier()
        self.risk       = RiskManager(capital)
        cost_key = self.broker_name if self.broker_name != "paper" else ("us_paper" if MARKET == "US" else "zerodha")
        self.cost       = CostModel(cost_key)
        self.notifier   = Notifier()
        self.pusher     = get_pusher()

        # AI layer (Engine B)
        strategy_names = [s.name for s in self.strategies]
        self.ai = MultiStrategyClassifier(strategy_names, threshold=ai_threshold) if use_ai else None

        # State
        self._running       = False
        self._df_window:    pd.DataFrame = pd.DataFrame()
        self._open_trades:  dict         = {}   # order_id → trade info
        self._last_bar_ts:  Optional[datetime] = None
        self._today:        Optional[date]      = None

        # Trade log
        self._log_dir = Path("data/processed")
        self._log_dir.mkdir(parents=True, exist_ok=True)

        inst     = INSTRUMENTS.get(symbol, {})
        self._lot_size = inst.get("lot_size", 1 if MARKET == "US" else 15)

        ai_status = "ON" if (use_ai and self.ai and self.ai.all_trained()) else "OFF (not trained)"
        logger.info(
            f"ExecutionEngine ready | {symbol} {timeframe} | "
            f"broker={self.broker_name} | AI={ai_status}"
        )

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self) -> None:
        """Connect broker and start the main loop."""
        logger.info("Connecting broker...")
        if not self.broker.connect():
            raise RuntimeError("Broker connection failed. Check credentials.")

        # Warm up data window
        self._load_warmup_data()

        self._running = True
        self.risk.reset_day()

        mode = "PAPER" if self.broker_name == "paper" else "LIVE"
        cur = "$" if MARKET == "US" else "₹"
        logger.info(f"Engine started [{mode}] | capital={cur}{self.risk.capital:,.0f}")
        self.notifier.send(
            f"TradeBot started [{mode}]\n"
            f"Symbol: {self.symbol} | Capital: {cur}{self.risk.capital:,.0f}"
        )

        try:
            self._main_loop()
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            self.stop()

    def stop(self) -> None:
        """Graceful shutdown — close all positions, save logs."""
        self._running = False
        self._close_all_positions("SHUTDOWN")
        self._save_daily_log()
        self.broker.disconnect()

        summary = self.risk.status()
        cur = "$" if MARKET == "US" else "₹"
        logger.info(f"Engine stopped | daily P&L={cur}{summary['daily_pnl']:,.0f}")
        self.notifier.send(
            f"TradeBot stopped\n"
            f"Daily P&L: {cur}{summary['daily_pnl']:,.0f} | "
            f"Trades: {summary['trades_today']}"
        )

    # ── Main loop ─────────────────────────────────────────────

    def _main_loop(self) -> None:
        """Poll for new candles every 30 seconds."""
        logger.info("Main loop started. Waiting for new candles...")
        while self._running:
            try:
                now = datetime.now()

                # Daily reset
                if now.date() != self._today:
                    self._on_new_day(now)

                if not self._is_market_hours(now):
                    time.sleep(60)
                    continue

                # EOD — close all and halt
                if self._is_eod(now):
                    self._close_all_positions("EOD")
                    logger.info("EOD: all positions closed, halting until next session")
                    while self._is_eod(datetime.now()) or not self._is_market_hours(datetime.now()):
                        time.sleep(60)
                    continue

                # Fetch latest candle
                latest = self.dp.get_latest_candle(self.symbol, self.timeframe)
                if latest is None:
                    time.sleep(30)
                    continue

                bar_ts = pd.to_datetime(latest["timestamp"])
                if bar_ts == self._last_bar_ts:
                    time.sleep(30)
                    continue

                self._last_bar_ts = bar_ts
                self._on_new_candle(latest, bar_ts)

            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                self.notifier.send(f"⚠️ Engine error: {e}")
                time.sleep(30)

    def _on_new_candle(self, candle: pd.Series, ts: datetime) -> None:
        """Full pipeline on each new completed candle."""

        # Append to rolling window
        new_row = pd.DataFrame([{
            "open": float(candle["open"]), "high": float(candle["high"]),
            "low":  float(candle["low"]),  "close": float(candle["close"]),
            "volume": int(candle.get("volume", 0)), "oi": int(candle.get("oi", 0)),
        }], index=[ts])
        self._df_window = pd.concat([self._df_window, new_row]).tail(600)

        # Update paper broker price if applicable
        if hasattr(self.broker, "update_price"):
            self.broker.update_price(self.symbol, float(candle["close"]))

        # Push live status to Railway every bar
        try:
            self.pusher.push_status(self.risk.status())
        except Exception:
            pass

        logger.info(
            f"[{ts.strftime('%H:%M')}] "
            f"O={candle['open']:.0f} H={candle['high']:.0f} "
            f"L={candle['low']:.0f}  C={candle['close']:.0f} "
            f"V={int(candle.get('volume',0)):,}"
        )

        # ── Step 1: Manage open positions ─────────────────────
        self._manage_open_positions(candle, ts)

        # ── Step 2: Check if we can take new trades ───────────
        status = self.risk.status()
        if status["halted"]:
            logger.warning("Daily loss limit hit — no new trades today")
            return
        if status["open_positions"] >= RISK["max_open_positions"]:
            return
        if self._is_no_trade_zone(ts):
            return

        # ── Step 3: Classify market regime ────────────────────
        regime = None
        try:
            regime = self.regime_clf.classify(self._df_window.tail(60))
            if not regime.is_tradeable():
                logger.debug(f"Regime={regime.regime.value} — skipping")
                return
        except Exception as e:
            logger.warning(f"Regime error: {e}")

        # ── Step 4: Generate algo signals (Engine A) ──────────
        for strategy in self.strategies:
            try:
                signals = strategy.generate_signals(self._df_window, regime)
            except Exception as e:
                logger.warning(f"Strategy {strategy.name} error: {e}")
                continue

            for signal in signals:
                self._process_signal(signal, ts)
                break  # one signal per strategy per bar

    def _process_signal(self, signal: Signal, ts: datetime) -> None:
        """Risk-check, AI-filter, and execute a signal."""

        # ── Engine B: AI confidence filter ────────────────────
        ai_score = 0.5
        if self.use_ai and self.ai and self.ai.all_trained():
            approved_ai, ai_score = self.ai.approve_signal(signal.strategy, self._df_window)
            if not approved_ai:
                logger.info(f"  [AI BLOCK] {signal.strategy} score={ai_score:.3f} < threshold")
                return
            logger.info(f"  [AI OK] {signal.strategy} score={ai_score:.3f}")
        elif self.use_ai and self.ai and not self.ai.all_trained():
            logger.debug("  [AI] Models not trained yet — running algo-only")

        # ── Risk manager gate ──────────────────────────────────
        size_result = self.risk.approve_signal(signal)
        if not size_result:
            logger.info(f"  [RISK BLOCK] {size_result.reason}")
            return

        lots     = size_result.lots
        quantity = lots * self._lot_size

        # ── Place order ────────────────────────────────────────
        order = Order(
            symbol=self.symbol,
            side=OrderSide.BUY if signal.direction == Direction.LONG else OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=quantity,
            tag=f"{signal.strategy[:12]}_{ts.strftime('%H%M')}",
        )

        try:
            order_id = self.broker.place_order(order)
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            return

        self.risk.record_open()
        self._open_trades[order_id] = {
            "signal":      signal,
            "lots":        lots,
            "quantity":    quantity,
            "entry_time":  ts,
            "sl":          signal.stop_loss,
            "target":      signal.target,
            "ai_score":    ai_score,
        }

        cur = "$" if MARKET == "US" else "₹"
        bep = self.cost.min_points_to_breakeven(self.symbol, lots, signal.entry_price)
        logger.info(
            f"  [ORDER] {signal.strategy} {signal.direction.value} "
            f"qty={quantity} entry={signal.entry_price:.2f} "
            f"SL={signal.stop_loss:.2f} T={signal.target:.2f} "
            f"RR={signal.rr_ratio:.2f} lots={lots} BEP={bep:.1f}pts"
        )
        self.notifier.send(
            f"{signal.strategy} {signal.direction.value}\n"
            f"Entry: {cur}{signal.entry_price:.2f} | SL: {cur}{signal.stop_loss:.2f} | "
            f"T: {cur}{signal.target:.2f} | R:R {signal.rr_ratio:.1f}\n"
            f"Lots: {lots} | AI: {ai_score:.2f}"
        )

    def _manage_open_positions(self, candle: pd.Series, ts: datetime) -> None:
        """Check SL/target hit for each open position."""
        for order_id, info in list(self._open_trades.items()):
            sig = info["signal"]
            sl, tgt = info["sl"], info["target"]
            is_long = sig.direction == Direction.LONG

            hit_sl  = candle["low"]  <= sl  if is_long else candle["high"] >= sl
            hit_tgt = candle["high"] >= tgt if is_long else candle["low"]  <= tgt

            if not (hit_sl or hit_tgt):
                continue

            exit_price  = sl  if hit_sl  else tgt
            exit_reason = "STOP_LOSS" if hit_sl else "TARGET"

            # Place exit order
            exit_order = Order(
                symbol=self.symbol,
                side=OrderSide.SELL if is_long else OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=info["quantity"],
                tag="exit",
            )
            try:
                self.broker.place_order(exit_order)
            except Exception as e:
                logger.error(f"Exit order failed: {e}")
                continue

            # Calculate P&L
            qty = info["quantity"]
            raw_pnl = (exit_price - sig.entry_price) * qty * (1 if is_long else -1)
            _, _, cost = self.cost.round_trip_cost(
                self.symbol, info["lots"], sig.entry_price, exit_price, is_long
            )
            net_pnl = raw_pnl - cost

            self.risk.record_close(net_pnl)
            del self._open_trades[order_id]

            cur = "$" if MARKET == "US" else "₹"
            emoji = "+" if net_pnl > 0 else "-"
            logger.info(
                f"  [{exit_reason}] @ {cur}{exit_price:.2f} | "
                f"gross={cur}{raw_pnl:,.2f} cost={cur}{cost:.2f} net={cur}{net_pnl:,.2f}"
            )
            self.notifier.send(
                f"{emoji} {exit_reason} — {sig.strategy}\n"
                f"Exit: {cur}{exit_price:.2f} | Net P&L: {cur}{net_pnl:,.2f}\n"
                f"Daily P&L: {cur}{self.risk.daily_pnl:,.2f}"
            )

    def _close_all_positions(self, reason: str) -> None:
        if not self._open_trades:
            return
        logger.info(f"Closing all positions: {reason}")
        positions = self.broker.get_positions()
        for pos in positions:
            try:
                order = Order(
                    symbol=self.symbol,
                    side=OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=pos.quantity,
                    tag=f"close_{reason[:10].lower()}",
                )
                self.broker.place_order(order)
                logger.info(f"Closed {pos.symbol} qty={pos.quantity} | reason={reason}")
            except Exception as e:
                logger.error(f"Failed to close position: {e}")
        self._open_trades.clear()

    # ── Daily management ──────────────────────────────────────

    def _on_new_day(self, now: datetime) -> None:
        self._today = now.date()
        self.risk.reset_day()
        logger.info(f"New trading day: {self._today}")
        self.notifier.send(f"New day: {self._today} | Capital: {('$' if MARKET == 'US' else '₹')}{self.risk.capital:,.0f}")

    def _load_warmup_data(self) -> None:
        logger.info("Loading warm-up window...")
        self._df_window = self.dp.get(self.symbol, self.timeframe, n_bars=600)
        if self._df_window.empty:
            self.dp.fetch_and_store(self.symbol, self.timeframe)
            self._df_window = self.dp.get(self.symbol, self.timeframe, n_bars=600)
        logger.info(f"Warm-up: {len(self._df_window)} bars loaded")

    def _save_daily_log(self) -> None:
        if hasattr(self.broker, "get_trade_log"):
            trades = self.broker.get_trade_log()
            if not trades.empty:
                path = self._log_dir / f"live_{self.symbol}_{datetime.now():%Y%m%d}.csv"
                trades.to_csv(path, index=False)
                logger.info(f"Trade log saved: {path}")
                # Push all trades to Railway
                for _, row in trades.iterrows():
                    try:
                        self.pusher.push_trade(row.to_dict())
                    except Exception:
                        pass

    # ── Time helpers ──────────────────────────────────────────

    @staticmethod
    def _is_market_hours(now: datetime) -> bool:
        t = now.hour * 60 + now.minute
        return _MARKET_OPEN <= t <= _MARKET_CLOSE

    @staticmethod
    def _is_eod(now: datetime) -> bool:
        t = now.hour * 60 + now.minute
        return t >= _NO_TRADE

    @staticmethod
    def _is_no_trade_zone(ts: datetime) -> bool:
        t = ts.hour * 60 + ts.minute
        return t < _FIRST_END or t > _NO_TRADE
