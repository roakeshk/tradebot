# ============================================================
#  tradebot / broker / base.py
#  Abstract broker interface — every broker implements this.
#  Strategy code never imports a specific broker directly.
# ============================================================

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class OrderSide(Enum):
    BUY  = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT  = "LIMIT"
    SL     = "SL"        # stop-loss market
    SL_M   = "SL_M"      # stop-loss limit


class OrderStatus(Enum):
    PENDING   = "PENDING"
    OPEN      = "OPEN"
    COMPLETE  = "COMPLETE"
    CANCELLED = "CANCELLED"
    REJECTED  = "REJECTED"


@dataclass
class Order:
    symbol:       str
    side:         OrderSide
    order_type:   OrderType
    quantity:     int                    # in lots × lot_size
    price:        Optional[float] = None # None for MARKET orders
    trigger_price: Optional[float] = None
    order_id:     Optional[str]  = None
    status:       OrderStatus = OrderStatus.PENDING
    fill_price:   Optional[float] = None
    filled_qty:   int = 0
    timestamp:    datetime = field(default_factory=datetime.now)
    tag:          str = ""               # strategy tag for tracking


@dataclass
class Position:
    symbol:        str
    side:          OrderSide
    quantity:      int
    avg_price:     float
    current_price: float = 0.0
    pnl:           float = 0.0
    open_time:     datetime = field(default_factory=datetime.now)


@dataclass
class AccountInfo:
    cash_balance:    float
    used_margin:     float
    available_margin: float
    total_pnl_today: float


class BrokerBase(ABC):
    """
    Every broker adapter inherits from this.
    Strategy code only calls these methods.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Authenticate and establish session. Returns True on success."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        ...

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        ...

    @abstractmethod
    def get_ltp(self, symbol: str, exchange: str) -> float:
        """Last traded price."""
        ...

    @abstractmethod
    def place_order(self, order: Order) -> str:
        """Place order. Returns broker order_id."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> Order:
        ...

    @abstractmethod
    def get_positions(self) -> list[Position]:
        ...

    @abstractmethod
    def get_historical_data(
        self,
        symbol:    str,
        exchange:  str,
        interval:  str,        # "1min" | "5min" | "15min" | "1hour" | "1day"
        from_date: datetime,
        to_date:   datetime,
    ) -> "pd.DataFrame":       # columns: open, high, low, close, volume
        ...

    @abstractmethod
    def subscribe_ticker(self, symbols: list[str], callback) -> None:
        """Subscribe to live tick feed. callback(tick_data) called on each tick."""
        ...

    @abstractmethod
    def unsubscribe_ticker(self, symbols: list[str]) -> None:
        ...
