# ============================================================
#  tradebot / broker / fyers_broker.py
#  Fyers API adapter — completely FREE (no monthly charges).
#
#  Why Fyers for data:
#    - Free API (no subscription)
#    - 100 days of 1-min historical data free
#    - WebSocket V3 for live ticks
#    - Official Python SDK (fyers-apiv3)
#    - Good community and documentation
#
#  Setup (one-time, 5 minutes):
#    1. Open free account at fyers.in
#    2. Go to myapi.fyers.in → Create App
#    3. Get App ID and Secret
#    4. Fill FYERS config in settings.py
#    5. pip install fyers-apiv3
#
#  Daily auth:
#    Run python generate_token_fyers.py once each morning.
#    Token is saved to .fyers_token and loaded automatically.
# ============================================================

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import pandas as pd

from broker.base import (
    BrokerBase, Order, Position, AccountInfo,
    OrderSide, OrderType, OrderStatus
)
from config.settings import FYERS

logger = logging.getLogger(__name__)

# Fyers interval strings
INTERVAL_MAP = {
    "1min": "1",   "3min": "3",   "5min": "5",
    "15min": "15", "1hour": "60", "1day": "D",
}

# Fyers symbol format: "NSE:NIFTYBANK-INDEX" or "NSE:BANKNIFTY25JANFUT"
SYMBOL_MAP = {
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",   # index (free, no futures token needed for data)
    "NIFTY":     "NSE:NIFTY50-INDEX",
    "CRUDEOIL":  "MCX:CRUDEOIL25JANFUT",  # update expiry monthly
}


class FyersBroker(BrokerBase):
    """
    Fyers API broker adapter.
    Completely free — no monthly API subscription.
    Use this for: historical data, live ticks, order execution.
    """

    def __init__(self, access_token: str = None):
        self.access_token = access_token
        self._fyers = None
        self._client_id = FYERS.get("client_id", "")

    def connect(self) -> bool:
        try:
            from fyers_apiv3 import fyersModel
        except ImportError:
            logger.error("fyers-apiv3 not installed. Run: pip install fyers-apiv3")
            return False
        try:
            token = self.access_token or self._load_token()
            self._fyers = fyersModel.FyersModel(
                client_id=self._client_id,
                token=token,
                log_path="",
                is_async=False,
            )
            profile = self._fyers.get_profile()
            if profile.get("code") == 200:
                name = profile["data"]["name"]
                logger.info(f"Fyers connected | user={name}")
                return True
            logger.error(f"Fyers auth failed: {profile}")
            return False
        except Exception as e:
            logger.error(f"Fyers connect error: {e}")
            return False

    def disconnect(self) -> None:
        logger.info("Fyers disconnected")

    def _load_token(self) -> str:
        token_file = Path(__file__).parent.parent / ".fyers_token"
        if token_file.exists():
            return token_file.read_text().strip()
        raise FileNotFoundError("No Fyers token. Run generate_token_fyers.py first.")

    # ── Account ───────────────────────────────────────────────

    def get_account_info(self) -> AccountInfo:
        data = self._fyers.funds()
        fund_limit = data.get("fund_limit", [])
        equity = {f["title"]: f["equityAmount"] for f in fund_limit}
        available = equity.get("Available Balance", 0)
        used      = equity.get("Utilized Amount", 0)
        return AccountInfo(
            cash_balance=available + used,
            used_margin=used,
            available_margin=available,
            total_pnl_today=0,
        )

    def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        fyers_sym = SYMBOL_MAP.get(symbol, f"{exchange}:{symbol}")
        data = self._fyers.quotes({"symbols": fyers_sym})
        quotes = data.get("d", [])
        if quotes:
            return quotes[0].get("v", {}).get("lp", 0.0)
        return 0.0

    # ── Orders ────────────────────────────────────────────────

    def place_order(self, order: Order) -> str:
        side_map = {OrderSide.BUY: 1, OrderSide.SELL: -1}
        type_map = {
            OrderType.MARKET: 2,
            OrderType.LIMIT:  1,
            OrderType.SL:     3,
            OrderType.SL_M:   4,
        }
        fyers_sym = SYMBOL_MAP.get(order.symbol, f"NSE:{order.symbol}")
        data = {
            "symbol":        fyers_sym,
            "qty":           order.quantity,
            "type":          type_map.get(order.order_type, 2),
            "side":          side_map[order.side],
            "productType":   "INTRADAY",
            "limitPrice":    order.price or 0,
            "stopPrice":     order.trigger_price or 0,
            "validity":      "DAY",
            "disclosedQty":  0,
            "offlineOrder":  False,
        }
        result = self._fyers.place_order(data=data)
        if result.get("code") == 200:
            order_id = result["id"]
            logger.info(f"Fyers order placed: {order_id}")
            return order_id
        raise RuntimeError(f"Fyers order failed: {result}")

    def cancel_order(self, order_id: str) -> bool:
        result = self._fyers.cancel_order(data={"id": order_id})
        return result.get("code") == 200

    def get_order_status(self, order_id: str) -> Optional[Order]:
        result = self._fyers.orderbook()
        for o in result.get("orderBook", []):
            if o["id"] == order_id:
                status_map = {1: OrderStatus.OPEN, 2: OrderStatus.COMPLETE,
                              3: OrderStatus.REJECTED, 5: OrderStatus.CANCELLED}
                return Order(
                    symbol=o["symbol"],
                    side=OrderSide.BUY if o["side"] == 1 else OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=o["qty"],
                    fill_price=o.get("tradedPrice"),
                    order_id=order_id,
                    status=status_map.get(o["status"], OrderStatus.PENDING),
                )
        return None

    def get_positions(self) -> list[Position]:
        result = self._fyers.positions()
        positions = []
        for p in result.get("netPositions", []):
            if p["netQty"] == 0:
                continue
            positions.append(Position(
                symbol=p["symbol"].split(":")[-1],
                side=OrderSide.BUY if p["netQty"] > 0 else OrderSide.SELL,
                quantity=abs(p["netQty"]),
                avg_price=p["netAvg"],
                current_price=p.get("ltp", p["netAvg"]),
                pnl=p.get("pl", 0),
            ))
        return positions

    # ── Historical data (FREE) ────────────────────────────────

    def get_historical_data(
        self,
        symbol:    str,
        exchange:  str,
        interval:  str,
        from_date: datetime,
        to_date:   datetime,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV history from Fyers — completely free.
        Limits: 1min → 100 days, 5min → 200 days, 1day → unlimited.
        For longer history, chunk requests automatically.
        """
        fyers_sym      = SYMBOL_MAP.get(symbol, f"{exchange}:{symbol}")
        fyers_interval = INTERVAL_MAP.get(interval, "5")

        all_frames = []
        chunk_days = {"1min": 90, "3min": 90, "5min": 180, "15min": 180,
                      "1hour": 365, "1day": 1000}
        days_per_chunk = chunk_days.get(interval, 100)

        current = from_date
        while current < to_date:
            chunk_end = min(current + timedelta(days=days_per_chunk), to_date)
            data = {
                "symbol":      fyers_sym,
                "resolution":  fyers_interval,
                "date_format": "1",
                "range_from":  current.strftime("%Y-%m-%d"),
                "range_to":    chunk_end.strftime("%Y-%m-%d"),
                "cont_flag":   "1",
            }
            try:
                result = self._fyers.history(data=data)
                candles = result.get("candles", [])
                if candles:
                    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
                    df["timestamp"] = df["timestamp"].dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
                    all_frames.append(df)
            except Exception as e:
                logger.warning(f"Fyers history chunk {current}→{chunk_end} error: {e}")
            current = chunk_end

        if not all_frames:
            return pd.DataFrame()

        combined = pd.concat(all_frames).drop_duplicates("timestamp").sort_values("timestamp")
        combined = combined.set_index("timestamp")
        logger.info(f"Fyers: fetched {len(combined)} bars for {symbol} {interval}")
        return combined

    # ── WebSocket live feed ────────────────────────────────────

    def subscribe_ticker(self, symbols: list[str], callback) -> None:
        try:
            from fyers_apiv3 import fyersModel
            from fyers_apiv3.FyersWebsocket import data_ws

            fyers_symbols = [SYMBOL_MAP.get(s, f"NSE:{s}") for s in symbols]
            token = self.access_token or self._load_token()

            def on_message(msg):
                callback(msg)

            self._ws = data_ws.FyersDataSocket(
                access_token=f"{self._client_id}:{token}",
                log_path="",
                litemode=False,
                write_to_file=False,
                reconnect=True,
                on_connect=lambda ws: ws.subscribe(symbols=fyers_symbols, data_type="SymbolUpdate"),
                on_close=lambda ws: logger.info("Fyers WS closed"),
                on_message=on_message,
                on_error=lambda ws, e: logger.error(f"Fyers WS error: {e}"),
            )
            self._ws.connect()
            logger.info(f"Fyers WebSocket subscribed: {fyers_symbols}")
        except Exception as e:
            logger.error(f"Fyers WebSocket error: {e}")

    def unsubscribe_ticker(self, symbols: list[str]) -> None:
        if hasattr(self, "_ws"):
            self._ws.close_connection()
