# ============================================================
#  tradebot / broker / paper_broker.py
#  Simulated broker for paper trading phase.
#  Mimics real fills using live or replayed market data.
#  No real money involved — but tracks everything as if it were.
# ============================================================

import uuid
import logging
from datetime import datetime
from typing import Optional, Callable
import pandas as pd

from broker.base import (
    BrokerBase, Order, Position, AccountInfo,
    OrderSide, OrderType, OrderStatus
)
from config.settings import RISK, COST_MODEL, INSTRUMENTS, MARKET

logger = logging.getLogger(__name__)


class PaperBroker(BrokerBase):
    """
    Paper trading broker.

    Fill logic:
    - MARKET orders: fill at current price + slippage
    - LIMIT orders:  fill when price crosses limit (checked on each tick)
    - SL orders:     trigger when price touches trigger_price

    All costs (brokerage, STT, exchange charges, stamp duty, GST)
    are deducted exactly as they would be in live trading.
    This is critical — most paper trading setups ignore costs and
    produce unrealistically good results.
    """

    def __init__(self, initial_capital: float = None, cost_model: str = "zerodha"):
        self.capital    = initial_capital or RISK["max_capital"]
        self.cost_key   = cost_model
        self.costs      = COST_MODEL[cost_model]
        self.slippage_t = COST_MODEL["slippage_ticks"]

        self._orders:    dict[str, Order]    = {}
        self._positions: dict[str, Position] = {}
        self._pnl_today: float = 0.0
        self._trade_log: list[dict]          = []

        self._ltp:       dict[str, float]    = {}    # symbol -> last price
        self._callbacks: list[Callable]      = []

        cur = "$" if MARKET == "US" else "₹"
        logger.info(f"PaperBroker initialised | capital={cur}{self.capital:,.0f} | costs={cost_model}")

    # ── Connection ────────────────────────────────────────────

    def connect(self) -> bool:
        logger.info("PaperBroker connected (no real connection needed)")
        return True

    def disconnect(self) -> None:
        logger.info("PaperBroker disconnected")

    # ── Account ───────────────────────────────────────────────

    def get_account_info(self) -> AccountInfo:
        used_margin = sum(
            p.avg_price * p.quantity * 0.10   # approximate 10% margin
            for p in self._positions.values()
        )
        return AccountInfo(
            cash_balance=self.capital,
            used_margin=used_margin,
            available_margin=self.capital - used_margin,
            total_pnl_today=self._pnl_today,
        )

    # ── Price feed ────────────────────────────────────────────

    def get_ltp(self, symbol: str, exchange: str = "") -> float:
        return self._ltp.get(symbol, 0.0)

    def update_price(self, symbol: str, price: float) -> None:
        """Called by data pipeline on each new tick / candle close."""
        self._ltp[symbol] = price
        self._check_pending_orders(symbol, price)

    # ── Orders ────────────────────────────────────────────────

    def place_order(self, order: Order) -> str:
        order.order_id = str(uuid.uuid4())[:8]
        order.timestamp = datetime.now()
        self._orders[order.order_id] = order

        if order.order_type == OrderType.MARKET:
            self._fill_order(order)
        else:
            order.status = OrderStatus.OPEN
            logger.info(f"[PAPER] Pending order {order.order_id} | {order.side.value} {order.symbol} @ {order.price}")

        return order.order_id

    def _fill_order(self, order: Order) -> None:
        ltp = self._ltp.get(order.symbol, order.price or 0)
        if ltp == 0:
            logger.warning(f"No price for {order.symbol}, cannot fill")
            order.status = OrderStatus.REJECTED
            return

        # Apply slippage — adverse for us (buy higher, sell lower)
        inst = INSTRUMENTS.get(order.symbol, {})
        tick = inst.get("tick_size", 0.01)
        slip = self.slippage_t * tick
        if order.side == OrderSide.BUY:
            fill_price = ltp + slip
        else:
            fill_price = ltp - slip

        order.fill_price = round(fill_price, 2)
        order.filled_qty = order.quantity
        order.status     = OrderStatus.COMPLETE

        # Deduct costs
        notional = fill_price * order.quantity
        cost = self._calc_cost(notional, order.side)
        self.capital -= cost

        # Update positions
        self._update_position(order)

        cur = "$" if MARKET == "US" else "₹"
        logger.info(
            f"[PAPER] FILLED {order.order_id} | {order.side.value} {order.quantity} "
            f"{order.symbol} @ {cur}{fill_price:.2f} | cost={cur}{cost:.2f}"
        )

        self._trade_log.append({
            "time":       order.timestamp,
            "symbol":     order.symbol,
            "side":       order.side.value,
            "qty":        order.quantity,
            "price":      fill_price,
            "cost":       cost,
            "tag":        order.tag,
        })

    def _check_pending_orders(self, symbol: str, price: float) -> None:
        for order in list(self._orders.values()):
            if order.symbol != symbol or order.status != OrderStatus.OPEN:
                continue

            triggered = False
            if order.order_type == OrderType.LIMIT:
                if order.side == OrderSide.BUY  and price <= order.price:
                    triggered = True
                if order.side == OrderSide.SELL and price >= order.price:
                    triggered = True
            elif order.order_type in (OrderType.SL, OrderType.SL_M):
                if order.side == OrderSide.BUY  and price >= order.trigger_price:
                    triggered = True
                if order.side == OrderSide.SELL and price <= order.trigger_price:
                    triggered = True

            if triggered:
                self._fill_order(order)

    def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order and order.status == OrderStatus.OPEN:
            order.status = OrderStatus.CANCELLED
            return True
        return False

    def get_order_status(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    # ── Positions ─────────────────────────────────────────────

    def _update_position(self, order: Order) -> None:
        sym = order.symbol
        if sym in self._positions:
            pos = self._positions[sym]
            if order.side == pos.side:
                # Add to position
                total_qty  = pos.quantity + order.quantity
                pos.avg_price = (
                    (pos.avg_price * pos.quantity + order.fill_price * order.quantity)
                    / total_qty
                )
                pos.quantity = total_qty
            else:
                # Reduce / close position
                pnl = (order.fill_price - pos.avg_price) * order.quantity
                if pos.side == OrderSide.SELL:
                    pnl = -pnl
                self._pnl_today += pnl
                self.capital    += pnl

                if order.quantity >= pos.quantity:
                    del self._positions[sym]
                    cur = "$" if MARKET == "US" else "₹"
                    logger.info(f"[PAPER] Position closed {sym} | pnl={cur}{pnl:.2f}")
                else:
                    pos.quantity -= order.quantity
        else:
            self._positions[sym] = Position(
                symbol=sym,
                side=order.side,
                quantity=order.quantity,
                avg_price=order.fill_price,
            )

    def get_positions(self) -> list[Position]:
        # Refresh PnL with current prices
        for sym, pos in self._positions.items():
            ltp = self._ltp.get(sym, pos.avg_price)
            pos.current_price = ltp
            diff = ltp - pos.avg_price
            pos.pnl = diff * pos.quantity if pos.side == OrderSide.BUY else -diff * pos.quantity
        return list(self._positions.values())

    # ── Cost calculation ──────────────────────────────────────

    def _calc_cost(self, notional: float, side: OrderSide) -> float:
        """
        Calculates total transaction cost for one order leg.
        Mirrors exact Zerodha F&O cost structure.
        """
        c = self.costs
        brokerage = c["brokerage_per_order"]

        stt = notional * c["stt_pct_sell"] if side == OrderSide.SELL else 0.0

        exchange_charge = notional * c["exchange_txn_charge_pct"]
        sebi_charge     = notional * c["sebi_charges_pct"]
        stamp_duty      = notional * c["stamp_duty_pct_buy"] if side == OrderSide.BUY else 0.0

        # GST applies on brokerage + exchange charges (not STT or stamp duty)
        gst = (brokerage + exchange_charge) * c["gst_pct"]

        total = brokerage + stt + exchange_charge + sebi_charge + stamp_duty + gst

        cur = "$" if MARKET == "US" else "₹"
        logger.debug(
            f"Cost breakdown: brok={brokerage:.2f} stt={stt:.4f} "
            f"exc={exchange_charge:.4f} sebi={sebi_charge:.6f} "
            f"stamp={stamp_duty:.4f} gst={gst:.4f} → total={cur}{total:.2f}"
        )
        return total

    # ── Reporting ─────────────────────────────────────────────

    def get_trade_log(self) -> pd.DataFrame:
        return pd.DataFrame(self._trade_log)

    def get_daily_summary(self) -> dict:
        return {
            "capital":       round(self.capital, 2),
            "pnl_today":     round(self._pnl_today, 2),
            "open_positions": len(self._positions),
            "total_trades":  len(self._trade_log),
        }

    # ── Unused in paper mode ──────────────────────────────────

    def get_historical_data(self, *args, **kwargs) -> pd.DataFrame:
        raise NotImplementedError("Use DataPipeline for historical data, not broker")

    def subscribe_ticker(self, symbols, callback):
        self._callbacks.append(callback)

    def unsubscribe_ticker(self, symbols):
        pass
