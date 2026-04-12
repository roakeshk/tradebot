from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .analytics import InstrumentAnalyzer, PriceBuffer
from .config import (
    DEFAULT_SIGNAL_THRESHOLD,
    MARKET,
    MAX_DAILY_LOSS_PCT,
    MAX_DD_PCT,
    MAX_DD,
    MAX_POSITIONS,
    MAX_TRADES_DAY,
    MIN_RR,
    RISK_CAPITAL,
    SYMBOL_MARKET_MAP,
    WEBAPP_URL,
    WORLD_MARKETS,
)
from .database import Database
from .markets import all_market_snapshots
from .simulator import SimulatorManager


class DashboardService:
    def __init__(self, database: Database, buffer: PriceBuffer, analyzer: InstrumentAnalyzer) -> None:
        self.database = database
        self.buffer = buffer
        self.analyzer = analyzer
        self.simulator = SimulatorManager(database, buffer, analyzer)
        self._analysis_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._seed_all()

    def _seed_all(self) -> None:
        for market_id, market in WORLD_MARKETS.items():
            for instrument in market["instruments"]:
                symbol = instrument["s"]
                self.buffer.seed_symbol(symbol, market_id, instrument["p"])

    def auth_ok(self, provided_key: str, expected_key: str) -> bool:
        return bool(expected_key) and provided_key == expected_key

    def push_trade(self, payload: dict[str, Any]) -> None:
        symbol = payload.get("symbol")
        market_id = SYMBOL_MARKET_MAP.get(symbol, payload.get("market_id", MARKET))
        payload["market_id"] = market_id
        payload.setdefault("exit_time", datetime.now().isoformat())
        payload.setdefault("source", "live")
        self.database.insert_trade(payload)
        if payload.get("entry_price") is not None and payload.get("exit_price") is not None:
            ts = datetime.now().isoformat()
            base = float(payload["entry_price"])
            close = float(payload["exit_price"])
            bar = {
                "symbol": symbol,
                "market_id": market_id,
                "ts": ts,
                "open": base,
                "high": max(base, close),
                "low": min(base, close),
                "close": close,
                "volume": 100000.0,
                "source": "live-trade",
            }
            self.buffer.append(bar)
            self.database.insert_price_bar(bar)

    def push_status(self, payload: dict[str, Any]) -> None:
        self.database.kv_set("engine_status", payload)

    def push_log(self, level: str, message: str) -> None:
        self.database.insert_log(level, message)

    def get_status(self) -> dict[str, Any]:
        status = self.database.kv_get("engine_status", {})
        trades = self.database.recent_trades(1)["total"]
        return {
            "engine": status,
            "broker_token_set": bool(self.database.kv_get("broker_auth_token")),
            "broker_token_time": self.database.kv_get("broker_token_time", ""),
            "db_trades": trades,
            "market": MARKET,
            "server_time": datetime.now().isoformat(),
            "railway_url": WEBAPP_URL,
            "risk_config": {
                "max_capital": RISK_CAPITAL,
                "max_daily_loss_pct": MAX_DAILY_LOSS_PCT,
                "max_dd_pct": MAX_DD_PCT,
                "max_positions": MAX_POSITIONS,
                "max_trades_day": MAX_TRADES_DAY,
                "min_rr": MIN_RR,
            },
        }

    def get_metrics(self) -> dict[str, Any]:
        engine = self.database.kv_get("engine_status", {})
        capital = float(engine.get("capital", RISK_CAPITAL))
        daily_pnl = float(engine.get("daily_pnl", 0.0))
        return self.database.metrics(daily_pnl=daily_pnl, capital=capital)

    def get_markets(self) -> list[dict[str, Any]]:
        result = []
        for snapshot in all_market_snapshots():
            instruments = []
            for symbol in snapshot["instruments"]:
                series = self.buffer.series(symbol, 24).bars
                latest = series[-1] if series else None
                prior = series[0]["close"] if series else None
                change_pct = 0.0
                if latest and prior:
                    change_pct = ((latest["close"] - prior) / prior) * 100 if prior else 0.0
                instruments.append(
                    {
                        "symbol": symbol,
                        "price": latest["close"] if latest else None,
                        "change_pct": round(change_pct, 2),
                    }
                )
            result.append({**snapshot, "instrument_data": instruments})
        return result

    def analyze_symbol(self, symbol: str) -> dict[str, Any]:
        cached = self._analysis_cache.get(symbol)
        now = datetime.now()
        if cached and now - cached[0] < timedelta(seconds=20):
            return cached[1]
        report = self.analyzer.analyze(symbol)
        self._analysis_cache[symbol] = (now, report)
        self.database.cache_analysis(symbol, report)
        if not self.database.recent_news(symbol, 2):
            for headline in report["sentiment"]["headlines"]:
                self.database.insert_news(headline)
        return report

    def ensure_signal_book(self, symbol: str) -> dict[str, Any]:
        report = self.analyze_symbol(symbol)
        self.database.insert_signal(report["signal"])
        return report

    def get_signals(self) -> list[dict[str, Any]]:
        return self.database.recent_signals(60)

    def get_news(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol and not self.database.recent_news(symbol, 1):
            self.analyze_symbol(symbol)
        return self.database.recent_news(symbol, 60)

    def get_price_history(self, symbol: str, bars: int) -> list[dict[str, Any]]:
        history = self.database.price_history(symbol, bars)
        if history:
            return history
        market_id = SYMBOL_MARKET_MAP[symbol]
        base_price = next(instrument["p"] for instrument in WORLD_MARKETS[market_id]["instruments"] if instrument["s"] == symbol)
        self.buffer.seed_symbol(symbol, market_id, base_price)
        return self.buffer.series(symbol, bars).bars

    def get_reports(self) -> dict[str, Any]:
        return self.database.report_breakdown()

    def simulation_start(self, market_id: str, speed: int, auto_trade: bool, threshold: float) -> dict[str, Any]:
        return self.simulator.start(market_id, speed, auto_trade, threshold)

    def simulation_stop(self) -> dict[str, Any]:
        return self.simulator.stop()

    def simulation_status(self) -> dict[str, Any]:
        return self.simulator.status()

    def simulation_reset(self) -> dict[str, Any]:
        result = self.simulator.reset()
        self._analysis_cache.clear()
        self._seed_all()
        return result