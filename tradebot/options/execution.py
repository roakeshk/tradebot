# ============================================================
#  tradebot / options / execution.py
#  T6 + T7 — Position manager + Execution engine
#
#  Manages the full lifecycle of options positions:
#    Open → Monitor → Adjust → Close
#
#  Execution specifics for options:
#    - Always use LIMIT orders with a buffer (options spread is wide)
#    - Multi-leg positions: send legs simultaneously or in sequence
#    - Exit rule: 50% profit OR 2× loss (whichever comes first)
#    - EOD exit: close all positions near market close (avoid gamma risk)
#    - Roll logic: when DTE ≤ 2, roll to next week if position profitable
# ============================================================

import logging
from datetime import datetime, date
from typing import Optional
from pathlib import Path

import pandas as pd

from options.strategies import OptionsPosition, OptionsLeg
from options.data import OptionChain, OptionsDataPipeline
from options.risk import OptionsRiskManager
from options.signals import OptionsSignalEngine, OptionsSignal
from broker.base import BrokerBase, Order, OrderSide, OrderType
from broker.paper_broker import PaperBroker
from alerts.notifier import Notifier
from config.settings import INSTRUMENTS, MARKET, SESSION, COST_MODEL

_CUR = "$" if MARKET == "US" else "₹"
_DEF_BROKER = "us_paper" if MARKET == "US" else "zerodha"

logger = logging.getLogger(__name__)


class OptionsExecutionEngine:
    """
    Live options execution engine.

    Works in paper mode (default) or live mode.
    Integrates with the existing ExecutionEngine via a unified runner.
    """

    def __init__(
        self,
        symbol:      str = None,
        broker:      BrokerBase = None,
        capital:     float = 100000,
        use_paper:   bool  = True,
    ):
        _def_sym = list(INSTRUMENTS.keys())[0] if INSTRUMENTS else "SPY"
        self.symbol    = symbol or _def_sym
        self.broker    = broker or PaperBroker(capital, cost_model=_DEF_BROKER)
        self.use_paper = use_paper
        self.odp       = OptionsDataPipeline(None if use_paper else broker)
        self.signal_eng= OptionsSignalEngine()
        self.risk      = OptionsRiskManager(capital)
        self.notifier  = Notifier()

        self._open_positions: list[OptionsPosition] = []
        self._closed_trades:  list[dict]            = []
        self._price_window:   pd.DataFrame          = pd.DataFrame()

        log_dir = Path("data/processed")
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_dir / f"options_{symbol}_{datetime.now():%Y%m%d}.csv"

        logger.info(f"OptionsExecutionEngine | {symbol} | {'paper' if use_paper else 'live'}")

    # ── Main candle handler ───────────────────────────────────

    def on_new_candle(
        self,
        candle:   pd.Series,
        ts:       datetime,
        price_df: pd.DataFrame,
    ) -> None:
        """
        Called on each new 5-min candle close.
        Manage existing positions first, then look for new entries.
        """
        self._price_window = price_df

        # Get fresh option chain
        chain = self.odp.get_chain(self.symbol)
        if chain is None:
            logger.warning("No option chain available")
            return

        # ── Step 1: Manage existing positions ─────────────────
        for pos in list(self._open_positions):
            self._manage_position(pos, chain, ts)

        # ── Step 2: Check risk limits ─────────────────────────
        status = self.risk.status()
        if status["halted"]:
            return
        if status["open_positions"] >= 3:
            return
        if self._is_no_trade_zone(ts):
            return

        # ── Step 3: Generate signal ───────────────────────────
        signal = self.signal_eng.generate(chain, price_df, lots=1)
        if signal is None or not signal.is_valid:
            return

        # ── Step 4: Risk approval ─────────────────────────────
        result = self.risk.approve_position(signal.position, chain)
        if not result:
            logger.info(f"Options risk blocked: {result.reason}")
            return

        # Scale position to approved lots
        signal.position = self._scale_lots(signal.position, result.lots)

        # ── Step 5: Execute ───────────────────────────────────
        success = self._execute_entry(signal.position, ts)
        if success:
            self._open_positions.append(signal.position)
            self.risk.record_open(signal.position)
            self._notify_entry(signal)

    # ── Position management ───────────────────────────────────

    def _manage_position(
        self,
        pos:   OptionsPosition,
        chain: OptionChain,
        ts:    datetime,
    ) -> None:
        """Check exit conditions and close if triggered."""
        should_exit, reason = pos.should_exit(chain)

        # EOD exit
        t = ts.hour * 60 + ts.minute
        _nh, _nm = map(int, SESSION["no_trade_after"].split(":"))
        if t >= _nh * 60 + _nm:
            should_exit = True
            reason      = "EOD"

        if not should_exit:
            return

        pnl = self._execute_exit(pos, chain, ts, reason)
        self._open_positions.remove(pos)
        self.risk.record_exit(pos, pnl)
        pos.status = "closed"

        self._closed_trades.append({
            "strategy":   pos.strategy_name,
            "symbol":     pos.symbol,
            "expiry":     str(pos.expiry),
            "entry_time": str(pos.entry_time),
            "exit_time":  str(ts),
            "exit_reason":reason,
            "net_pnl":    pnl,
            "legs":       len(pos.legs),
        })

        emoji = "✅" if pnl > 0 else "❌"
        logger.info(f"Options {reason}: {pos.strategy_name} P&L={_CUR}{pnl:,.0f}")
        self.notifier.send(
            f"{emoji} Options {reason} — {pos.strategy_name}\n"
            f"P&L: {_CUR}{pnl:,.0f} | Reason: {reason}"
        )

    # ── Entry execution ───────────────────────────────────────

    def _execute_entry(self, pos: OptionsPosition, ts: datetime) -> bool:
        """
        Place all legs of the position as limit orders.
        For paper mode: fills are immediate at market price.
        For live mode: use limit orders with 0.5% buffer.
        """
        try:
            for leg in pos.legs:
                side    = OrderSide.SELL if leg.action == "sell" else OrderSide.BUY
                # Limit price: add 1% buffer for buys, subtract for sells
                buffer  = leg.premium * 0.01
                limit   = leg.premium + buffer if leg.action == "buy" else leg.premium - buffer
                limit   = round(limit, 2)

                order = Order(
                    symbol=self._option_symbol(leg),
                    side=side,
                    order_type=OrderType.LIMIT if not self.use_paper else OrderType.MARKET,
                    quantity=leg.quantity,
                    price=limit,
                    tag=f"opt_{pos.strategy_name[:8]}_{leg.option_type}{int(leg.strike)}",
                )

                if self.use_paper:
                    from broker.paper_broker import PaperBroker
                    if isinstance(self.broker, PaperBroker):
                        self.broker.update_price(order.symbol, leg.premium)

                order_id    = self.broker.place_order(order)
                leg.order_id = order_id

                logger.info(
                    f"  {'SELL' if side==OrderSide.SELL else 'BUY'} "
                    f"{leg.strike}{leg.option_type.upper()} "
                    f"@ {_CUR}{leg.premium:.2f} qty={leg.quantity}"
                )
            return True
        except Exception as e:
            logger.error(f"Options entry execution failed: {e}")
            return False

    # ── Exit execution ────────────────────────────────────────

    def _execute_exit(
        self,
        pos:    OptionsPosition,
        chain:  OptionChain,
        ts:     datetime,
        reason: str,
    ) -> float:
        """Close all legs of the position. Returns net P&L."""
        total_pnl = 0.0
        for leg in pos.legs:
            # Get current market price
            row   = chain.get_strike(leg.strike)
            curr  = float(row[f"{leg.option_type}_ltp"]) if row is not None else leg.premium

            # P&L: if we sold, we profit when price falls; if we bought, profit when rises
            pnl_per_unit = (leg.premium - curr) if leg.action == "sell" else (curr - leg.premium)
            leg_pnl      = pnl_per_unit * leg.quantity
            total_pnl   += leg_pnl

            # Place closing order (opposite side)
            close_side = OrderSide.BUY if leg.action == "sell" else OrderSide.SELL
            close_order = Order(
                symbol=self._option_symbol(leg),
                side=close_side,
                order_type=OrderType.MARKET,
                quantity=leg.quantity,
                tag=f"opt_exit_{reason[:6]}",
            )
            try:
                self.broker.place_order(close_order)
            except Exception as e:
                logger.warning(f"Exit order error: {e}")

        # Deduct transaction costs (options have lower cost than futures)
        cost = self._estimate_cost(pos)
        return round(total_pnl - cost, 2)

    # ── Helpers ───────────────────────────────────────────────

    def _option_symbol(self, leg: OptionsLeg) -> str:
        """
        Construct NSE option symbol string.
        Format: BANKNIFTY25APR48000CE
        """
        exp_str = leg.expiry.strftime("%d%b%y").upper()
        return f"{leg.symbol}{exp_str}{int(leg.strike)}{leg.option_type.upper()}"

    def _estimate_cost(self, pos: OptionsPosition) -> float:
        """
        Approximate transaction cost for options.
        NSE options: STT only on SELL side (0.05% of premium × qty)
        + exchange charge + GST + brokerage
        Much lower than futures per trade.
        """
        total = 0.0
        cm = COST_MODEL.get(_DEF_BROKER, {})
        slip_ticks = COST_MODEL.get("slippage_ticks", 1)
        for leg in pos.legs:
            notional   = leg.premium * leg.quantity
            brokerage  = cm.get("brokerage_per_order", 0)
            stt        = notional * cm.get("stt_pct_sell", 0) if leg.action == "sell" else 0
            exc_charge = notional * cm.get("exchange_txn_charge_pct", 0)
            gst        = brokerage * cm.get("gst_pct", 0)
            total     += brokerage + stt + exc_charge + gst
        return round(total, 2)

    def _scale_lots(self, pos: OptionsPosition, lots: int) -> OptionsPosition:
        """Scale all legs to approved lot count."""
        for leg in pos.legs:
            leg.lots = lots
        return pos

    def _notify_entry(self, signal: OptionsSignal) -> None:
        pos = signal.position
        legs_str = " | ".join(
            f"{'S' if l.action=='sell' else 'B'} {int(l.strike)}{l.option_type.upper()} {_CUR}{l.premium:.0f}"
            for l in pos.legs
        )
        self.notifier.send(
            f"📊 Options entry — {signal.strategy_name}\n"
            f"{signal.symbol} | {legs_str}\n"
            f"IV rank: {signal.iv_rank:.0f} | PCR: {signal.pcr:.2f}\n"
            f"Max profit: {_CUR}{pos.max_profit:,.0f} | Max loss: {_CUR}{pos.max_loss:,.0f}\n"
            f"Breakevens: {pos.breakevens}"
        )

    @staticmethod
    def _is_no_trade_zone(ts: datetime) -> bool:
        t = ts.hour * 60 + ts.minute
        _oh, _om = map(int, SESSION["market_open"].split(":"))
        _nh, _nm = map(int, SESSION["no_trade_after"].split(":"))
        return t < _oh * 60 + _om or t > _nh * 60 + _nm

    # ── Reporting ─────────────────────────────────────────────

    def save_trades(self) -> None:
        if self._closed_trades:
            pd.DataFrame(self._closed_trades).to_csv(self._log_path, index=False)
            logger.info(f"Options trades saved: {self._log_path}")

    def daily_summary(self) -> dict:
        pnls = [t["net_pnl"] for t in self._closed_trades]
        return {
            "total_trades":   len(pnls),
            "total_pnl":      round(sum(pnls), 2),
            "win_rate":       round(sum(1 for p in pnls if p > 0) / max(1, len(pnls)) * 100, 1),
            "open_positions": len(self._open_positions),
        }
