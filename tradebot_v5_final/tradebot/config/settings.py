# ============================================================
#  tradebot / config / settings.py
#  Central configuration — edit this file, nothing else
# ============================================================

from pathlib import Path

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
    "api_key":     "YOUR_API_KEY",
    "client_id":   "YOUR_CLIENT_ID",     # Angel One login ID
    "password":    "YOUR_PIN",            # 4-digit trading PIN
    "totp_secret": "YOUR_TOTP_SECRET",   # from Angel One app QR code (base32 string)
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
    "telegram_token":   "",             # "123456:ABC-DEF..."
    "telegram_chat_id": "",             # your chat ID (integer as string)
    # Email (optional)
    "smtp_host":        "",             # e.g. "smtp.gmail.com"
    "smtp_port":        465,
    "email_from":       "",
    "email_to":         "",
    "email_password":   "",             # app password (not main password)
}

# ── Logging ──────────────────────────────────────────────────
LOGGING = {
    "level":        "INFO",             # DEBUG | INFO | WARNING | ERROR
    "to_file":      True,
    "to_console":   True,
    "max_mb":       50,                 # rotate log file at 50 MB
    "backup_count": 5,
}
