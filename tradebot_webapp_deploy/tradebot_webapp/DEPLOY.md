# TradeBot Web App — Deployment Guide

## What this is

A standalone Flask web app that:
- Provides a **real public HTTPS URL** for the Angel One SmartAPI form
- Serves the **live trading dashboard** (accessible from phone / any browser)
- Receives **live data** pushed from your local trading engine
- Handles the **Angel One OAuth callback** if needed

## Option A — Railway (recommended, fastest)

Railway gives you a free HTTPS URL in under 5 minutes.

### Steps

```bash
# 1. Install Railway CLI
npm install -g @railway/cli        # needs Node.js
# OR on Mac: brew install railway

# 2. Login
railway login

# 3. From THIS folder (tradebot_webapp/)
railway init
# When asked: "Create new project?" → Yes
# Project name: tradebot

# 4. Deploy
railway up

# 5. Get your URL
railway open
# Your app is live at: https://tradebot-xxxx.railway.app
```

### Set environment variable

In the Railway dashboard → your project → Variables → Add:
```
TRADEBOT_KEY = any_secret_string_you_choose_eg_tb_secret_2026
```

Copy the same string into `config/settings.py` in your main tradebot project:
```python
WEBAPP_KEY = "same_string_here"
WEBAPP_URL = "https://tradebot-xxxx.railway.app"
```

### Your Angel One form values

```
Redirect URL: https://tradebot-xxxx.railway.app/callback
```

---

## Option B — Render (also free, no CLI needed)

### Steps

1. Create a free account at render.com
2. Click **New → Web Service**
3. Connect your GitHub account
4. Create a new GitHub repo called `tradebot-webapp`
5. Push this folder's contents to that repo:
   ```bash
   git init
   git add .
   git commit -m "TradeBot web app"
   git remote add origin https://github.com/YOUR_USERNAME/tradebot-webapp.git
   git push -u origin main
   ```
6. In Render: select that repo → it auto-detects Python → click Deploy
7. Your URL: `https://tradebot-webapp.onrender.com`

**Note:** Render free tier spins down after 15 minutes of inactivity.
The first request after that takes ~30 seconds. For trading use, upgrade to
the Starter plan ($7/month) or use Railway instead.

---

## Option C — Any VPS (DigitalOcean / Hetzner / AWS Lightsail)

Best option once you need a static IP for April 2026 requirements.
Hetzner CX11 = €3.79/month (~₹350), includes a static IP.

```bash
# On your VPS (Ubuntu 22.04)
git clone https://github.com/YOUR_USERNAME/tradebot-webapp.git
cd tradebot-webapp
pip install -r requirements.txt gunicorn

# Run with gunicorn
export TRADEBOT_KEY="your_secret"
export PORT=8080
gunicorn webapp.app:app --bind 0.0.0.0:8080 --workers 1 --threads 4 --daemon

# Set up nginx (optional, for HTTPS)
# Use certbot for free Let's Encrypt SSL
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `TRADEBOT_KEY` | Yes | Secret key — local engine uses this to push data |
| `PORT` | Auto-set by Railway/Render | Port to listen on |
| `DATA_DIR` | No (defaults to /tmp) | Where to store the SQLite database |

---

## How the local engine pushes data here

After deploying, the execution engine in your main tradebot project
will push trade data, status updates, and logs to this server.

This is already wired in `execution/engine.py` via `alerts/notifier.py`.
Just set these two values in `config/settings.py`:

```python
WEBAPP_URL = "https://tradebot-xxxx.railway.app"   # your Railway URL
WEBAPP_KEY = "same_key_as_TRADEBOT_KEY_env_var"    # must match
```

Data flow:
```
Your machine running main.py
    → POST /api/push/trade   (each completed trade)
    → POST /api/push/status  (engine status every 30s)
    → POST /api/push/log     (log lines)
         ↓
Railway/Render (this app)
    → stores in SQLite
    → serves to dashboard at /
    → accessible from phone/browser anywhere
```

---

## After deploying — fill the Angel One form

```
App Name:          TradeBot
Redirect URL:      https://YOUR-APP.railway.app/callback
Post back URL:     (leave empty)
Primary Static IP: [your VPS or static IP from ISP]
Secondary IP:      (leave empty)
```

**IMPORTANT (April 2026 requirement):**
Angel One now requires all API order requests to come from a registered
static IP. This means your `main.py` trading engine must run from a
static IP address. Options:
- Hetzner CX11 VPS: €3.79/month, static IP included
- DigitalOcean Droplet: $6/month, static IP included
- Ask your broadband ISP for a static IP: ₹200–500/month
- Run the trading engine on the same VPS as this web app

---

## Verify it's working

```bash
# Check health
curl https://YOUR-APP.railway.app/health

# Expected: {"status":"ok","time":"2026-03-22T..."}

# Open setup guide in browser
open https://YOUR-APP.railway.app/setup
```
