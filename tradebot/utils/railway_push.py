# ============================================================
#  tradebot / utils / railway_push.py
#  Pushes trade data, status, and logs to Railway dashboard.
#
#  Your Railway dashboard: https://tradebot-production-c63c.up.railway.app
#  Data flows: main.py on your machine → POST → Railway → browser
#
#  All pushes are non-blocking (background thread).
#  Failures are silently logged — never crash the trading engine.
# ============================================================

import logging
import threading
import json
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import so we don't break imports if requests isn't installed
_requests = None

def _get_requests():
    global _requests
    if _requests is None:
        try:
            import requests as r
            _requests = r
        except ImportError:
            pass
    return _requests


class RailwayPusher:
    """
    Pushes data to the Railway-hosted dashboard.

    Usage:
        pusher = RailwayPusher()
        pusher.push_trade({...})
        pusher.push_status({...})
        pusher.push_log("INFO", "Trade executed")
    """

    def __init__(self):
        # Read from environment / settings
        try:
            from config.settings import WEBAPP_URL, WEBAPP_KEY
            self.url = WEBAPP_URL.rstrip("/")
            self.key = WEBAPP_KEY
        except Exception:
            self.url = "https://tradebot-production-c63c.up.railway.app"
            self.key = "tb_secret_2026"

        self._enabled = bool(self.url and self.key)
        if self._enabled:
            logger.info(f"RailwayPusher ready → {self.url}")

    def _push(self, endpoint: str, data: dict) -> None:
        """Fire-and-forget POST in background thread."""
        if not self._enabled:
            return
        req = _get_requests()
        if req is None:
            return
        def _do():
            try:
                req.post(
                    f"{self.url}/api/{endpoint}",
                    json=data,
                    headers={"X-API-Key": self.key},
                    timeout=8,
                )
            except Exception as e:
                logger.debug(f"Railway push failed ({endpoint}): {e}")
        threading.Thread(target=_do, daemon=True).start()

    def push_trade(self, trade: dict) -> None:
        """Push a completed trade to Railway."""
        self._push("push/trade", {
            "symbol":      trade.get("symbol", "BANKNIFTY"),
            "strategy":    trade.get("strategy", ""),
            "direction":   trade.get("direction", ""),
            "entry_price": trade.get("entry_price", 0),
            "exit_price":  trade.get("exit_price", 0),
            "net_pnl":     trade.get("net_pnl", 0),
            "exit_reason": trade.get("exit_reason", ""),
            "entry_time":  str(trade.get("entry_time", datetime.now().isoformat())),
            "lots":        trade.get("lots", 1),
        })

    def push_status(self, status: dict) -> None:
        """Push engine status (called every 30s from engine loop)."""
        self._push("push/status", {
            **status,
            "running":    True,
            "updated_at": datetime.now().isoformat(),
        })

    def push_log(self, level: str, message: str) -> None:
        """Push a log line."""
        self._push("push/log", {"level": level, "message": message})

    def push_options_trade(self, trade) -> None:
        """Push an options paper trade."""
        self._push("push/trade", {
            "symbol":      getattr(trade, "symbol", "BANKNIFTY"),
            "strategy":    getattr(trade, "strategy", "options"),
            "direction":   "OPTIONS",
            "entry_price": getattr(trade, "entry_spot", 0),
            "exit_price":  getattr(trade, "exit_spot", 0),
            "net_pnl":     getattr(trade, "net_pnl", 0),
            "exit_reason": getattr(trade, "exit_reason", ""),
            "entry_time":  str(getattr(trade, "entry_time", datetime.now().isoformat())),
            "lots":        getattr(trade, "lots", 1),
        })


# Singleton — import and use directly
_pusher: Optional[RailwayPusher] = None

def get_pusher() -> RailwayPusher:
    global _pusher
    if _pusher is None:
        _pusher = RailwayPusher()
    return _pusher
