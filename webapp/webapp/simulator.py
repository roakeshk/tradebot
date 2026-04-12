from __future__ import annotations

import math
import random
import threading
import time
import traceback
from datetime import datetime, timedelta
from typing import Any

from .analytics import InstrumentAnalyzer, PriceBuffer
from .config import DEFAULT_SIGNAL_THRESHOLD, MAX_DAILY_LOSS_PCT, RISK_CAPITAL, SYMBOL_MARKET_MAP, WORLD_MARKETS
from .database import Database


class DemoSimulator(threading.Thread):
    def __init__(
        self,
        database: Database,
        buffer: PriceBuffer,
        analyzer: InstrumentAnalyzer,
        market_id: str = "US",
        speed: int = 5,
        auto_trade: bool = True,
        min_confidence: float = DEFAULT_SIGNAL_THRESHOLD,
        direction_filter: str = "both",
        max_trades: int = 0,
        capital: float = RISK_CAPITAL,
    ) -> None:
        super().__init__(daemon=True)
        self.database = database
        self.buffer = buffer
        self.analyzer = analyzer
        self.market_id = market_id
        self.speed = max(1, speed)
        self.auto_trade = auto_trade
        self.min_confidence = min_confidence
        self.direction_filter = direction_filter
        self.max_trades = max_trades
        self._stop_event = threading.Event()
        self.started_at: str | None = None
        self.capital = capital
        self.daily_pnl = 0.0
        self.trades_count = 0
        self.open_positions = 0

    @property
    def running(self) -> bool:
        return self.is_alive() and not self._stop_event.is_set()

    def stop(self) -> None:
        self._stop_event.set()

    def status_dict(self) -> dict[str, Any]:
        elapsed = "0:00:00"
        if self.started_at:
            started = datetime.fromisoformat(self.started_at)
            elapsed = str(datetime.now() - started).split(".", 1)[0]
        return {
            "running": self.running,
            "alive": self.is_alive(),
            "market": self.market_id,
            "speed": self.speed,
            "trades": self.trades_count,
            "capital": round(self.capital, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "started_at": self.started_at,
            "elapsed": elapsed,
            "auto_trade": self.auto_trade,
            "threshold": self.min_confidence,
            "direction_filter": self.direction_filter,
            "max_trades": self.max_trades,
        }

    def run(self) -> None:
        self.started_at = datetime.now().isoformat()
        market = WORLD_MARKETS[self.market_id]
        self.database.insert_log("INFO", f"Simulation started for {self.market_id} at {self.speed}s cadence")
        try:
            while not self._stop_event.is_set():
                self.database.insert_log("INFO", f"Simulation cycle running for {self.market_id}")
                for instrument in market["instruments"]:
                    if self._stop_event.is_set():
                        break
                    symbol = instrument["s"]
                    self.buffer.seed_symbol(symbol, self.market_id, instrument["p"])
                    bar = self._next_bar(symbol, self.market_id)
                    self.buffer.append(bar)
                    self.database.insert_price_bar(bar)
                    analysis = self.analyzer.analyze(symbol)
                    for headline in analysis["sentiment"]["headlines"]:
                        self.database.insert_news(headline)
                    self.database.insert_signal(analysis["signal"])
                    self.database.cache_analysis(symbol, analysis)
                    if self.auto_trade and analysis["signal"]["confidence"] >= self.min_confidence:
                        self._execute_trade(analysis)
                    self._push_status()
                    if self._stop_event.wait(self.speed / max(len(market["instruments"]), 1)):
                        break
                if abs(self.daily_pnl) >= RISK_CAPITAL * (MAX_DAILY_LOSS_PCT / 100):
                    self.database.insert_log("WARN", "Simulation hit daily loss guardrail; auto-pausing new entries")
                    self._push_status(halted=True)
                    while not self._stop_event.is_set() and self._stop_event.wait(1):
                        break
        except Exception as exc:
            self.database.insert_log("ERROR", f"Simulation crashed: {exc}")
            self.database.insert_log("ERROR", traceback.format_exc().strip())
            self.database.kv_set(
                "engine_status",
                {
                    "running": False,
                    "halted": False,
                    "capital": round(self.capital, 2),
                    "daily_pnl": round(self.daily_pnl, 2),
                    "open_positions": self.open_positions,
                    "trades_today": self.trades_count,
                    "max_daily_loss": round(abs(min(self.daily_pnl, 0.0)), 2),
                    "updated_at": datetime.now().isoformat(),
                    "mode": "simulation",
                    "market": self.market_id,
                    "auto_trade": self.auto_trade,
                    "threshold": self.min_confidence,
                    "error": str(exc),
                },
            )
        finally:
            self.database.insert_log("INFO", f"Simulation loop exited stop_event={self._stop_event.is_set()} trades={self.trades_count}")

    def _next_bar(self, symbol: str, market_id: str) -> dict[str, Any]:
        latest = self.buffer.latest(symbol)
        open_price = float(latest["close"]) if latest else WORLD_MARKETS[market_id]["instruments"][0]["p"]
        sigma = self._session_volatility(market_id)
        drift = random.gauss(0.0005, sigma)
        close_price = max(0.0001, open_price * math.exp(drift))
        high_price = max(open_price, close_price) * (1 + abs(random.gauss(0, sigma / 2)))
        low_price = min(open_price, close_price) * (1 - abs(random.gauss(0, sigma / 2)))
        volume = abs(random.gauss(120000, 45000))
        return {
            "symbol": symbol,
            "market_id": market_id,
            "ts": datetime.now().isoformat(),
            "open": round(open_price, 6),
            "high": round(high_price, 6),
            "low": round(max(0.0001, low_price), 6),
            "close": round(close_price, 6),
            "volume": round(volume, 2),
            "source": "simulation",
        }

    def _session_volatility(self, market_id: str) -> float:
        market_type = WORLD_MARKETS[market_id]["type"]
        utc_hour = datetime.utcnow().hour
        if market_type == "crypto":
            return 0.012 if utc_hour in {13, 14, 15, 20, 21} else 0.009
        if market_type == "forex":
            if 7 <= utc_hour < 16:
                return 0.0038
            if 13 <= utc_hour < 22:
                return 0.0035
            if 0 <= utc_hour < 7:
                return 0.0024
            return 0.0018
        return 0.0045

    def _execute_trade(self, analysis: dict[str, Any]) -> None:
        signal = analysis["signal"]
        symbol = signal["symbol"]
        market_id = signal["market_id"]
        direction = signal["direction"]
        if self.direction_filter == "long_only" and direction != "LONG":
            return
        if self.direction_filter == "short_only" and direction != "SHORT":
            return
        if self.max_trades > 0 and self.trades_count >= self.max_trades:
            return
        latest = self.buffer.latest(symbol) or {"close": signal["entry_price"]}
        entry_price = float(latest["close"])
        confidence = float(signal["confidence"])
        hold_steps = random.randint(2, 6)
        exit_price = entry_price
        for _ in range(hold_steps):
            preview = self._next_bar(symbol, market_id)
            exit_price = float(preview["close"])
            self.buffer.append(preview)
            self.database.insert_price_bar(preview)
        if direction == "LONG":
            pnl = (exit_price - entry_price) * 100
        else:
            pnl = (entry_price - exit_price) * 100
        pnl *= max(1, round(confidence / 20))
        pnl = round(pnl, 2)
        exit_reason = "target" if pnl > 0 else "stop"
        trade = {
            "symbol": symbol,
            "market_id": market_id,
            "strategy": "analysis_auto",
            "direction": direction,
            "entry_price": round(entry_price, 4),
            "exit_price": round(exit_price, 4),
            "net_pnl": pnl,
            "exit_reason": exit_reason,
            "entry_time": datetime.now().isoformat(),
            "exit_time": (datetime.now() + timedelta(minutes=hold_steps * 5)).isoformat(),
            "lots": max(1, round(confidence / 25)),
            "source": "simulation",
            "confidence": confidence,
            "analysis": {
                "technical": analysis["technicals"]["scores"]["technical"],
                "fundamental": analysis["fundamentals"]["scores"]["fundamental"],
                "sentiment": analysis["sentiment"]["aggregate"],
            },
        }
        self.database.insert_trade(trade)
        self.database.insert_log(
            "INFO",
            f"AUTO {direction} {symbol} conf={confidence:.1f} entry={entry_price:.4f} exit={exit_price:.4f} pnl={pnl:+.2f}",
        )
        self.capital += pnl
        self.daily_pnl += pnl
        self.trades_count += 1

    def _push_status(self, halted: bool = False) -> None:
        self.database.kv_set(
            "engine_status",
            {
                "running": True,
                "halted": halted,
                "capital": round(self.capital, 2),
                "daily_pnl": round(self.daily_pnl, 2),
                "open_positions": self.open_positions,
                "trades_today": self.trades_count,
                "max_daily_loss": round(abs(min(self.daily_pnl, 0.0)), 2),
                "updated_at": datetime.now().isoformat(),
                "mode": "simulation",
                "market": self.market_id,
                "auto_trade": self.auto_trade,
                "threshold": self.min_confidence,
            },
        )


class SimulatorManager:
    def __init__(self, database: Database, buffer: PriceBuffer, analyzer: InstrumentAnalyzer) -> None:
        self.database = database
        self.buffer = buffer
        self.analyzer = analyzer
        self._lock = threading.Lock()
        self._simulator: DemoSimulator | None = None

    def start(self, market_id: str, speed: int, auto_trade: bool, threshold: float,
              direction_filter: str = "both", max_trades: int = 0, capital: float = RISK_CAPITAL) -> dict[str, Any]:
        with self._lock:
            if self._simulator and self._simulator.running:
                self._simulator.stop()
                self._simulator.join(timeout=3)
            self._simulator = DemoSimulator(
                database=self.database,
                buffer=self.buffer,
                analyzer=self.analyzer,
                market_id=market_id,
                speed=speed,
                auto_trade=auto_trade,
                min_confidence=threshold,
                direction_filter=direction_filter,
                max_trades=max_trades,
                capital=capital,
            )
            self._simulator.start()
            return self._simulator.status_dict()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._simulator and self._simulator.running:
                self._simulator.stop()
                self._simulator.join(timeout=3)
                self.database.kv_set(
                    "engine_status",
                    {
                        "running": False,
                        "halted": False,
                        "capital": round(self._simulator.capital, 2),
                        "daily_pnl": round(self._simulator.daily_pnl, 2),
                        "open_positions": 0,
                        "trades_today": self._simulator.trades_count,
                        "max_daily_loss": round(abs(min(self._simulator.daily_pnl, 0.0)), 2),
                        "updated_at": datetime.now().isoformat(),
                        "mode": "simulation",
                        "market": self._simulator.market_id,
                        "auto_trade": self._simulator.auto_trade,
                        "threshold": self._simulator.min_confidence,
                    },
                )
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._simulator:
                return self._simulator.status_dict()
        return {"running": False, "market": None, "trades": 0, "capital": RISK_CAPITAL, "daily_pnl": 0.0}

    def reset(self) -> dict[str, Any]:
        self.stop()
        self.database.reset()
        self.database.init_db()
        return {"status": "reset"}