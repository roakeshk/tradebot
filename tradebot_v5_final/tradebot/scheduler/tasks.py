#!/usr/bin/env python3
# ============================================================
#  tradebot / scheduler / tasks.py
#  Daily and weekly automated tasks.
#
#  Run this as a background process alongside the engine:
#    python -m scheduler.tasks
#
#  Or use cron (recommended on Linux/Mac):
#    # Add to crontab: crontab -e
#    0 8 * * 1-5  cd /path/to/tradebot && python -m scheduler.tasks --task token
#    30 8 * * 1-5 cd /path/to/tradebot && python -m scheduler.tasks --task data
#    0 20 * * 0   cd /path/to/tradebot && python -m scheduler.tasks --task retrain
#    0 16 * * 1-5 cd /path/to/tradebot && python -m scheduler.tasks --task summary
#
#  Tasks:
#    token   — refresh broker access tokens (run 8:00 AM daily)
#    data    — fetch latest OHLCV bars (run 8:30 AM daily)
#    retrain — retrain AI classifiers (run Sunday 8:00 PM)
#    summary — send daily P&L report (run 4:00 PM on trading days)
#    all     — run all morning tasks in sequence
# ============================================================

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.logger import setup_logging
from config.settings import DATA_SOURCE, ACTIVE_BROKER, INSTRUMENTS, TIMEFRAMES


def task_refresh_tokens() -> None:
    """Refresh access tokens for configured brokers."""
    logger = logging.getLogger("scheduler.token")
    logger.info("Refreshing broker tokens...")

    if DATA_SOURCE == "angel":
        try:
            import pyotp
            from SmartApi import SmartConnect
            from config.settings import ANGEL_ONE

            api  = SmartConnect(api_key=ANGEL_ONE["api_key"])
            totp = pyotp.TOTP(ANGEL_ONE["totp_secret"]).now()
            data = api.generateSession(ANGEL_ONE["client_id"], ANGEL_ONE["password"], totp)
            if data["status"]:
                Path(".angel_auth_token").write_text(data["data"]["jwtToken"])
                Path(".angel_feed_token").write_text(api.getfeedToken())
                logger.info("Angel One token refreshed successfully")
            else:
                logger.error(f"Angel One token refresh failed: {data.get('message')}")
        except ImportError:
            logger.warning("Angel One SDK not installed — skip token refresh")
        except Exception as e:
            logger.error(f"Angel token error: {e}")

    if DATA_SOURCE == "fyers" or ACTIVE_BROKER == "fyers":
        logger.info("Fyers token requires browser auth — run generate_token_fyers.py manually")

    if ACTIVE_BROKER == "zerodha":
        logger.info("Zerodha token requires browser auth — run generate_token.py manually")

    if ACTIVE_BROKER == "shoonya":
        try:
            import pyotp
            from NorenRestApiPy.NorenApi import NorenApi
            from config.settings import SHOONYA

            class API(NorenApi):
                def __init__(self):
                    super().__init__(
                        host="https://api.shoonya.com/NorenWClientTP/",
                        websocket="wss://api.shoonya.com/NorenWSTP/"
                    )
            api  = API()
            totp = pyotp.TOTP(SHOONYA.get("totp_secret", "")).now() if SHOONYA.get("totp_secret") else ""
            ret  = api.login(
                userid=SHOONYA["user_id"], password=SHOONYA["password"],
                twoFA=totp, vendor_code=SHOONYA["vendor_code"],
                api_secret=SHOONYA["api_secret"], imei=SHOONYA["imei"],
            )
            if ret and ret.get("stat") == "Ok":
                logger.info("Shoonya token refreshed")
            else:
                logger.error(f"Shoonya login failed: {ret}")
        except ImportError:
            logger.warning("Shoonya SDK not installed")
        except Exception as e:
            logger.error(f"Shoonya token error: {e}")


def task_fetch_data() -> None:
    """Fetch latest OHLCV bars for all instruments."""
    logger = logging.getLogger("scheduler.data")
    from data.pipeline import DataPipeline

    dp = DataPipeline()
    priority_tfs = ["1min", "5min", "15min", "1hour", "1day"]

    for symbol in INSTRUMENTS:
        for tf in priority_tfs:
            try:
                n = dp.fetch_and_store(symbol, tf, force=False)
                if n:
                    logger.info(f"  {symbol} {tf}: +{n} bars")
            except Exception as e:
                logger.warning(f"  {symbol} {tf}: {e}")

    logger.info("Data fetch complete")


def task_retrain() -> None:
    """Retrain AI classifiers on latest data."""
    logger = logging.getLogger("scheduler.retrain")
    logger.info("Starting weekly retraining...")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "ai.retrain"],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode == 0:
            logger.info("Retraining completed successfully")
            logger.info(result.stdout[-2000:])
        else:
            logger.error(f"Retraining failed:\n{result.stderr[-1000:]}")
    except Exception as e:
        logger.error(f"Retrain task error: {e}")


def task_daily_summary() -> None:
    """Send daily P&L summary via alerts."""
    logger = logging.getLogger("scheduler.summary")
    from pathlib import Path
    import pandas as pd
    from alerts.notifier import Notifier

    notifier = Notifier()
    today    = datetime.now().strftime("%Y%m%d")
    log_dir  = Path("data/processed")

    # Find today's trade logs
    today_files = list(log_dir.glob(f"*_{today}.csv"))
    if not today_files:
        notifier.send(f"📊 Daily Summary {datetime.now():%d %b}: No trades today")
        return

    dfs  = [pd.read_csv(f) for f in today_files]
    all_ = pd.concat(dfs)

    if "net_pnl" not in all_.columns:
        return

    total_pnl = all_["net_pnl"].sum()
    trades    = len(all_)
    wins      = (all_["net_pnl"] > 0).sum()
    wr        = wins / trades * 100 if trades else 0

    summary = {
        "daily_pnl":    total_pnl,
        "trades_today": trades,
        "capital":      0,
        "halted":       False,
    }
    notifier.send_daily_summary(summary)
    logger.info(f"Summary sent: P&L=₹{total_pnl:,.0f} trades={trades} WR={wr:.0f}%")


def main():
    parser = argparse.ArgumentParser(description="Run scheduled tasks")
    parser.add_argument("--task", default="all",
                        choices=["token", "data", "retrain", "summary", "all"])
    args = parser.parse_args()

    setup_logging("scheduler")
    logger = logging.getLogger("scheduler")
    logger.info(f"Running task: {args.task}")

    if args.task in ("token", "all"):
        task_refresh_tokens()
    if args.task in ("data", "all"):
        task_fetch_data()
    if args.task == "summary":
        task_daily_summary()
    if args.task == "retrain":
        task_retrain()

    logger.info("Done")


if __name__ == "__main__":
    main()
