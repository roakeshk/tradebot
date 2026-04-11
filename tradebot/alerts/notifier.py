# ============================================================
#  tradebot / alerts / notifier.py
#  Multi-channel alert system.
#  Sends trade notifications, daily summaries, and error alerts.
#
#  Channels supported:
#    - Telegram (recommended — instant, free)
#    - Email (SMTP — for daily summary reports)
#    - Console (always on, for development)
#
#  Telegram setup (5 minutes):
#    1. Open Telegram → search @BotFather → /newbot
#    2. Give it a name → get your BOT_TOKEN
#    3. Message your bot once to create a chat
#    4. Visit: https://api.telegram.org/bot<TOKEN>/getUpdates
#    5. Copy the chat_id from the JSON response
#    6. Fill ALERTS config in settings.py
# ============================================================

import logging
import smtplib
import threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from config.settings import ALERTS, MARKET

_CUR = "$" if MARKET == "US" else "₹"

logger = logging.getLogger(__name__)


class Notifier:
    """
    Thread-safe notification dispatcher.
    Sends to all configured channels.
    Failed sends are logged but never crash the trading engine.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._queue: list[str] = []
        self._enabled = ALERTS.get("enabled", False)
        logger.info(
            f"Notifier ready | telegram={'on' if ALERTS.get('telegram_token') else 'off'} "
            f"email={'on' if ALERTS.get('smtp_host') else 'off'}"
        )

    def send(self, message: str, level: str = "INFO") -> None:
        """
        Send alert to all configured channels.
        Non-blocking — runs in background thread.
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg  = f"[{timestamp}] {message}"

        # Always log to console
        if level == "ERROR":
            logger.error(f"ALERT: {message}")
        else:
            logger.info(f"ALERT: {message}")

        if not self._enabled:
            return

        thread = threading.Thread(
            target=self._dispatch,
            args=(full_msg, level),
            daemon=True
        )
        thread.start()

    def send_daily_summary(self, summary: dict) -> None:
        """Send formatted daily P&L summary."""
        pnl   = summary.get("daily_pnl", 0)
        emoji = "✅" if pnl >= 0 else "❌"
        msg = (
            f"{emoji} Daily Summary — {datetime.now():%d %b %Y}\n"
            f"P&L:     {_CUR}{pnl:,.0f}\n"
            f"Trades:  {summary.get('trades_today', 0)}\n"
            f"Capital: {_CUR}{summary.get('capital', 0):,.0f}\n"
            f"Status:  {'HALTED' if summary.get('halted') else 'Active'}"
        )
        self.send(msg, level="INFO")

    def send_error(self, error: str, context: str = "") -> None:
        """Send error alert — higher priority."""
        msg = f"🚨 ERROR\n{error}"
        if context:
            msg += f"\nContext: {context}"
        self.send(msg, level="ERROR")

    def _dispatch(self, message: str, level: str) -> None:
        """Send to all channels. Errors are caught silently."""
        if ALERTS.get("telegram_token"):
            self._send_telegram(message)
        if ALERTS.get("smtp_host") and level in ("ERROR", "DAILY"):
            self._send_email(message)

    def _send_telegram(self, message: str) -> None:
        try:
            import requests
            token   = ALERTS["telegram_token"]
            chat_id = ALERTS["telegram_chat_id"]
            url     = f"https://api.telegram.org/bot{token}/sendMessage"
            resp = requests.post(url, json={
                "chat_id":    chat_id,
                "text":       message,
                "parse_mode": "HTML",
            }, timeout=10)
            if not resp.ok:
                logger.warning(f"Telegram send failed: {resp.text[:100]}")
        except Exception as e:
            logger.warning(f"Telegram error: {e}")

    def _send_email(self, message: str) -> None:
        try:
            msg = MIMEMultipart()
            msg["From"]    = ALERTS["email_from"]
            msg["To"]      = ALERTS["email_to"]
            msg["Subject"] = f"TradeBot Alert — {datetime.now():%d %b %H:%M}"
            msg.attach(MIMEText(message, "plain"))

            with smtplib.SMTP_SSL(ALERTS["smtp_host"], ALERTS.get("smtp_port", 465)) as smtp:
                smtp.login(ALERTS["email_from"], ALERTS["email_password"])
                smtp.sendmail(ALERTS["email_from"], ALERTS["email_to"], msg.as_string())
        except Exception as e:
            logger.warning(f"Email error: {e}")
