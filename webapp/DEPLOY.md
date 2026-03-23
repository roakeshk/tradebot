# TradeBot Web App — Deployment Guide

## Current deployment status

| Service | URL | Status |
|---|---|---|
| Railway dashboard | https://tradebot-production-c63c.up.railway.app/ | ✅ LIVE |
| Health check | https://tradebot-production-c63c.up.railway.app/health | ✅ Live |
| Angel One callback | https://tradebot-production-c63c.up.railway.app/callback | ✅ Ready |
| Setup guide | https://tradebot-production-c63c.up.railway.app/setup | ✅ Live |

**GitHub repo:** https://github.com/roakeshk/tradebot

---

## Railway is already deployed. To redeploy after changes:

```bash
# From tradebot_webapp/ folder
git add -A
git commit -m "update webapp"
git push  # Railway auto-deploys on push if GitHub connected
# OR
railway up  # manual deploy
```

---

## Environment variables (set in Railway → Variables)

| Variable | Value | Notes |
|---|---|---|
| `TRADEBOT_KEY` | `tb_secret_2026` | ✅ Already set |
| `DATA_DIR` | `/tmp` | Auto-set by render.yaml |

---

## Angel One SmartAPI form — exact values

```
App Name:          TradeBot
Redirect URL:      https://tradebot-production-c63c.up.railway.app/callback
Post back URL:     (leave empty)
Primary Static IP: [Oracle Cloud VM public IP — free forever]
Secondary IP:      (leave empty)
```

---

## Local .env file (for running on your machine)

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
# Edit .env with your Angel One credentials
```

The engine reads settings from `.env` automatically via python-dotenv.

---

## Architecture

```
Your PC / Oracle VM                    Railway Cloud
┌─────────────────────┐         ┌─────────────────────────────┐
│  main.py             │─ POST ─▶│  webapp/app.py (gunicorn)   │
│  execution/engine.py │         │  /api/push/trade            │
│  simulation/*.py     │         │  /api/push/status           │
│  .env (secrets)      │         │  /api/push/log              │
└─────────────────────┘         │                             │
                                 │  / (dashboard)              │
Phone / Browser ────────────────▶│  /callback (Angel One)      │
                                 │  /setup (guide)             │
                                 │  TRADEBOT_KEY = tb_secret.. │
                                 └─────────────────────────────┘
```

---

## How data flows to the Railway dashboard

The trading engine automatically pushes to Railway after every:
- Completed trade (futures or options)
- Engine status update (every bar)
- Log line (errors and key events)

This happens via `utils/railway_push.py` which reads `WEBAPP_URL` and
`TRADEBOT_KEY` from your `.env` file (or environment variables).

No manual action needed — just start `main.py` and watch the dashboard update.
