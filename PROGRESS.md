# TradeBot — Complete Progress Tracker

> **Last updated:** March 2026  
> **Status:** Build 100% complete. Waiting for broker account confirmation to begin paper trading.

---

## Quick status

| Area | Status | Notes |
|---|---|---|
| Core foundation | ✅ Done | Data, costs, brokers, risk |
| Algo engine | ✅ Done | 3 strategies, walk-forward backtest |
| AI layer | ✅ Done | XGBoost Engine B, 300+ features |
| Live execution engine | ✅ Done | Connects everything end-to-end |
| Web app / dashboard | ✅ Done | Flask + Streamlit, Angel One callback |
| Alert system | ✅ Done | Telegram + email |
| Scheduler | ✅ Done | Daily token, data, weekly retrain |
| Reporting | ✅ Done | Daily/monthly P&L reports |
| Zerodha account | ⏳ Pending | Waiting for confirmation (1–2 days) |
| Shoonya account | ⏳ Pending | Waiting for confirmation (1–2 days) |
| Angel One SmartAPI app | ⏳ Pending | Fill form once accounts confirmed |
| Paper trading start | ⏳ Pending | Requires account + API setup |
| Backtest gate passed | ⏳ Pending | Needs real Angel One data |
| AI training | ⏳ Pending | Needs 200+ paper trades |
| Live trading | ⏳ Pending | Needs paper gate passed |

---

## All files built

### Phase 1 — Foundation

| File | Purpose | Status |
|---|---|---|
| `config/settings.py` | All config: brokers, costs, risk, alerts | ✅ Done |
| `data/pipeline.py` | Fetch / cache / serve OHLCV from SQLite | ✅ Done |
| `risk/cost_model.py` | Exact NSE F&O cost calculation (tested) | ✅ Done |
| `risk/manager.py` | Daily loss guard, position sizing | ✅ Done |
| `broker/base.py` | Abstract broker interface | ✅ Done |
| `broker/paper_broker.py` | Full simulation with slippage + costs | ✅ Done |
| `broker/zerodha_broker.py` | Kite Connect adapter | ✅ Done |
| `broker/shoonya_broker.py` | Zero-brokerage adapter | ✅ Done |
| `broker/fyers_broker.py` | Free API — data + execution | ✅ Done |
| `broker/angel_broker.py` | Free API — best MCX data, TOTP auto-login | ✅ Done |
| `broker/factory.py` | `get_broker()` + `get_data_source()` | ✅ Done |
| `utils/logger.py` | Rotating file + console logging | ✅ Done |
| `setup.py` | One-command install + data bootstrap | ✅ Done |
| `generate_token.py` | Zerodha browser-based daily token | ✅ Done |
| `generate_token_angel.py` | Angel One TOTP auto-login (cron-safe) | ✅ Done |
| `generate_token_fyers.py` | Fyers browser-based daily token | ✅ Done |

### Phase 2 — Algo engine

| File | Purpose | Status |
|---|---|---|
| `strategy/indicators.py` | 20 indicators — EMA, VWAP, RSI, ATR, BB, CVD, OBV… | ✅ Done |
| `strategy/regime.py` | Trending / ranging / high-vol classifier | ✅ Done |
| `strategy/base_strategy.py` | Signal dataclass + R:R validation | ✅ Done |
| `strategy/strategies.py` | VWAPReversion + OpeningRangeBreakout + EMATrend | ✅ Done |
| `backtest/engine.py` | Bar-by-bar walk-forward + cost model + gate check | ✅ Done |
| `run_backtest.py` | One-command full walk-forward analysis | ✅ Done |

### Phase 3 — Paper trading

| File | Purpose | Status |
|---|---|---|
| `paper_trading/runner.py` | Live paper trading loop | ✅ Done |
| `monitor/dashboard.py` | Streamlit live dashboard | ✅ Done |

### Phase 4 — AI + live execution + infra

| File | Purpose | Status |
|---|---|---|
| `ai/features.py` | 300+ engineered features, no lookahead | ✅ Done |
| `ai/classifier.py` | XGBoost per strategy, OOS evaluation | ✅ Done |
| `ai/retrain.py` | Weekly retraining pipeline | ✅ Done |
| `execution/engine.py` | Engine A + B + Risk + Broker in one loop | ✅ Done |
| `alerts/notifier.py` | Telegram + email notifications | ✅ Done |
| `scheduler/tasks.py` | Daily token refresh, data, retrain, summary | ✅ Done |
| `reporting/reports.py` | Daily/monthly P&L reports | ✅ Done |
| `webapp/app.py` | Flask dashboard + Angel One callback endpoint | ✅ Done |
| `main.py` | Single entry point for the whole system | ✅ Done |

---

## What needs to happen next — in exact order

### Step 1 — Angel One SmartAPI form (do this now, takes 15 min)

The form you're looking at asks for:

```
App Name:          TradeBot
Redirect URL:      http://localhost:5000/callback
Post back URL:     (leave empty)
Primary Static IP: [your public IP — see below how to find it]
Secondary IP:      (leave empty)
```

**To find your public IP:**
- Open a terminal → type: `curl ifconfig.me` → copy the result
- OR open this after starting the web app: `http://localhost:5000/setup-guide`

**Why localhost works:**
For personal TOTP-based login, Angel One never actually calls the Redirect URL.
It's only used in OAuth flows for commercial apps with multiple users.
Your login happens directly via `generate_token_angel.py` using your TOTP pin.
The URL just needs to be filled with something valid.

**After clicking Add:**
- You'll see your API Key on screen — copy it immediately
- Also note your Client ID (your Angel One login ID)

---

### Step 2 — Enable TOTP on Angel One app

```
1. Open Angel One app on phone
2. Go to: My Profile → Security → Enable TOTP
3. A QR code will appear — DO NOT scan it yet
4. Tap "Can't scan?" or "Show secret key"
5. Copy the base32 string shown (e.g. JBSWY3DPEHPK3PXP...)
6. Save this somewhere safe — it's your totp_secret
7. NOW scan the QR with Google Authenticator (to verify TOTP works)
```

---

### Step 3 — Fill settings.py

Open `config/settings.py` and fill:

```python
ANGEL_ONE = {
    "api_key":     "paste_your_api_key_here",
    "client_id":   "your_angel_one_login_id",
    "password":    "your_4_digit_trading_pin",
    "totp_secret": "your_base32_secret_from_step_2",
}
DATA_SOURCE = "angel"
ACTIVE_BROKER = "paper"   # keep as paper until gate is passed
```

Also fill Telegram alerts (optional but recommended):

```python
ALERTS = {
    "enabled":          True,
    "telegram_token":   "your_bot_token_from_botfather",
    "telegram_chat_id": "your_chat_id",
    ...
}
```

How to create Telegram bot (5 minutes):
```
1. Open Telegram → search @BotFather → /start → /newbot
2. Give it a name (e.g. "TradeBot Alerts")
3. Copy the token it gives you (e.g. 123456:ABC-DEF...)
4. Message your new bot once (so a chat exists)
5. Open: https://api.telegram.org/bot<TOKEN>/getUpdates
6. Copy the "id" number from "chat" section — that's your chat_id
```

---

### Step 4 — Setup and test (no market hours needed)

```bash
# Install all packages + fetch free data
python setup.py

# Test Angel One connection
python generate_token_angel.py
# Expected output: "Logged in as: YOUR NAME"

# Start the web app (open http://localhost:5000 in browser)
python -m webapp.app

# Run backtest on free yfinance data first (partial results)
python run_backtest.py --quick
```

---

### Step 5 — Fetch real data (do this after Angel One is connected)

```bash
# Fetch 3 years of real BankNifty futures data (free via Angel One)
python -m scheduler.tasks --task data

# Re-run full walk-forward backtest on real data
python run_backtest.py

# Check which strategies PASS the gate
```

**Gate criteria (all must pass to proceed):**
- Win rate ≥ 55% (out-of-sample)
- Profit factor ≥ 1.4
- Max drawdown < ₹12,000 per lot
- Minimum 200 trades in the OOS windows

---

### Step 6 — Start paper trading (market hours: 9:15–15:30 IST)

```bash
# Terminal 1: Start engine in paper mode
python main.py

# Terminal 2: Web dashboard
python -m webapp.app
# Open http://localhost:5000

# Terminal 3: (optional) Streamlit dashboard
streamlit run monitor/dashboard.py
```

**Add to cron for daily automation (Mac/Linux):**
```bash
# Edit crontab: crontab -e
0 8 * * 1-5   cd /path/to/tradebot && python generate_token_angel.py
30 8 * * 1-5  cd /path/to/tradebot && python -m scheduler.tasks --task data
0 9 * * 1-5   cd /path/to/tradebot && python main.py
0 16 * * 1-5  cd /path/to/tradebot && python -m scheduler.tasks --task summary
0 20 * * 0    cd /path/to/tradebot && python -m scheduler.tasks --task retrain
```

---

### Step 7 — Paper trading for 3 months

Check weekly:
- Dashboard at http://localhost:5000
- Run: `python -m reporting.reports --period week`
- Monitor divergence from backtest (must stay < 15%)

When all 4 gate conditions are green on the dashboard → proceed to Step 8.

---

### Step 8 — Train AI classifiers (after 200+ paper trades)

```bash
python -m ai.retrain --symbol BANKNIFTY
# Expected: 2–3 classifiers pass (accuracy ≥52%, AUC ≥55%)
```

---

### Step 9 — Go live (only after gate passes)

```bash
# 1. Set in config/settings.py:
ACTIVE_BROKER = "zerodha"   # or "shoonya"

# 2. Start with --confirm flag (safety check)
python main.py --mode live --confirm

# 3. First week: 1 lot only, watch every trade
# 4. After 30 days live proof: gradually increase size
```

---

## Cost summary — what you pay

| Item | Cost |
|---|---|
| Angel One account | Free |
| Angel One SmartAPI | Free |
| Fyers API | Free |
| Zerodha account | Free |
| Zerodha execution API | Free (since April 2025) |
| Zerodha data API | ₹2,000/month (we skip this — use Angel One instead) |
| Shoonya account | Free |
| Shoonya API | Free |
| **Total monthly running cost** | **₹0** |
| Zerodha brokerage per F&O trade | ₹20/order |
| Shoonya brokerage per F&O trade | ₹0 (zero) |
| BankNifty 1 lot round-trip (Zerodha) | ~₹228 |
| BankNifty 1 lot round-trip (Shoonya) | ~₹181 |

---

## Performance gates — what "ready" looks like

### Backtest gate (must pass before paper trading)
- [ ] Win rate ≥ 55% on OOS data
- [ ] Profit factor ≥ 1.4
- [ ] Max drawdown < ₹12,000 per lot
- [ ] Minimum 200 OOS trades

### Paper trading gate (must pass before live capital)
- [ ] All backtest gates above, on live paper data
- [ ] < 15% divergence from backtest win rate
- [ ] < 15% divergence from backtest profit factor
- [ ] System runs unattended 5+ days without errors
- [ ] No single-day loss > 3% of capital
- [ ] 200+ paper trades completed

### AI gate (must pass before Engine B activates)
- [ ] OOS accuracy ≥ 52%
- [ ] OOS AUC ≥ 0.55
- [ ] At least 2 of 3 classifiers pass

### Live scaling gate (before increasing position size)
- [ ] 30 consecutive calendar days live
- [ ] Live win rate within 5pp of paper trading win rate
- [ ] No system errors or missed trades in 30 days

---

## Realistic targets

| Metric | Minimum | Good | Excellent |
|---|---|---|---|
| Win rate | 55% | 58–62% | 65%+ |
| Profit factor | 1.4 | 1.6–1.9 | 2.0+ |
| Avg R:R | 1.2:1 | 1.5–1.8:1 | 2.0+:1 |
| Monthly return (₹1L) | ₹2,000–4,000 | ₹5,000–8,000 | ₹10,000+ |
| Sharpe ratio | 0.8 | 1.2–1.5 | 1.8+ |

---

## Key commands — cheat sheet

```bash
# First time setup
python setup.py

# Angel One token (run every morning at 8 AM)
python generate_token_angel.py

# Fetch latest market data
python -m scheduler.tasks --task data

# Backtest (quick single-pass)
python run_backtest.py --quick

# Backtest (full walk-forward, takes ~3 min)
python run_backtest.py

# Start paper trading
python main.py

# Start web dashboard (http://localhost:5000)
python -m webapp.app

# Start Streamlit dashboard
streamlit run monitor/dashboard.py

# P&L report
python -m reporting.reports --period today
python -m reporting.reports --period month

# Train AI (after 200+ paper trades)
python -m ai.retrain

# Go live (after gate passes)
python main.py --mode live --confirm
```

---

*This file is the single source of truth for project progress. Update checkboxes as tasks complete.*

---

## Web app hosting — Angel One callback URL

Angel One requires a **public HTTPS URL** (not localhost, not HTTP).

### Deploy the webapp (do this first)

Use the separate `tradebot_webapp/` folder — it's a standalone deployable app.

**Railway (fastest — 5 min):**
```bash
cd tradebot_webapp/
npm install -g @railway/cli
railway login
railway init
railway up
# URL: https://tradebot-xxxx.railway.app
```

**Render (no CLI needed):**
- Push `tradebot_webapp/` to a GitHub repo
- Connect on render.com → auto-deploys
- URL: `https://tradebot-xxxx.onrender.com`

### After deploying, fill Angel One form:
```
Redirect URL:      https://YOUR-APP.railway.app/callback
Primary Static IP: [your VPS or ISP static IP]
```

### Set in config/settings.py:
```python
WEBAPP_URL = "https://YOUR-APP.railway.app"
WEBAPP_KEY = "same_as_TRADEBOT_KEY_env_var"
```

### Static IP requirement (April 2026)
Angel One now requires API orders from a registered static IP.
Cheapest option: Hetzner CX11 VPS = €3.79/month (~₹350) includes static IP.

---

## Hosting — completely free solution

### Problem 1: HTTPS callback URL → PythonAnywhere (free forever)

1. Sign up at pythonanywhere.com (free, no card)
2. Web tab → Add new web app → Flask → Python 3.11
3. Open flask_app.py in editor → delete everything → paste webapp/app.py contents
4. Save → Reload
5. Your URL: `https://YOURNAME.pythonanywhere.com`
6. Add env var: `TRADEBOT_KEY = any_secret`

**Use in Angel One form:**
```
Redirect URL: https://YOURNAME.pythonanywhere.com/callback
```

### Problem 2: Static IP for trading engine → Oracle Cloud (free forever)

1. Sign up at cloud.oracle.com (card needed for verification, nothing charged)
2. Compute → Instances → Create → Shape: VM.Standard.E2.1.Micro (Always Free)
3. Region: Mumbai or Hyderabad
4. Note the Public IP shown after VM starts

**Use in Angel One form:**
```
Primary Static IP: [Oracle VM Public IP]
```

5. SSH into VM, copy tradebot/ folder, run:
   ```bash
   pip3 install -r requirements.txt
   python3 setup.py
   python3 main.py   # or use screen/nohup for persistence
   ```

### Total monthly cost: ₹0
- PythonAnywhere: free dashboard + HTTPS callback URL
- Oracle Cloud VM: free static IP + trading engine host
- Angel One API: free data
- Zerodha: free execution (or Shoonya: free + zero brokerage)
