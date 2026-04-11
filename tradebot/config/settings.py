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

# ── Market ─────────────────────────────────────────────────
# MARKET: "US" | "INDIA"
# US market uses yfinance (free, no API key needed)
# India market can use broker APIs or yfinance fallback
MARKET = os.environ.get("MARKET", "US")

# ── Broker ───────────────────────────────────────────────────
# ACTIVE_BROKER controls order execution.
# DATA_SOURCE controls where historical + live data comes from.
# They can be different — e.g. get data from Fyers (free) but
# execute orders through Shoonya (zero brokerage).
#
# ACTIVE_BROKER: "paper" | "zerodha" | "shoonya"
# DATA_SOURCE:   "yfinance" | "fyers" | "angel" | "zerodha"
ACTIVE_BROKER = "paper"    # start here; switch when account confirmed
DATA_SOURCE   = os.environ.get("DATA_SOURCE", "yfinance")  # yfinance works with zero config

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
# INDIA instruments — NSE Futures
INSTRUMENTS_INDIA = {
    "BANKNIFTY": {
        "exchange": "NSE", "segment": "NFO", "lot_size": 15,
        "tick_size": 0.05, "margin_approx": 55000, "priority": 1,
        "yfinance": "^NSEBANK", "currency": "INR",
        "strike_step": 100, "expiry_weekday": 2, "default_iv": 0.18,
        "iv_hist_low": 0.10, "iv_hist_high": 0.40, "default_spot": 48000,
    },
    "NIFTY": {
        "exchange": "NSE", "segment": "NFO", "lot_size": 50,
        "tick_size": 0.05, "margin_approx": 55000, "priority": 2,
        "yfinance": "^NSEI", "currency": "INR",
        "strike_step": 50, "expiry_weekday": 3, "default_iv": 0.14,
        "iv_hist_low": 0.09, "iv_hist_high": 0.35, "default_spot": 22000,
    },
    "CRUDEOIL": {
        "exchange": "MCX", "segment": "MCX", "lot_size": 100,
        "tick_size": 1.0, "margin_approx": 40000, "priority": 3,
        "yfinance": "CL=F", "currency": "INR",
        "strike_step": 50, "expiry_weekday": 3, "default_iv": 0.25,
        "iv_hist_low": 0.20, "iv_hist_high": 0.55, "default_spot": 5500,
    },
}

# US market instruments — stocks and ETFs (lot_size=1 for stocks)
INSTRUMENTS_US = {
    "SPY": {
        "exchange": "NYSE", "segment": "EQUITY", "lot_size": 1,
        "tick_size": 0.01, "margin_approx": 0, "priority": 1,
        "yfinance": "SPY", "currency": "USD",
        "strike_step": 1, "expiry_weekday": 4, "default_iv": 0.16,
        "iv_hist_low": 0.09, "iv_hist_high": 0.45, "default_spot": 520,
    },
    "QQQ": {
        "exchange": "NASDAQ", "segment": "EQUITY", "lot_size": 1,
        "tick_size": 0.01, "margin_approx": 0, "priority": 2,
        "yfinance": "QQQ", "currency": "USD",
        "strike_step": 1, "expiry_weekday": 4, "default_iv": 0.18,
        "iv_hist_low": 0.12, "iv_hist_high": 0.50, "default_spot": 440,
    },
    "AAPL": {
        "exchange": "NASDAQ", "segment": "EQUITY", "lot_size": 1,
        "tick_size": 0.01, "margin_approx": 0, "priority": 3,
        "yfinance": "AAPL", "currency": "USD",
        "strike_step": 2.5, "expiry_weekday": 4, "default_iv": 0.25,
        "iv_hist_low": 0.15, "iv_hist_high": 0.55, "default_spot": 190,
    },
    "MSFT": {
        "exchange": "NASDAQ", "segment": "EQUITY", "lot_size": 1,
        "tick_size": 0.01, "margin_approx": 0, "priority": 4,
        "yfinance": "MSFT", "currency": "USD",
        "strike_step": 2.5, "expiry_weekday": 4, "default_iv": 0.22,
        "iv_hist_low": 0.14, "iv_hist_high": 0.50, "default_spot": 420,
    },
    "TSLA": {
        "exchange": "NASDAQ", "segment": "EQUITY", "lot_size": 1,
        "tick_size": 0.01, "margin_approx": 0, "priority": 5,
        "yfinance": "TSLA", "currency": "USD",
        "strike_step": 5, "expiry_weekday": 4, "default_iv": 0.45,
        "iv_hist_low": 0.30, "iv_hist_high": 0.90, "default_spot": 250,
    },
    "NVDA": {
        "exchange": "NASDAQ", "segment": "EQUITY", "lot_size": 1,
        "tick_size": 0.01, "margin_approx": 0, "priority": 6,
        "yfinance": "NVDA", "currency": "USD",
        "strike_step": 5, "expiry_weekday": 4, "default_iv": 0.35,
        "iv_hist_low": 0.25, "iv_hist_high": 0.70, "default_spot": 130,
    },
    "AMZN": {
        "exchange": "NASDAQ", "segment": "EQUITY", "lot_size": 1,
        "tick_size": 0.01, "margin_approx": 0, "priority": 7,
        "yfinance": "AMZN", "currency": "USD",
        "strike_step": 2.5, "expiry_weekday": 4, "default_iv": 0.28,
        "iv_hist_low": 0.18, "iv_hist_high": 0.55, "default_spot": 190,
    },
    "GOOGL": {
        "exchange": "NASDAQ", "segment": "EQUITY", "lot_size": 1,
        "tick_size": 0.01, "margin_approx": 0, "priority": 8,
        "yfinance": "GOOGL", "currency": "USD",
        "strike_step": 2.5, "expiry_weekday": 4, "default_iv": 0.25,
        "iv_hist_low": 0.16, "iv_hist_high": 0.50, "default_spot": 165,
    },
    "META": {
        "exchange": "NASDAQ", "segment": "EQUITY", "lot_size": 1,
        "tick_size": 0.01, "margin_approx": 0, "priority": 9,
        "yfinance": "META", "currency": "USD",
        "strike_step": 5, "expiry_weekday": 4, "default_iv": 0.30,
        "iv_hist_low": 0.20, "iv_hist_high": 0.60, "default_spot": 500,
    },
    "JPM": {
        "exchange": "NYSE", "segment": "EQUITY", "lot_size": 1,
        "tick_size": 0.01, "margin_approx": 0, "priority": 10,
        "yfinance": "JPM", "currency": "USD",
        "strike_step": 2.5, "expiry_weekday": 4, "default_iv": 0.20,
        "iv_hist_low": 0.12, "iv_hist_high": 0.45, "default_spot": 200,
    },
}

# Active instruments based on market selection
INSTRUMENTS = INSTRUMENTS_US if MARKET == "US" else INSTRUMENTS_INDIA

# ── Timeframes ───────────────────────────────────────────────
# All timeframes we store. Primary strategy runs on 5min.
TIMEFRAMES = ["1min", "3min", "5min", "15min", "1hour", "1day"]
PRIMARY_TF  = "5min"

# ── Session ──────────────────────────────────────────────────
# Market session times (24h format, local time)
SESSION_INDIA = {
    "market_open":     "09:15",
    "market_close":    "15:30",
    "pre_open":        "09:00",
    "first_candle_end": "09:20",
    "no_trade_after":  "15:15",
    "mcx_open":        "09:00",
    "mcx_close":       "23:30",
    "timezone":        "Asia/Kolkata",
}

SESSION_US = {
    "market_open":     "09:30",
    "market_close":    "16:00",
    "pre_open":        "09:00",
    "first_candle_end": "09:45",
    "no_trade_after":  "15:45",
    "timezone":        "America/New_York",
}

SESSION = SESSION_US if MARKET == "US" else SESSION_INDIA

# ── Risk-free rate ──────────────────────────────────────────
# Used in Black-Scholes pricing and cost-of-carry calculations
RISK_FREE_RATE = 0.0525 if MARKET == "US" else 0.065   # Fed rate / RBI repo rate

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
    # --- US market (paper trading / simulation) ---
    "us_paper": {
        "brokerage_per_order":    0.00,    # commission-free (like Robinhood, Schwab)
        "stt_pct_sell":           0.0,
        "exchange_txn_charge_pct": 0.0000051,   # SEC fee ~$5.10 per million
        "sebi_charges_pct":       0.0,
        "gst_pct":                0.0,
        "stamp_duty_pct_buy":     0.0,
    },
    # Slippage — conservative estimate
    "slippage_ticks": 1,           # assume 1 tick adverse fill per side
}

# ── Risk limits ──────────────────────────────────────────────
RISK = {
    "max_capital":             100000,   # total capital (USD or INR based on MARKET)
    "max_risk_per_trade_pct":  1.0,      # max 1% of capital per trade
    "max_daily_loss_pct":      3.0,      # halt bot if daily loss exceeds 3%
    "max_open_positions":      3,        # never hold more than 3 concurrent positions
    "max_trades_per_day":      15,       # circuit breaker on overtrading
    "min_rr_ratio":            1.5,      # reject any trade with R:R below 1.5
    "max_drawdown_pct":        10.0,     # halt if drawdown from peak exceeds 10%
    "trailing_stop_pct":       2.0,      # trailing stop at 2% from peak unrealized PnL
    "currency":                "USD" if MARKET == "US" else "INR",
}

# Backwards compat alias
RISK["max_capital_inr"] = RISK["max_capital"]

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
OPTIONS_INDIA = {
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

OPTIONS_US = {
    "enabled":              True,
    "symbols":              ["SPY", "QQQ", "AAPL"],
    "max_open_positions":   2,
    "max_daily_loss_pct":   3.0,
    "max_capital_pct":      30.0,
    "min_premium":          0.50,       # minimum option premium to trade ($)
    "min_iv_rank_sell":     55.0,
    "max_iv_rank_buy":      45.0,
    "target_exit_pct":      0.50,
    "stop_loss_pct":        2.0,
    "min_dte_sell":         2,
    "max_dte_sell":         30,         # US weeklies/monthlies available further out
    "min_dte_buy":          5,
    "chain_refresh_secs":   60,
}

OPTIONS = OPTIONS_US if MARKET == "US" else OPTIONS_INDIA
