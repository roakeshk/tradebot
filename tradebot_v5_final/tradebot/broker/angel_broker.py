# ============================================================
#  tradebot / broker / angel_broker.py
#  Angel One SmartAPI adapter — completely FREE.
#
#  Why Angel One for data:
#    - Free API (no subscription at all)
#    - Historical data: NSE, NFO futures, MCX — all free
#    - 30 days 1min / 100 days 5min per request
#    - Up to 8000 candles per call
#    - WebSocket V2 for live ticks
#    - Official Python SDK (smartapi-python)
#    - Covers MCX commodities well (Crude Oil, Gold)
#
#  Setup (one-time):
#    1. Open free account at angelone.in
#    2. Go to smartapi.angelone.in → Create App
#    3. Get API key
#    4. Enable TOTP in Angel One app (for 2FA)
#    5. pip install smartapi-python pyotp
#
#  No daily token script needed — TOTP auto-generates fresh token.
# ============================================================

import logging
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd

from broker.base import (
    BrokerBase, Order, Position, AccountInfo,
    OrderSide, OrderType, OrderStatus
)
from config.settings import ANGEL_ONE

logger = logging.getLogger(__name__)

# Angel One interval strings
INTERVAL_MAP = {
    "1min":  "ONE_MINUTE",
    "3min":  "THREE_MINUTE",
    "5min":  "FIVE_MINUTE",
    "15min": "FIFTEEN_MINUTE",
    "1hour": "ONE_HOUR",
    "1day":  "ONE_DAY",
}

# Angel One token IDs for key instruments (from scrip master)
# These are stable — update only when instrument changes
SYMBOL_TOKENS = {
    "BANKNIFTY_IDX": "99926009",   # BankNifty index (for data)
    "NIFTY_IDX":     "99926000",   # Nifty 50 index
    "CRUDEOIL":      "MCX token",  # fetch from scrip master
}

# Max days per interval for Angel One API
MAX_DAYS = {
    "1min": 30, "3min": 60, "5min": 100,
    "15min": 200, "1hour": 400, "1day": 2000,
}


class AngelBroker(BrokerBase):
    """
    Angel One SmartAPI broker adapter.
    Completely free — no monthly charges of any kind.
    Excellent for historical data including MCX.
    """

    def __init__(self):
        self._api = None
        self._auth_token  = None
        self._feed_token  = None
        self._refresh_token = None

    def connect(self) -> bool:
        try:
            from SmartApi import SmartConnect
            import pyotp
        except ImportError:
            logger.error("Install: pip install smartapi-python pyotp")
            return False
        try:
            self._api = SmartConnect(api_key=ANGEL_ONE["api_key"])
            totp = pyotp.TOTP(ANGEL_ONE["totp_secret"]).now()
            data = self._api.generateSession(
                ANGEL_ONE["client_id"],
                ANGEL_ONE["password"],
                totp,
            )
            if data["status"]:
                self._auth_token   = data["data"]["jwtToken"]
                self._refresh_token = data["data"]["refreshToken"]
                self._feed_token   = self._api.getfeedToken()
                logger.info(f"Angel One connected | client={ANGEL_ONE['client_id']}")
                return True
            logger.error(f"Angel login failed: {data}")
            return False
        except Exception as e:
            logger.error(f"Angel connect error: {e}")
            return False

    def disconnect(self) -> None:
        if self._api:
            try:
                self._api.terminateSession(ANGEL_ONE["client_id"])
            except Exception:
                pass
        logger.info("Angel One disconnected")

    # ── Account ───────────────────────────────────────────────

    def get_account_info(self) -> AccountInfo:
        data = self._api.rmsLimit()
        rms  = data.get("data", {})
        return AccountInfo(
            cash_balance=float(rms.get("availablecash", 0)),
            used_margin=float(rms.get("utiliseddebits", 0)),
            available_margin=float(rms.get("net", 0)),
            total_pnl_today=0,
        )

    def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        token = self._resolve_token(symbol, exchange)
        data  = self._api.ltpData(exchange, symbol, token)
        return float(data.get("data", {}).get("ltp", 0))

    # ── Orders ────────────────────────────────────────────────

    def place_order(self, order: Order) -> str:
        from SmartApi.smartConnect import SmartConnect
        side_map = {OrderSide.BUY: "BUY", OrderSide.SELL: "SELL"}
        type_map = {
            OrderType.MARKET: "MARKET",
            OrderType.LIMIT:  "LIMIT",
            OrderType.SL:     "STOPLOSS_LIMIT",
            OrderType.SL_M:   "STOPLOSS_MARKET",
        }
        token = self._resolve_token(order.symbol, "NFO")
        params = {
            "variety":         "NORMAL",
            "tradingsymbol":   order.symbol,
            "symboltoken":     token,
            "transactiontype": side_map[order.side],
            "exchange":        "NFO",
            "ordertype":       type_map.get(order.order_type, "MARKET"),
            "producttype":     "INTRADAY",
            "duration":        "DAY",
            "price":           str(order.price or 0),
            "triggerprice":    str(order.trigger_price or 0),
            "quantity":        str(order.quantity),
        }
        result = self._api.placeOrder(params)
        if result["status"]:
            order_id = result["data"]["orderid"]
            logger.info(f"Angel order placed: {order_id}")
            return order_id
        raise RuntimeError(f"Angel order failed: {result}")

    def cancel_order(self, order_id: str) -> bool:
        result = self._api.cancelOrder("NORMAL", order_id)
        return result.get("status", False)

    def get_order_status(self, order_id: str) -> Optional[Order]:
        orders = self._api.orderBook().get("data", []) or []
        for o in orders:
            if o["orderid"] == order_id:
                status_map = {
                    "complete": OrderStatus.COMPLETE,
                    "cancelled": OrderStatus.CANCELLED,
                    "rejected": OrderStatus.REJECTED,
                    "open": OrderStatus.OPEN,
                }
                return Order(
                    symbol=o["tradingsymbol"],
                    side=OrderSide.BUY if o["transactiontype"] == "BUY" else OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=int(o.get("quantity", 0)),
                    fill_price=float(o.get("averageprice", 0) or 0),
                    order_id=order_id,
                    status=status_map.get(o["status"].lower(), OrderStatus.PENDING),
                )
        return None

    def get_positions(self) -> list[Position]:
        data = self._api.position().get("data", []) or []
        positions = []
        for p in data:
            qty = int(p.get("netqty", 0))
            if qty == 0:
                continue
            positions.append(Position(
                symbol=p["tradingsymbol"],
                side=OrderSide.BUY if qty > 0 else OrderSide.SELL,
                quantity=abs(qty),
                avg_price=float(p.get("netprice", 0) or 0),
                current_price=float(p.get("ltp", 0) or 0),
                pnl=float(p.get("unrealised", 0) or 0),
            ))
        return positions

    # ── Historical data (FREE — best for MCX) ─────────────────

    def get_historical_data(
        self,
        symbol:    str,
        exchange:  str,
        interval:  str,
        from_date: datetime,
        to_date:   datetime,
    ) -> pd.DataFrame:
        """
        Free historical data from Angel One.
        Covers NSE equities, NFO futures, MCX commodities, BSE.
        Best source for MCX Gold and Crude Oil historical data.
        """
        token     = self._resolve_token(symbol, exchange)
        interval_a = INTERVAL_MAP.get(interval, "FIVE_MINUTE")
        max_days  = MAX_DAYS.get(interval, 100)

        all_frames = []
        current = from_date

        while current < to_date:
            chunk_end = min(current + timedelta(days=max_days), to_date)
            params = {
                "exchange":    exchange,
                "symboltoken": token,
                "interval":    interval_a,
                "fromdate":    current.strftime("%Y-%m-%d %H:%M"),
                "todate":      chunk_end.strftime("%Y-%m-%d %H:%M"),
            }
            try:
                result = self._api.getCandleData(params)
                candles = result.get("data", [])
                if candles:
                    df = pd.DataFrame(candles,
                                      columns=["timestamp", "open", "high", "low", "close", "volume"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                    all_frames.append(df)
            except Exception as e:
                logger.warning(f"Angel history chunk {current}→{chunk_end}: {e}")
            current = chunk_end

        if not all_frames:
            return pd.DataFrame()

        combined = pd.concat(all_frames).drop_duplicates("timestamp").sort_values("timestamp")
        combined = combined.set_index("timestamp")
        logger.info(f"Angel: fetched {len(combined)} bars for {symbol} {interval}")
        return combined

    # ── WebSocket ─────────────────────────────────────────────

    def subscribe_ticker(self, symbols: list[str], callback) -> None:
        try:
            from SmartApi.smartWebSocketV2 import SmartWebSocketV2
            tokens = [self._resolve_token(s, "NFO") for s in symbols]
            correlation = "tradebot_feed"
            self._ws = SmartWebSocketV2(
                self._auth_token, ANGEL_ONE["api_key"],
                ANGEL_ONE["client_id"], self._feed_token,
            )
            self._ws.on_open   = lambda ws: ws.subscribe(correlation, 1, [[1, t] for t in tokens])
            self._ws.on_data   = lambda ws, msg: callback(msg)
            self._ws.on_error  = lambda ws, e: logger.error(f"Angel WS error: {e}")
            self._ws.on_close  = lambda ws: logger.info("Angel WS closed")
            self._ws.connect()
        except Exception as e:
            logger.error(f"Angel WebSocket error: {e}")

    def unsubscribe_ticker(self, symbols: list[str]) -> None:
        pass

    # ── Token resolution ──────────────────────────────────────

    def _resolve_token(self, symbol: str, exchange: str) -> str:
        """
        Resolve instrument token from scrip master.
        Angel One requires token IDs, not symbol strings.
        Cache the scrip master locally for speed.
        """
        import requests, json
        from pathlib import Path

        cache_file = Path("data/cache/angel_scrip_master.json")
        if not cache_file.exists() or \
           (datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)).days > 1:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            data = requests.get(url, timeout=30).json()
            cache_file.write_text(json.dumps(data))
        else:
            import json
            data = json.loads(cache_file.read_text())

        # Search for matching symbol
        for inst in data:
            if inst.get("symbol", "").upper() == symbol.upper() and \
               inst.get("exch_seg", "").upper() == exchange.upper():
                return inst["token"]

        # Fallback to known index tokens
        fallback = {
            "BANKNIFTY": "99926009",
            "NIFTY":     "99926000",
        }
        return fallback.get(symbol, "")
