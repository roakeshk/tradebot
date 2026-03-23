# ============================================================
#  tradebot / config / settings.py
#  Central configuration — edit this file, nothing else
# ============================================================

import os
from pathlib import Path

# Load .env file if present (local development)
# Checks tradebot/.env first, then project root .env
try:
    from dotenv import load_dotenv
    _tradebot_env = Path(__file__).parent.parent / ".env"
    _root_env = Path(__file__).parent.parent.parent / ".env"
    load_dotenv(_tradebot_env)
    load_dotenv(_root_env)  # root values won't overwrite tradebot/.env values
except ImportError:
    pass  # dotenv not installed, use system env vars or defaults

# ── Paths ────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent.parent
DATA_DIR   = BASE_DIR / "data"
RAW_DIR    = DATA_DIR / "raw"
PROC_DIR   = DATA_DIR / "processed"
CACHE_DIR  = DATA_DIR / "cache"
LOG_DIR    = BASE_DIR / "logs"

# ── Broker ───────────────────────────────────────────────────
# ACTIVE_BROKER controls order execution.
# DATA_SOURCE controls where historical + live data comes from.
# They can be different — e.g. get data from Fyers (free) but
# execute orders through Shoonya (zero brokerage).
#
# ACTIVE_BROKER: "paper" | "zerodha" | "shoonya"
# DATA_SOURCE:   "fyers" | "angel" | "zerodha" | "yfinance"
ACTIVE_BROKER = "paper"    # start here; switch when account confirmed
DATA_SOURCE   = "angel"    # Angel One or Fyers — both free, no subscription

ZERODHA = {
    "api_key":    "YOUR_API_KEY",
    "api_secret": "YOUR_API_SECRET",
    "user_id":    "YOUR_USER_ID",
}

SHOONYA = {
    "user_id":   "YOUR_USER_ID",
    "password":  "YOUR_PASSWORD",
    "vendor_code": "YOUR_VENDOR_CODE",
    "api_secret":  "YOUR_API_SECRET",
    "imei":        "YOUR_IMEI",
}

# ── Fyers (FREE API — no monthly charges) ────────────────────
# Get from: myapi.fyers.in → Create App
FYERS = {
    "client_id":    "YOUR_APP_ID-100",    # e.g. "XJ12345-100"
    "secret_key":   "YOUR_SECRET_KEY",
    "redirect_uri": "https://127.0.0.1",  # set this in your Fyers app
}

# ── Angel One SmartAPI (FREE API — no monthly charges) ───────
# Get from: smartapi.angelone.in → Create App
# Requires TOTP (Google Authenticator) enabled on your Angel One account
ANGEL_ONE = {
    "api_key":     os.environ.get("ANGEL_API_KEY",     "YOUR_API_KEY"),
    "client_id":   os.environ.get("ANGEL_CLIENT_ID",  "YOUR_CLIENT_ID"),
    "password":    os.environ.get("ANGEL_PIN",         "YOUR_PIN"),
    "totp_secret": os.environ.get("ANGEL_TOTP_SECRET","YOUR_TOTP_SECRET"),
}

# ── Instruments ──────────────────────────────────────────────
# These are the instruments we will trade / collect data for.
# NSE Futures — use nearest expiry; code handles roll-over.
INSTRUMENTS = {
    "BANKNIFTY": {
        "exchange":     "NSE",
        "segment":      "NFO",          # futures segment
        "lot_size":     15,             # BankNifty lot = 15 units
        "tick_size":    0.05,
        "margin_approx_inr": 55000,    # approx per lot (changes daily)
        "priority":     1,              # 1 = primary instrument
    },
    "NIFTY": {
        "exchange":     "NSE",
        "segment":      "NFO",
        "lot_size":     50,
        "tick_size":    0.05,
        "margin_approx_inr": 65000,
        "priority":     2,
    },
    "CRUDEOIL": {
        "exchange":     "MCX",
        "segment":      "MCX",
        "lot_size":     100,            # barrels
        "tick_size":    1.0,
        "margin_approx_inr": 40000,
        "priority":     3,
    },
}

# ── Timeframes ───────────────────────────────────────────────
# All timeframes we store. Primary strategy runs on 5min.
TIMEFRAMES = ["1min", "3min", "5min", "15min", "1hour", "1day"]
PRIMARY_TF  = "5min"

# ── Session ──────────────────────────────────────────────────
# NSE / NFO session times (IST, 24h format)
SESSION = {
    "market_open":     "09:15",
    "market_close":    "15:30",
    "pre_open":        "09:00",
    "first_candle_end": "09:20",   # avoid first 5-min candle noise
    "no_trade_after":  "15:15",    # don't open new positions in last 15 min
    "mcx_open":        "09:00",
    "mcx_close":       "23:30",
}

# ── Cost model ───────────────────────────────────────────────
# Every parameter is documented — these eat your profits if ignored.
COST_MODEL = {
    # --- Zerodha F&O costs ---
    "zerodha": {
        "brokerage_per_order":    20.00,   # ₹20 flat per order (buy OR sell)
        "stt_pct_sell":           0.0001,  # 0.01% on SELL side notional only (F&O)
        "exchange_txn_charge_pct": 0.0000495,  # NSE F&O: ₹4.95 per lakh
        "sebi_charges_pct":       0.000001,    # ₹1 per crore
        "gst_pct":                0.18,        # 18% GST on (brokerage + exchange charges)
        "stamp_duty_pct_buy":     0.00003,     # 0.003% on BUY side notional only
    },
    # --- Shoonya / Finvasia F&O costs ---
    "shoonya": {
        "brokerage_per_order":    0.00,    # zero brokerage on F&O
        "stt_pct_sell":           0.0001,
        "exchange_txn_charge_pct": 0.0000495,
        "sebi_charges_pct":       0.000001,
        "gst_pct":                0.18,
        "stamp_duty_pct_buy":     0.00003,
    },
    # Slippage — conservative estimate for BankNifty futures
    # Actual slippage depends on order size and time of day.
    "slippage_ticks": 1,           # assume 1 tick (₹0.05) adverse fill per side
}

# ── Risk limits ──────────────────────────────────────────────
RISK = {
    "max_capital_inr":         100000,   # total capital allocated to bot
    "max_risk_per_trade_pct":  1.0,      # max 1% of capital per trade
    "max_daily_loss_pct":      3.0,      # halt bot if daily loss exceeds 3%
    "max_open_positions":      2,        # never hold more than 2 concurrent positions
    "max_trades_per_day":      10,       # circuit breaker on overtrading
    "min_rr_ratio":            1.5,      # reject any trade with R:R below 1.5
}

# ── Data pipeline ────────────────────────────────────────────
DATA = {
    "history_years":      3,            # fetch 3 years of history
    "cache_expiry_hours": 4,            # re-fetch if cache older than 4h
    "db_filename":        "market_data.db",  # SQLite file in DATA_DIR
    "backup_source":      "yfinance",   # fallback when broker API unavailable
}

# ── Alerts ───────────────────────────────────────────────────
# Telegram: create a bot via @BotFather, get token + chat_id
# Email: optional, only for daily summaries
ALERTS = {
    "enabled":          False,          # set True when credentials are filled
    # Telegram (recommended)
    "telegram_token":   os.environ.get("TELEGRAM_TOKEN",  ""),
    "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID",""),
    # Email (optional)
    "smtp_host":        "",             # e.g. "smtp.gmail.com"
    "smtp_port":        465,
    "email_from":       "",
    "email_to":         "",
    "email_password":   "",             # app password (not main password)
}

# ── Web app + Railway deployment ─────────────────────────────
# Your Railway deployment: https://tradebot-production-c63c.up.railway.app
# WEBAPP_URL and WEBAPP_KEY are read from environment variables.
# Set them in Railway → Variables dashboard, and locally in .env file.
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://tradebot-production-c63c.up.railway.app")
WEBAPP_KEY = os.environ.get("TRADEBOT_KEY", os.environ.get("WEBAPP_KEY", ""))

# ── Logging ──────────────────────────────────────────────────
LOGGING = {
    "level":        "INFO",             # DEBUG | INFO | WARNING | ERROR
    "to_file":      True,
    "to_console":   True,
    "max_mb":       50,                 # rotate log file at 50 MB
    "backup_count": 5,
}

# ── Options trading ──────────────────────────────────────────
OPTIONS = {
    "enabled":              True,
    "symbols":              ["BANKNIFTY", "NIFTY"],
    "max_open_positions":   2,
    "max_daily_loss_pct":   3.0,
    "max_capital_pct":      30.0,       # max % of capital in options at once
    "min_premium":          10.0,       # minimum option premium to trade (₹)
    "min_iv_rank_sell":     55.0,       # sell strategies need IV rank > 55
    "max_iv_rank_buy":      45.0,       # buy strategies need IV rank < 45
    "target_exit_pct":      0.50,       # exit selling strategies at 50% profit
    "stop_loss_pct":        2.0,        # exit if loss exceeds 2x premium collected
    "min_dte_sell":         2,          # don't sell with less than 2 DTE
    "max_dte_sell":         8,          # don't sell more than 8 DTE out
    "min_dte_buy":          5,          # don't buy with less than 5 DTE
    "chain_refresh_secs":   60,         # refresh option chain every 60 seconds
}
