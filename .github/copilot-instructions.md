# Project Guidelines

## Overview

**TradeBot** — Autonomous intraday trading system for NSE/MCX futures and options.  
Dual-engine architecture: Engine A (rule-based strategies) + Engine B (XGBoost AI classifier).  
Deployed to Railway (Flask web dashboard) with local Python trading engine.

Two top-level packages:
- `tradebot/` — Core trading engine (brokers, strategies, execution, AI, options, simulation)
- `webapp/` — Flask dashboard deployed on Railway (`https://tradebot-production-c63c.up.railway.app`)

## Code Style

- **Python 3.11+** (Railway runs 3.11; local dev uses 3.14)
- **Imports**: stdlib → third-party → local. Local uses relative paths (`from strategy.base_strategy import ...`)
- **Naming**: `PascalCase` classes, `snake_case` functions, `UPPER_SNAKE_CASE` constants, `_private` prefix for internal methods
- **Dataclasses** for data objects (`Signal`, `Order`, `Position`, `Trade`)
- **Error handling**: try/except with `logger.error()` — never silent failures. Alert/notification errors must never crash the trading engine.
- **Type hints** on all new public functions and method signatures

## Architecture

- `config/settings.py` is the **single source of truth** for all configuration. Never hardcode values in business logic.
- **Secrets** come from environment variables via `os.environ.get()` with safe defaults. The `.env` file (git-ignored) feeds `settings.py` through `python-dotenv`.
- **Broker integration** uses the **Factory pattern** (`broker/factory.py`). Never import a specific broker directly — always use `get_broker()` / `get_data_source()`.
- **Strategies** inherit from `StrategyBase` (`strategy/base_strategy.py`). Each must implement `generate_signals()` and return `Signal` dataclasses.
- **Risk gates** are mandatory — no position opens without passing `RiskManager.approve_signal()`.
- `execution/engine.py` orchestrates: data → indicators → regime → signals → AI filter → risk → broker → alerts.

## Build and Test

```bash
# Install dependencies (use venv)
python -m venv .venv && .venv\Scripts\activate
pip install -r tradebot/requirements.txt

# Run in paper mode (default, safe)
cd tradebot && python main.py --mode paper

# Walk-forward backtest
python run_backtest.py --symbol BANKNIFTY --quick

# Fetch market data
python -m scheduler.tasks --task data

# Daily token refresh (Angel One TOTP)
python generate_token_angel.py

# Start webapp locally
cd webapp && pip install -r requirements.txt && python -m webapp.app
```

There are no unit tests yet — validation is via walk-forward backtesting only.

## Conventions

- **7 execution modes** in `main.py`: `paper`, `live`, `simulate`, `backtest`, `options-sim`, `options-paper`, `options-backtest`
- **Live mode requires `--confirm` flag** — safety gate against accidental real-money trading
- `ACTIVE_BROKER = "paper"` must remain default in committed code — only override via `.env`
- All trading decisions flow through `RiskManager` — max daily loss, position sizing, R:R ratio checks
- The webapp pushes data via `POST /api/push/*` endpoints authenticated with `X-API-Key` header matching `TRADEBOT_KEY`
- **Options module** (`options/`) uses Black-Scholes pricing and Greeks — see `options/pricing.py`
- **Simulation module** (`simulation/`) replays historical bars at configurable speed

## Key Files

| File | Purpose |
|---|---|
| `tradebot/config/settings.py` | All config — brokers, instruments, risk, costs, alerts |
| `tradebot/execution/engine.py` | Main orchestration loop |
| `tradebot/broker/base.py` | Abstract broker interface (8 abstract methods) |
| `tradebot/broker/factory.py` | Broker/data-source factory |
| `tradebot/strategy/base_strategy.py` | Strategy base class + Signal dataclass |
| `tradebot/main.py` | CLI entry point (argparse, 7 modes) |
| `webapp/webapp/app.py` | Flask app with ProxyFix for Railway HTTPS |
| `.env.example` | Environment variable template |

## Security

### Secrets Management — CRITICAL

- **All secrets MUST live in `.env` (git-ignored) or environment variables.** Never write real API keys, passwords, TOTP secrets, or tokens into any committed file.
- `config/settings.py` reads secrets via `os.environ.get()` only. Placeholder defaults like `"YOUR_API_KEY"` are acceptable; real values are not.
- `.env.example` shows structure with dummy values — never populate it with real credentials.

### Exposed Key Protocol — HARD STOP

If you detect **any real secret** (API key, password, TOTP secret, token, or `TRADEBOT_KEY`) in a file that is or could be committed to git:

1. **STOP immediately.** Do not proceed with any other task.
2. **Alert the user** with the exact file, line, and key type exposed.
3. **Instruct the user to:**
   - Revoke / regenerate the exposed key at the provider (Angel One, Fyers, Zerodha, Telegram, Railway, etc.)
   - Remove the secret from the file and replace with a placeholder or `os.environ.get()` call
   - Run `git rm --cached <file>` if it was already staged/committed, then force-push
4. **Do not continue** with any code generation, deployment, or commit until the user confirms the key has been regenerated and the file is clean.

### What Counts as a Real Secret

- Angel One: `api_key`, `client_id`, `totp_secret`, `password` (PIN) — if they don't look like `YOUR_*` or `changeme`
- Fyers: `client_id`, `secret_key` — if not placeholder
- Zerodha: `api_key`, `api_secret` — if not placeholder
- Telegram: `telegram_token` (format `123456:ABC-...`), `telegram_chat_id` (numeric)
- Railway / webapp: `TRADEBOT_KEY` — if not placeholder like `changeme` or empty
- Any string that looks like a real token, hash, or base32-encoded secret (e.g., 20+ alphanumeric chars)

### Pre-Commit Checks

Before every `git add` or `git commit`:
- Scan all staged files for patterns matching real secrets (API keys, tokens, base32 strings)
- If `.env` is accidentally staged, **block the commit** and unstage it
- Ensure `.env` and `.env.*` (except `.env.example`) remain in `.gitignore`

### Webapp Authentication

- All `/api/push/*` endpoints require `X-API-Key` header matching `TRADEBOT_KEY`
- The `_check_key()` function in `webapp/app.py` must never use a hardcoded fallback that leaks into production — always read from `os.environ.get("TRADEBOT_KEY")`
- Railway environment variables are the source of truth for deployed secrets
