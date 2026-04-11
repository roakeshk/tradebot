# ============================================================
#  tradebot / broker / factory.py
#  Returns the right broker or data source from config.
#
#  Key concept — execution vs data are now separate:
#    get_broker()      → for placing orders (execution)
#    get_data_source() → for historical data + live ticks
#
#  Recommended combinations:
#
#  US market (paper/simulation):
#    ACTIVE_BROKER = "paper"
#    DATA_SOURCE   = "yfinance"   <- free, no account needed
#
#  India Phase 1-3 (paper trading):
#    ACTIVE_BROKER = "paper"
#    DATA_SOURCE   = "angel"   <- free, no subscription
#
#  India Phase 4 (live, Zerodha):
#    ACTIVE_BROKER = "zerodha" <- free execution (post Apr 2025)
#    DATA_SOURCE   = "angel"   <- still free
# ============================================================

from config.settings import ACTIVE_BROKER, DATA_SOURCE, MARKET
from broker.base import BrokerBase


def get_broker(override: str = None) -> BrokerBase:
    name = override or ACTIVE_BROKER
    if name == "paper":
        from broker.paper_broker import PaperBroker
        cost_model = "us_paper" if MARKET == "US" else "zerodha"
        return PaperBroker(cost_model=cost_model)
    if name == "zerodha":
        from broker.zerodha_broker import ZerodhaBroker
        return ZerodhaBroker()
    if name == "shoonya":
        from broker.shoonya_broker import ShoonyaBroker
        return ShoonyaBroker()
    if name == "fyers":
        from broker.fyers_broker import FyersBroker
        return FyersBroker()
    if name == "angel":
        from broker.angel_broker import AngelBroker
        return AngelBroker()
    raise ValueError(f"Unknown broker: '{name}'. Choose: paper|zerodha|shoonya|fyers|angel")


def get_data_source(override: str = None) -> BrokerBase:
    """
    Returns broker instance for DATA only (historical + live ticks).
    Returns None for yfinance — DataPipeline handles that directly.
    """
    name = override or DATA_SOURCE
    if name in ("yfinance", "yf", None, ""):
        return None   # DataPipeline falls back to yfinance
    if name == "fyers":
        from broker.fyers_broker import FyersBroker
        return FyersBroker()
    if name == "angel":
        from broker.angel_broker import AngelBroker
        return AngelBroker()
    if name == "zerodha":
        from broker.zerodha_broker import ZerodhaBroker
        return ZerodhaBroker()
    if name == "shoonya":
        from broker.shoonya_broker import ShoonyaBroker
        return ShoonyaBroker()
    return None   # DataPipeline falls back to yfinance
