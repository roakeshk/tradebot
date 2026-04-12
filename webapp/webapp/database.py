from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, db_path: Path, starting_capital: float, max_drawdown: float) -> None:
        self.db_path = db_path
        self.starting_capital = starting_capital
        self.max_drawdown = max_drawdown
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def init_db(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    market_id TEXT,
                    strategy TEXT,
                    direction TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    net_pnl REAL,
                    exit_reason TEXT,
                    entry_time TEXT,
                    exit_time TEXT,
                    lots INTEGER,
                    source TEXT,
                    confidence REAL,
                    analysis_json TEXT
                );
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT,
                    level TEXT,
                    message TEXT
                );
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    ts TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    market_id TEXT,
                    source TEXT
                );
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    ts TEXT,
                    market_id TEXT,
                    direction TEXT,
                    confidence REAL,
                    entry_price REAL,
                    stop_loss REAL,
                    target_price REAL,
                    status TEXT,
                    rationale TEXT,
                    automation_ready INTEGER,
                    payload_json TEXT
                );
                CREATE TABLE IF NOT EXISTS news_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    ts TEXT,
                    sentiment REAL,
                    headline TEXT,
                    source TEXT,
                    market_id TEXT
                );
                CREATE TABLE IF NOT EXISTS analysis_cache (
                    symbol TEXT PRIMARY KEY,
                    ts TEXT,
                    payload_json TEXT
                );
                """
            )

    def kv_set(self, key: str, value: Any) -> None:
        payload = json.dumps(value)
        timestamp = datetime.now().isoformat()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO kv(key, value, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, payload, timestamp),
            )

    def kv_get(self, key: str, default: Any = None) -> Any:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def insert_trade(self, trade: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO trades(
                    symbol, market_id, strategy, direction, entry_price, exit_price,
                    net_pnl, exit_reason, entry_time, exit_time, lots, source,
                    confidence, analysis_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.get("symbol"),
                    trade.get("market_id"),
                    trade.get("strategy"),
                    trade.get("direction"),
                    trade.get("entry_price"),
                    trade.get("exit_price"),
                    trade.get("net_pnl"),
                    trade.get("exit_reason"),
                    trade.get("entry_time"),
                    trade.get("exit_time"),
                    trade.get("lots", 1),
                    trade.get("source", "live"),
                    trade.get("confidence"),
                    json.dumps(trade.get("analysis", {})),
                ),
            )

    def insert_log(self, level: str, message: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO logs(ts, level, message) VALUES(?, ?, ?)",
                (datetime.now().isoformat(), level, message),
            )
            connection.execute(
                "DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 1200)"
            )

    def insert_price_bar(self, bar: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO price_history(symbol, ts, open, high, low, close, volume, market_id, source)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bar["symbol"],
                    bar["ts"],
                    bar["open"],
                    bar["high"],
                    bar["low"],
                    bar["close"],
                    bar["volume"],
                    bar["market_id"],
                    bar.get("source", "analysis"),
                ),
            )
            connection.execute(
                "DELETE FROM price_history WHERE id NOT IN (SELECT id FROM price_history WHERE symbol=? ORDER BY id DESC LIMIT 1000)",
                (bar["symbol"],),
            )

    def insert_signal(self, signal: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO signals(symbol, ts, market_id, direction, confidence, entry_price, stop_loss,
                    target_price, status, rationale, automation_ready, payload_json)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal["symbol"],
                    signal["ts"],
                    signal["market_id"],
                    signal["direction"],
                    signal["confidence"],
                    signal["entry_price"],
                    signal["stop_loss"],
                    signal["target_price"],
                    signal.get("status", "new"),
                    signal.get("rationale", ""),
                    1 if signal.get("automation_ready") else 0,
                    json.dumps(signal),
                ),
            )

    def insert_news(self, item: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO news_events(symbol, ts, sentiment, headline, source, market_id) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    item["symbol"],
                    item["ts"],
                    item["sentiment"],
                    item["headline"],
                    item["source"],
                    item["market_id"],
                ),
            )
            connection.execute(
                "DELETE FROM news_events WHERE id NOT IN (SELECT id FROM news_events ORDER BY id DESC LIMIT 500)"
            )

    def cache_analysis(self, symbol: str, payload: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO analysis_cache(symbol, ts, payload_json) VALUES(?, ?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET ts=excluded.ts, payload_json=excluded.payload_json",
                (symbol, datetime.now().isoformat(), json.dumps(payload)),
            )

    def recent_trades(self, limit: int = 50) -> dict[str, Any]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            total = connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        return {"trades": [dict(row) for row in rows], "total": total}

    def recent_logs(self, limit: int = 150) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT ts, level, message FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [f"[{row['ts'][:19]}] {row['level']:<8} {row['message']}" for row in reversed(rows)]

    def price_history(self, symbol: str, bars: int = 200) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT symbol, ts, open, high, low, close, volume, market_id FROM price_history WHERE symbol=? ORDER BY id DESC LIMIT ?",
                (symbol, bars),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def recent_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            items.append(item)
        return items

    def recent_news(self, symbol: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
        query = "SELECT symbol, ts, sentiment, headline, source, market_id FROM news_events"
        params: tuple[Any, ...] = ()
        if symbol:
            query += " WHERE symbol=?"
            params = (symbol,)
        query += " ORDER BY id DESC LIMIT ?"
        params += (limit,)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def metrics(self, daily_pnl: float = 0.0, capital: float | None = None) -> dict[str, Any]:
        with self.connect() as connection:
            rows = connection.execute("SELECT net_pnl FROM trades ORDER BY id ASC").fetchall()
        pnls = [float(row[0]) for row in rows if row[0] is not None]
        nav_base = self.starting_capital if capital is None else capital
        if not pnls:
            gate = {"win_rate": False, "profit_factor": False, "max_drawdown": False, "min_trades": False, "all_pass": False}
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "total_pnl": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
                "sortino": 0.0,
                "calmar": 0.0,
                "expectancy": 0.0,
                "daily_pnl": daily_pnl,
                "nav": nav_base,
                "gate": gate,
            }

        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl <= 0]
        total_trades = len(pnls)
        win_rate = len(wins) / total_trades * 100
        profit_factor = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 99.0
        expectancy = sum(pnls) / total_trades
        variance = sum((pnl - expectancy) ** 2 for pnl in pnls) / total_trades if total_trades > 1 else 0.0
        deviation = math.sqrt(variance) if variance > 0 else 0.0
        sharpe = (expectancy / deviation) * math.sqrt(252) if deviation else 0.0
        negative_deviation = math.sqrt(sum(pnl ** 2 for pnl in losses if pnl < 0) / total_trades) if losses else 0.0
        sortino = (expectancy / negative_deviation) * math.sqrt(252) if negative_deviation else 0.0

        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for pnl in pnls:
            cumulative += pnl
            peak = max(peak, cumulative)
            max_drawdown = min(max_drawdown, cumulative - peak)
        total_pnl = sum(pnls)
        annual_return_pct = (total_pnl / self.starting_capital) * 100 if self.starting_capital else 0.0
        max_dd_pct = abs(max_drawdown) / self.starting_capital * 100 if self.starting_capital else 0.0
        calmar = annual_return_pct / max_dd_pct if max_dd_pct else 0.0

        gate = {
            "win_rate": win_rate >= 55,
            "profit_factor": profit_factor >= 1.4,
            "max_drawdown": abs(max_drawdown) < self.max_drawdown,
            "min_trades": total_trades >= 200,
        }
        gate["all_pass"] = all(gate.values())
        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "total_pnl": round(total_pnl, 2),
            "max_drawdown": round(max_drawdown, 2),
            "sharpe": round(sharpe, 2),
            "sortino": round(sortino, 2),
            "calmar": round(calmar, 2),
            "expectancy": round(expectancy, 2),
            "daily_pnl": round(daily_pnl, 2),
            "nav": round(nav_base + total_pnl, 2),
            "gate": gate,
        }

    def equity_curve(self, capital: float) -> dict[str, Any]:
        with self.connect() as connection:
            rows = connection.execute("SELECT net_pnl FROM trades ORDER BY id ASC").fetchall()
        curve = [capital]
        running = capital
        for row in rows:
            running += float(row[0] or 0)
            curve.append(round(running, 2))
        peak = capital
        drawdown = []
        drawdown_pct = []
        for equity in curve:
            peak = max(peak, equity)
            dd = equity - peak
            drawdown.append(round(dd, 2))
            drawdown_pct.append(round((dd / peak * 100) if peak else 0, 2))
        return {"equity": curve, "drawdown": drawdown, "dd_pct": drawdown_pct, "capital": capital, "peak": peak}

    def report_breakdown(self) -> dict[str, Any]:
        with self.connect() as connection:
            trades = [dict(row) for row in connection.execute("SELECT * FROM trades ORDER BY id DESC").fetchall()]
        summary = {
            "total_pnl": round(sum(float(trade.get("net_pnl") or 0) for trade in trades), 2),
            "best_trade": round(max((float(trade.get("net_pnl") or 0) for trade in trades), default=0.0), 2),
            "worst_trade": round(min((float(trade.get("net_pnl") or 0) for trade in trades), default=0.0), 2),
            "avg_hold_minutes": 0,
            "trade_count": len(trades),
        }
        strategy_map: dict[str, list[float]] = defaultdict(list)
        market_map: dict[str, float] = defaultdict(float)
        hour_map: dict[str, float] = defaultdict(float)
        for trade in trades:
            pnl = float(trade.get("net_pnl") or 0)
            strategy_map[trade.get("strategy") or "unknown"].append(pnl)
            market_map[trade.get("market_id") or "UNKNOWN"] += pnl
            entry_time = trade.get("entry_time") or ""
            hour = entry_time[11:13] if len(entry_time) >= 13 else "--"
            hour_map[hour] += pnl
        by_strategy = [
            {
                "strategy": strategy,
                "trades": len(values),
                "win_rate": round(sum(1 for value in values if value > 0) / len(values) * 100, 1) if values else 0.0,
                "avg_pnl": round(sum(values) / len(values), 2) if values else 0.0,
                "total_pnl": round(sum(values), 2),
                "profit_factor": round(sum(value for value in values if value > 0) / abs(sum(value for value in values if value <= 0)), 2)
                if any(value <= 0 for value in values)
                else 99.0,
            }
            for strategy, values in sorted(strategy_map.items(), key=lambda item: sum(item[1]), reverse=True)
        ]
        by_market = [{"market": market, "total_pnl": round(total, 2)} for market, total in market_map.items()]
        by_hour = [{"hour": hour, "total_pnl": round(total, 2)} for hour, total in sorted(hour_map.items())]
        return {"summary": summary, "by_strategy": by_strategy, "by_market": by_market, "by_hour": by_hour, "trades": trades[:50]}

    def reset(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM trades")
            connection.execute("DELETE FROM logs")
            connection.execute("DELETE FROM kv")
            connection.execute("DELETE FROM price_history")
            connection.execute("DELETE FROM signals")
            connection.execute("DELETE FROM news_events")
            connection.execute("DELETE FROM analysis_cache")