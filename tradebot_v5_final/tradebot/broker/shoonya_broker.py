# ============================================================
#  tradebot / broker / shoonya_broker.py
#  Shoonya / Finvasia broker adapter.
#  Zero brokerage on F&O — use this when going live at scale.
#
#  Setup:
#    pip install NorenRestApiPy
#    Fill SHOONYA config in settings.py
# ============================================================

import logging
import time
from datetime import datetime
from typing import Optional
import pandas as pd

from broker.base import (
    BrokerBase, Order, Position, AccountInfo,
    OrderSide, OrderType, OrderStatus
)
from config.settings import SHOONYA

logger = logging.getLogger(__name__)

INTERVAL_MAP = {
    "1min": "1",  "3min": "3",  "5min": "5",
    "15min": "15", "1hour": "60", "1day": "D",
}


class ShoonyaBroker(BrokerBase):
    """
    Shoonya / Finvasia broker adapter.
    Zero brokerage on F&O — switch from Zerodha when profitable.
    """

    def __init__(self):
        self._api = None

    def connect(self) -> bool:
        try:
            import pyotp
            from NorenRestApiPy.NorenApi import NorenApi
            class ShoonyaApi(NorenApi):
                def __init__(self):
                    super().__init__(
                        host="https://api.shoonya.com/NorenWClientTP/",
                        websocket="wss://api.shoonya.com/NorenWSTP/"
                    )
            self._api = ShoonyaApi()
            totp = pyotp.TOTP(SHOONYA.get("totp_secret", "")).now() if SHOONYA.get("totp_secret") else ""
            ret = self._api.login(
                userid=SHOONYA["user_id"],
                password=SHOONYA["password"],
                twoFA=totp,
                vendor_code=SHOONYA["vendor_code"],
                api_secret=SHOONYA["api_secret"],
                imei=SHOONYA["imei"],
            )
            if ret and ret.get("stat") == "Ok":
                logger.info(f"Shoonya connected | user={SHOONYA['user_id']}")
                return True
            logger.error(f"Shoonya login failed: {ret}")
            return False
        except ImportError:
            logger.error("NorenRestApiPy not installed. Run: pip install NorenRestApiPy pyotp")
            return False
        except Exception as e:
            logger.error(f"Shoonya connection error: {e}")
            return False

    def disconnect(self) -> None:
        logger.info("Shoonya session ended")

    def get_account_info(self) -> AccountInfo:
        limits = self._api.get_limits()
        cash = float(limits.get("cash", 0)) if limits else 0
        return AccountInfo(
            cash_balance=cash,
            used_margin=0,
            available_margin=cash,
            total_pnl_today=0,
        )

    def get_ltp(self, symbol: str, exchange: str = "NFO") -> float:
        quote = self._api.get_quotes(exchange=exchange, token=symbol)
        return float(quote.get("lp", 0)) if quote else 0.0

    def place_order(self, order: Order) -> str:
        buy_or_sell = "B" if order.side == OrderSide.BUY else "S"
        pricetype   = "MKT" if order.order_type == OrderType.MARKET else "LMT"
        ret = self._api.place_order(
            buy_or_sell=buy_or_sell,
            product_type="I",      # Intraday
            exchange="NFO",
            tradingsymbol=order.symbol,
            quantity=order.quantity,
            discloseqty=0,
            price_type=pricetype,
            price=order.price or 0,
            trigger_price=order.trigger_price,
            retention="DAY",
            remarks=order.tag[:30] if order.tag else "",
        )
        if ret and ret.get("stat") == "Ok":
            order_id = ret["norenordno"]
            logger.info(f"Shoonya order placed: {order_id}")
            return order_id
        raise RuntimeError(f"Shoonya order failed: {ret}")

    def cancel_order(self, order_id: str) -> bool:
        ret = self._api.cancel_order(orderno=order_id)
        return ret and ret.get("stat") == "Ok"

    def get_order_status(self, order_id: str) -> Optional[Order]:
        orders = self._api.get_order_book() or []
        for o in orders:
            if o.get("norenordno") == order_id:
                return Order(
                    symbol=o["tsym"],
                    side=OrderSide.BUY if o["trantype"] == "B" else OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=int(o.get("qty", 0)),
                    fill_price=float(o.get("avgprc", 0) or 0),
                    order_id=order_id,
                    status=OrderStatus.COMPLETE if o["status"] == "COMPLETE" else OrderStatus.OPEN,
                )
        return None

    def get_positions(self) -> list[Position]:
        positions = []
        data = self._api.get_positions() or []
        for p in data:
            qty = int(p.get("netqty", 0))
            if qty == 0:
                continue
            positions.append(Position(
                symbol=p["tsym"],
                side=OrderSide.BUY if qty > 0 else OrderSide.SELL,
                quantity=abs(qty),
                avg_price=float(p.get("netavgprc", 0)),
                current_price=float(p.get("lp", 0)),
                pnl=float(p.get("rpnl", 0)),
            ))
        return positions

    def get_historical_data(self, symbol, exchange, interval, from_date, to_date) -> pd.DataFrame:
        interval_s = INTERVAL_MAP.get(interval, "5")
        ret = self._api.get_time_price_series(
            exchange=exchange,
            token=symbol,
            starttime=int(from_date.timestamp()),
            endtime=int(to_date.timestamp()),
            interval=interval_s,
        )
        if not ret:
            return pd.DataFrame()
        df = pd.DataFrame(ret)
        df["timestamp"] = pd.to_datetime(df["time"])
        for col in ["into", "inth", "intl", "intc", "intv"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.rename(columns={"into":"open","inth":"high","intl":"low","intc":"close","intv":"volume"})
        return df[["timestamp","open","high","low","close","volume"]].set_index("timestamp")

    def subscribe_ticker(self, symbols: list[str], callback) -> None:
        def on_message(msg):
            if msg.get("t") == "tf":
                callback(msg)
        self._api.start_websocket(
            subscribe_callback=on_message,
            order_update_callback=lambda msg: None,
            socket_open_callback=lambda: logger.info("Shoonya WS open"),
        )

    def unsubscribe_ticker(self, symbols: list[str]) -> None:
        pass
