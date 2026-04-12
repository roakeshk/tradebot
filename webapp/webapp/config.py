from __future__ import annotations

import os
from pathlib import Path
from typing import Any


MARKET = os.environ.get("MARKET", "US")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://tradebot-production-c63c.up.railway.app")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp"))
DB_PATH = DATA_DIR / "tradebot_web.db"

RISK_CAPITAL = float(os.environ.get("RISK_CAPITAL", "100000"))
MAX_DAILY_LOSS_PCT = float(os.environ.get("MAX_DAILY_LOSS_PCT", "3.0"))
MAX_DD_PCT = float(os.environ.get("MAX_DD_PCT", "10.0"))
MAX_DD = float(os.environ.get("MAX_DD", str(RISK_CAPITAL * MAX_DD_PCT / 100)))
MAX_POSITIONS = int(os.environ.get("MAX_POSITIONS", "3"))
MAX_TRADES_DAY = int(os.environ.get("MAX_TRADES_DAY", "15"))
MIN_RR = float(os.environ.get("MIN_RR", "1.5"))
DEFAULT_SIGNAL_THRESHOLD = float(os.environ.get("AUTO_SIGNAL_THRESHOLD", "68"))


def _asset_kind(symbol: str) -> str:
    if "/" in symbol:
        return "forex"
    if symbol in {"BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BNBUSD", "ADAUSD", "DOGEUSD", "AVAXUSD"}:
        return "crypto"
    return "equity"


WORLD_MARKETS: dict[str, dict[str, Any]] = {
    "US": {
        "name": "NYSE / NASDAQ",
        "flag": "US",
        "index": "S&P 500",
        "currency": "$",
        "locale": "en-US",
        "tz": "America/New_York",
        "tz_label": "ET",
        "type": "equity",
        "open": (9, 30),
        "close": (16, 0),
        "lunch": None,
        "instruments": [
            {"s": "SPY", "p": 520.0},
            {"s": "QQQ", "p": 440.0},
            {"s": "AAPL", "p": 190.0},
            {"s": "MSFT", "p": 420.0},
            {"s": "NVDA", "p": 130.0},
            {"s": "AMZN", "p": 190.0},
            {"s": "META", "p": 500.0},
            {"s": "JPM", "p": 200.0},
        ],
    },
    "UK": {
        "name": "London SE",
        "flag": "UK",
        "index": "FTSE 100",
        "currency": "£",
        "locale": "en-GB",
        "tz": "Europe/London",
        "tz_label": "GMT",
        "type": "equity",
        "open": (8, 0),
        "close": (16, 30),
        "lunch": None,
        "instruments": [
            {"s": "SHEL", "p": 27.0},
            {"s": "AZN", "p": 115.0},
            {"s": "HSBA", "p": 6.4},
            {"s": "BP", "p": 4.8},
            {"s": "RIO", "p": 52.0},
            {"s": "ULVR", "p": 42.0},
        ],
    },
    "INDIA": {
        "name": "NSE India",
        "flag": "IN",
        "index": "NIFTY 50",
        "currency": "₹",
        "locale": "en-IN",
        "tz": "Asia/Kolkata",
        "tz_label": "IST",
        "type": "equity",
        "open": (9, 15),
        "close": (15, 30),
        "lunch": None,
        "instruments": [
            {"s": "RELIANCE", "p": 2500.0},
            {"s": "TCS", "p": 3800.0},
            {"s": "INFY", "p": 1500.0},
            {"s": "HDFCBANK", "p": 1600.0},
            {"s": "ITC", "p": 440.0},
            {"s": "SBIN", "p": 780.0},
        ],
    },
    "JAPAN": {
        "name": "Tokyo SE",
        "flag": "JP",
        "index": "Nikkei 225",
        "currency": "¥",
        "locale": "ja-JP",
        "tz": "Asia/Tokyo",
        "tz_label": "JST",
        "type": "equity",
        "open": (9, 0),
        "close": (15, 0),
        "lunch": ((11, 30), (12, 30)),
        "instruments": [
            {"s": "7203.T", "p": 2800.0},
            {"s": "6758.T", "p": 13000.0},
            {"s": "9984.T", "p": 8500.0},
            {"s": "6861.T", "p": 52000.0},
            {"s": "8306.T", "p": 1400.0},
        ],
    },
    "CHINA": {
        "name": "Shanghai SE",
        "flag": "CN",
        "index": "SSE Composite",
        "currency": "¥",
        "locale": "zh-CN",
        "tz": "Asia/Shanghai",
        "tz_label": "CST",
        "type": "equity",
        "open": (9, 30),
        "close": (15, 0),
        "lunch": ((11, 30), (13, 0)),
        "instruments": [
            {"s": "600519", "p": 1700.0},
            {"s": "601318", "p": 48.0},
            {"s": "600036", "p": 35.0},
            {"s": "601888", "p": 70.0},
            {"s": "600276", "p": 25.0},
        ],
    },
    "AUSTRALIA": {
        "name": "ASX",
        "flag": "AU",
        "index": "ASX 200",
        "currency": "A$",
        "locale": "en-AU",
        "tz": "Australia/Sydney",
        "tz_label": "AEST",
        "type": "equity",
        "open": (10, 0),
        "close": (16, 0),
        "lunch": None,
        "instruments": [
            {"s": "BHP", "p": 46.0},
            {"s": "CBA", "p": 120.0},
            {"s": "CSL", "p": 280.0},
            {"s": "WBC", "p": 26.0},
            {"s": "NAB", "p": 35.0},
        ],
    },
    "FOREX": {
        "name": "Global FX",
        "flag": "FX",
        "index": "Dollar Basket",
        "currency": "$",
        "locale": "en-US",
        "tz": "America/New_York",
        "tz_label": "ET",
        "type": "forex",
        "schedule": "24x5",
        "open": (0, 0),
        "close": (23, 59),
        "lunch": None,
        "instruments": [
            {"s": "EUR/USD", "p": 1.082},
            {"s": "GBP/USD", "p": 1.264},
            {"s": "USD/JPY", "p": 151.8},
            {"s": "AUD/USD", "p": 0.662},
            {"s": "USD/CHF", "p": 0.907},
            {"s": "USD/CAD", "p": 1.357},
            {"s": "NZD/USD", "p": 0.603},
            {"s": "EUR/GBP", "p": 0.856},
        ],
    },
    "CRYPTO": {
        "name": "Crypto Spot",
        "flag": "CR",
        "index": "Digital Assets",
        "currency": "$",
        "locale": "en-US",
        "tz": "UTC",
        "tz_label": "UTC",
        "type": "crypto",
        "schedule": "24x7",
        "open": (0, 0),
        "close": (23, 59),
        "lunch": None,
        "instruments": [
            {"s": "BTCUSD", "p": 81200.0},
            {"s": "ETHUSD", "p": 3950.0},
            {"s": "SOLUSD", "p": 182.0},
            {"s": "XRPUSD", "p": 0.69},
            {"s": "BNBUSD", "p": 612.0},
            {"s": "ADAUSD", "p": 0.71},
            {"s": "DOGEUSD", "p": 0.19},
            {"s": "AVAXUSD", "p": 41.0},
        ],
    },
}


SYMBOL_MARKET_MAP: dict[str, str] = {
    instrument["s"]: market_id
    for market_id, market in WORLD_MARKETS.items()
    for instrument in market["instruments"]
}


def _fundamentals_for(symbol: str, market_id: str) -> dict[str, Any]:
    market = WORLD_MARKETS[market_id]
    kind = _asset_kind(symbol)
    base = {
        "symbol": symbol,
        "market": market_id,
        "asset_kind": kind,
        "sector": "Macro" if kind != "equity" else "Large Cap",
        "pe": None if kind != "equity" else round(14 + (sum(ord(c) for c in symbol) % 18), 1),
        "pb": None if kind != "equity" else round(1.2 + (len(symbol) % 5) * 0.6, 1),
        "dividend_yield": None if kind != "equity" else round((len(symbol) % 4) * 0.6, 2),
        "roe": round(11 + (len(symbol) % 9) * 2.1, 1),
        "profit_margin": round(8 + (len(symbol) % 7) * 2.5, 1),
        "revenue_growth": round(5 + (sum(ord(c) for c in symbol[:3]) % 17), 1),
        "earnings_growth": round(6 + (sum(ord(c) for c in symbol[-3:]) % 14), 1),
        "debt_to_equity": round(0.25 + (len(symbol) % 6) * 0.18, 2),
        "current_ratio": round(1.1 + (len(symbol) % 5) * 0.25, 2),
        "market_cap_label": "Mega" if len(symbol) <= 5 else "Large",
    }
    if kind == "forex":
        base.update({
            "sector": "Rates / Macro",
            "pe": None,
            "pb": None,
            "dividend_yield": None,
            "roe": 0.0,
            "profit_margin": 0.0,
            "revenue_growth": 0.0,
            "earnings_growth": 0.0,
            "debt_to_equity": 0.0,
            "current_ratio": 0.0,
            "macro_drivers": ["rate differentials", "CPI prints", "central bank tone"],
        })
    if kind == "crypto":
        base.update({
            "sector": "Digital Assets",
            "pe": None,
            "pb": None,
            "dividend_yield": 0.0,
            "roe": 0.0,
            "profit_margin": 0.0,
            "revenue_growth": round(12 + (len(symbol) % 5) * 6, 1),
            "earnings_growth": 0.0,
            "debt_to_equity": 0.0,
            "current_ratio": 0.0,
            "macro_drivers": ["ETF flows", "on-chain activity", "risk appetite"],
        })
    return base


INSTRUMENT_FUNDAMENTALS: dict[str, dict[str, Any]] = {
    symbol: _fundamentals_for(symbol, market_id)
    for symbol, market_id in SYMBOL_MARKET_MAP.items()
}


def default_market_meta(market_id: str = MARKET) -> tuple[str, str, str, str]:
    market = WORLD_MARKETS.get(market_id, WORLD_MARKETS["US"])
    return market["currency"], market["locale"], market["tz"], market["tz_label"]