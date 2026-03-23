# TradeBot — Deployment Log

> **Date:** March 22–23, 2026  
> **Status:** Deployed and live on Railway

---

## 1. GitHub Repository Setup

- Initialized git in `d:\AI\trade_bot`
- Created `.gitignore` (Python caches, `.env`, `.venv`, `*.zip`, IDE files, data dirs)
- Initial commit: 63 files, 9536 lines
- Pushed to: **https://github.com/roakeshk/tradebot**
- Branch: `main`
- Zip files excluded from repo

---

## 2. Railway Deployment

### Steps completed:
1. Installed Railway CLI: `npm install -g @railway/cli` (v4.33.0)
2. Logged in as: `roakesh.k@gmail.com`
3. Created Railway project: **tradebot**
4. Deployed from `tradebot_webapp_deploy/tradebot_webapp/`
5. Build: Nixpacks auto-detected Python 3.11, installed `flask` + `gunicorn`
6. Linked service and set environment variable

### Live URLs:
| Endpoint | URL |
|---|---|
| Dashboard | https://tradebot-production-c63c.up.railway.app/ |
| Health check | https://tradebot-production-c63c.up.railway.app/health |
| Setup guide | https://tradebot-production-c63c.up.railway.app/setup |
| Angel One callback | https://tradebot-production-c63c.up.railway.app/callback |
| API — metrics | https://tradebot-production-c63c.up.railway.app/api/metrics |
| API — trades | https://tradebot-production-c63c.up.railway.app/api/trades |
| API — equity | https://tradebot-production-c63c.up.railway.app/api/equity |
| API — logs | https://tradebot-production-c63c.up.railway.app/api/logs |
| API — status | https://tradebot-production-c63c.up.railway.app/api/status |

### Railway project dashboard:
https://railway.com/project/d8465bd8-5f1c-473f-a70c-b157e9c4931d

---

## 3. Environment Variable — TRADEBOT_KEY

- Set `TRADEBOT_KEY=tb_secret_2026` in Railway → Variables
- `settings.py` reads it from `os.environ.get("TRADEBOT_KEY", "")` — no secrets in git
- Created `.env.example` showing what variables to set locally
- Created `.env` locally with actual values (git-ignored)

---

## 4. HTTPS Fix — ProxyFix

- Added `werkzeug.middleware.proxy_fix.ProxyFix` to `webapp/app.py`
- Fixes `request.host_url` returning `http://` behind Railway's reverse proxy
- Callback URL now correctly shows `https://` on the setup page

---

## 5. Local Python Environment

- Created virtual environment: `d:\AI\trade_bot\tradebot_v5_final\tradebot\.venv\`
- Python version: 3.14.2
- Installed all dependencies from both `requirements.txt` files:
  - Flask 3.1.3, gunicorn 25.1.0
  - pandas 2.3.3, numpy 2.4.3
  - xgboost 3.2.0, scikit-learn, scipy, lightgbm
  - streamlit, requests, python-dotenv
  - yfinance, ta (technical analysis)

---

## 6. Files Created / Modified

| File | Action | Purpose |
|---|---|---|
| `.gitignore` | Created | Exclude caches, secrets, zips, venvs |
| `.env.example` | Created | Template for local environment variables |
| `.env` | Created | Local secrets (git-ignored) |
| `config/settings.py` | Modified | Added `import os`, `WEBAPP_KEY` and `WEBAPP_URL` from env vars |
| `webapp/app.py` (deploy) | Modified | Added `ProxyFix` for HTTPS behind Railway proxy |

### Git commits:
1. `220b99c` — Initial commit: TradeBot - complete trading system with webapp
2. `38aa0b3` — feat: read WEBAPP_KEY and WEBAPP_URL from env vars, add .env.example

---

## 7. Verification Results

All endpoints tested and confirmed working:
- `/health` → `{"status":"ok","time":"2026-03-22T12:02:40.781221"}`
- `/` → Dashboard loads with gate checks, metrics, equity curve, trade table, logs
- `/setup` → Setup guide with callback URL and step-by-step instructions
- `/callback` → Callback endpoint page (ready for Angel One)

---

## 8. What's Next

### Immediate (you need to do these):
1. **Angel One SmartAPI form** — go to smartapi.angelone.in → Add App:
   ```
   App Name:          TradeBot
   Redirect URL:      https://tradebot-production-c63c.up.railway.app/callback
   Post back URL:     (leave empty)
   Primary Static IP: [your static IP — see below]
   ```
2. **Enable TOTP** on Angel One mobile app → copy the base32 secret
3. **Fill `config/settings.py`** with your Angel One credentials (api_key, client_id, pin, totp_secret)
4. **Test connection:** `python generate_token_angel.py`

### Static IP (required by April 2026):
Angel One requires API orders from a registered static IP. Options:
| Option | Cost | Notes |
|---|---|---|
| Hetzner CX11 VPS | €3.79/month (~₹350) | Static IP included, run trading engine there |
| DigitalOcean Droplet | $6/month | Static IP included |
| ISP static IP | ₹200–500/month | Ask your broadband provider |
| VPN with dedicated IP | Varies | NordVPN, Mullvad, etc. |

The **web dashboard** (Railway) does NOT need a static IP — only the trading engine that places orders does.

### After broker setup:
1. `python setup.py` — bootstrap data
2. `python run_backtest.py --quick` — quick backtest on free data
3. `python main.py` — start paper trading
4. Wait for 200+ paper trades → train AI → go live

---

## Architecture Summary

```
Your PC (d:\AI\trade_bot)              Railway Cloud
┌─────────────────────┐          ┌──────────────────────────┐
│  main.py             │ ──POST──→│  webapp/app.py            │
│  (trading engine)    │          │  (Flask + gunicorn)       │
│  execution/engine.py │          │                           │
│  broker/angel_broker │          │  /api/push/trade          │
│                      │          │  /api/push/status         │
│  .env (secrets)      │          │  /api/push/log            │
│  config/settings.py  │          │                           │
└─────────────────────┘          │  / (dashboard)            │
                                  │  /callback (Angel One)    │
Phone / Browser ──────────────→  │  /setup (guide)           │
                                  │  /health                  │
                                  │                           │
                                  │  TRADEBOT_KEY (env var)   │
                                  │  SQLite DB (/tmp)         │
                                  └──────────────────────────┘
```
