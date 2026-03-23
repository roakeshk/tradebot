# ============================================================
#  tradebot / broker / zerodha_broker.py
#  Zerodha Kite Connect adapter.
#  Implements BrokerBase using the official kiteconnect SDK.
#
#  Setup:
#    1. pip install kiteconnect
#    2. Fill ZERODHA config in settings.py
#    3. Run generate_token.py once per day to get access_token
# ============================================================

import logging
from datetime import datetime
from typing import Optional
import pandas as pd

from broker.base import (
    BrokerBase, Order, Position, AccountInfo,
    OrderSide, OrderType, OrderStatus
)
from config.settings import ZERODHA

logger = logging.getLogger(__name__)

INTERVAL_MAP = {
    "1min": "minute", "3min": "3minute", "5min": "5minute",
    "15min": "15minute", "1hour": "60minute", "1day": "day",
}

EXCHANGE_MAP = {
    "NSE": "NSE", "NFO": "NFO", "MCX": "MCX",
}


class ZerodhaBroker(BrokerBase):
    """
    Zerodha Kite Connect broker adapter.
    Requires kiteconnect package and valid access_token.
    """

    def __init__(self, access_token: str = None):
        self.access_token = access_token
        self._kite = None

    def connect(self) -> bool:
        try:
            from kiteconnect import KiteConnect
            self._kite = KiteConnect(api_key=ZERODHA["api_key"])
            token = self.access_token or self._load_token()
            self._kite.set_access_token(token)
            profile = self._kite.profile()
            logger.info(f"Zerodha connected | user={profile['user_name']}")
            return True
        except ImportError:
            logger.error("kiteconnect not installed. Run: pip install kiteconnect")
            return False
        except Exception as e:
            logger.error(f"Zerodha connection failed: {e}")
            return False

    def disconnect(self) -> None:
        if self._kite:
            try:
                self._kite.invalidate_access_token()
            except Exception:
                pass
        logger.info("Zerodha disconnected")

    def _load_token(self) -> str:
        from pathlib import Path
        token_file = Path(__file__).parent.parent / ".zerodha_token"
        if token_file.exists():
            return token_file.read_text().strip()
        raise FileNotFoundError("No access token. Run generate_token.py first.")

    def get_account_info(self) -> AccountInfo:
        margins = self._kite.margins()
        equity  = margins.get("equity", {})
        return AccountInfo(
            cash_balance=equity.get("available", {}).get("cash", 0),
            used_margin=equity.get("utilised", {}).get("debits", 0),
            available_margin=equity.get("available", {}).get("live_balance", 0),
            total_pnl_today=0.0,
        )

    def get_ltp(self, symbol: str, exchange: str = "NFO") -> float:
        key = f"{exchange}:{symbol}"
        data = self._kite.ltp([key])
        return data.get(key, {}).get("last_price", 0.0)

    def place_order(self, order: Order) -> str:
        try:
            side_map = {OrderSide.BUY: self._kite.TRANSACTION_TYPE_BUY,
                        OrderSide.SELL: self._kite.TRANSACTION_TYPE_SELL}
            type_map = {
                OrderType.MARKET: self._kite.ORDER_TYPE_MARKET,
                OrderType.LIMIT:  self._kite.ORDER_TYPE_LIMIT,
                OrderType.SL:     self._kite.ORDER_TYPE_SL,
                OrderType.SL_M:   self._kite.ORDER_TYPE_SLM,
            }
            order_id = self._kite.place_order(
                variety=self._kite.VARIETY_REGULAR,
                exchange=self._kite.EXCHANGE_NFO,
                tradingsymbol=order.symbol,
                transaction_type=side_map[order.side],
                quantity=order.quantity,
                product=self._kite.PRODUCT_MIS,
                order_type=type_map[order.order_type],
                price=order.price,
                trigger_price=order.trigger_price,
                tag=order.tag[:20] if order.tag else None,
            )
            logger.info(f"Order placed: {order_id} | {order.side.value} {order.symbol}")
            order.order_id = str(order_id)
            return str(order_id)
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            order.status = OrderStatus.REJECTED
            raise

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._kite.cancel_order(variety=self._kite.VARIETY_REGULAR, order_id=order_id)
            return True
        except Exception as e:
            logger.error(f"Cancel failed: {e}")
            return False

    def get_order_status(self, order_id: str) -> Optional[Order]:
        orders = self._kite.orders()
        for o in orders:
            if str(o["order_id"]) == str(order_id):
                status_map = {
                    "COMPLETE": OrderStatus.COMPLETE,
                    "CANCELLED": OrderStatus.CANCELLED,
                    "REJECTED": OrderStatus.REJECTED,
                    "OPEN": OrderStatus.OPEN,
                }
                return Order(
                    symbol=o["tradingsymbol"],
                    side=OrderSide.BUY if o["transaction_type"] == "BUY" else OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=o["quantity"],
                    fill_price=o.get("average_price"),
                    filled_qty=o.get("filled_quantity", 0),
                    order_id=order_id,
                    status=status_map.get(o["status"], OrderStatus.PENDING),
                )
        return None

    def get_positions(self) -> list[Position]:
        positions = []
        data = self._kite.positions().get("day", [])
        for p in data:
            if p["quantity"] == 0:
                continue
            positions.append(Position(
                symbol=p["tradingsymbol"],
                side=OrderSide.BUY if p["quantity"] > 0 else OrderSide.SELL,
                quantity=abs(p["quantity"]),
                avg_price=p["average_price"],
                current_price=p.get("last_price", p["average_price"]),
                pnl=p.get("pnl", 0),
            ))
        return positions

    def get_historical_data(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        from_date: datetime,
        to_date: datetime,
    ) -> pd.DataFrame:
        interval_kite = INTERVAL_MAP.get(interval, "5minute")
        instruments = self._kite.instruments(exchange)
        inst = next((i for i in instruments if i["tradingsymbol"] == symbol), None)
        if not inst:
            raise ValueError(f"Instrument {symbol} not found on {exchange}")

        data = self._kite.historical_data(
            instrument_token=inst["instrument_token"],
            from_date=from_date,
            to_date=to_date,
            interval=interval_kite,
        )
        df = pd.DataFrame(data)
        if df.empty:
            return df
        df = df.rename(columns={"date": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df.set_index("timestamp")

    def subscribe_ticker(self, symbols: list[str], callback) -> None:
        from kiteconnect import KiteTicker
        self._ticker = KiteTicker(ZERODHA["api_key"], self.access_token)

        def on_ticks(ws, ticks):
            for tick in ticks:
                callback(tick)

        self._ticker.on_ticks = on_ticks
        self._ticker.on_connect = lambda ws, r: logger.info("Ticker connected")
        self._ticker.connect(threaded=True)

    def unsubscribe_ticker(self, symbols: list[str]) -> None:
        if hasattr(self, "_ticker"):
            self._ticker.close()
