# Tradebot — Autonomous Intraday Trading System

Dual-engine system targeting NSE BankNifty / Nifty futures and MCX commodities.
Built in phases — algo foundation first, AI layer second.

---

## Project structure

```
tradebot/
├── config/
│   └── settings.py          ← ALL configuration lives here
├── data/
│   ├── pipeline.py          ← Fetch, store, serve OHLCV data
│   ├── raw/                 ← Raw downloaded files
│   ├── processed/           ← Cleaned data
│   └── cache/               ← SQLite database lives here
├── broker/
│   ├── base.py              ← Abstract broker interface
│   ├── paper_broker.py      ← Paper trading (Phase 1–3)
│   ├── zerodha_broker.py    ← Zerodha Kite (Phase 4) [TODO]
│   └── shoonya_broker.py    ← Shoonya / Finvasia (Phase 5) [TODO]
├── risk/
│   └── cost_model.py        ← Exact cost calculation (brokerage, STT, GST...)
├── strategy/                ← Signal generators [Phase 2]
├── backtest/                ← Walk-forward backtester [Phase 2]
├── execution/               ← Order management [Phase 4]
├── monitor/                 ← Streamlit dashboard [Phase 4]
├── paper_trading/           ← Paper trade runner [Phase 3]
├── utils/
│   └── logger.py            ← Centralised logging
├── logs/                    ← Rotating log files
└── setup.py                 ← Run this first
```

---

## Phase 1 — Setup (you are here)

### Step 1: Open a Zerodha account
- Go to zerodha.com → Open account (Aadhaar-based, takes 1–2 days)
- After account opens, subscribe to Kite Connect API at kite.trade (₹2000/month)
- Get your api_key and api_secret from the developer console

### Step 2: Install and initialise
```bash
# Clone / copy the project
cd tradebot

# Run setup (installs packages + fetches initial data)
python setup.py
```

### Step 3: Set your broker credentials
Edit `config/settings.py`:
```python
ZERODHA = {
    "api_key":    "your_actual_key",
    "api_secret": "your_actual_secret",
    "user_id":    "your_user_id",
}
```

### Step 4: Fetch historical data
```bash
# Uses yfinance as fallback if broker not connected yet
python -m data.pipeline
```

### Step 5: Understand your costs
```bash
# See exact cost breakdown for your instruments
python -m risk.cost_model
```

---

## Cost model — what you MUST understand

BankNifty 1 lot at ₹48,000 (15 units, notional = ₹7.2 lakh):

| Cost component      | Per trade (one leg) | Round trip |
|---------------------|--------------------:|--------:|
| Brokerage (Zerodha) |              ₹20.00 |  ₹40.00 |
| STT (sell side)     |              ₹72.00 |  ₹72.00 |
| Exchange charge     |               ₹3.56 |   ₹7.12 |
| SEBI charge         |               ₹0.07 |   ₹0.14 |
| Stamp duty (buy)    |               ₹2.16 |   ₹2.16 |
| GST                 |               ₹4.24 |   ₹8.48 |
| Slippage (1 tick)   |               ₹0.75 |   ₹1.50 |
| **Total**           |           **₹102** | **₹131** |

**Breakeven points needed per trade: ~8.7 index points**

This means every signal must have at least 8–9 point expected profit
before it is worth executing. Strategies that produce 5-point average
wins are losers after costs. This is why cost modelling comes first.

With Shoonya (zero brokerage), round-trip drops to ~₹91 (saves ₹40).
At 5 trades/day × 250 days = 1250 trades/year, that's ₹50,000 in savings.

---

## Phase roadmap

| Phase | What we build              | Duration  | Gate to proceed         |
|-------|----------------------------|-----------|-------------------------|
| 1     | Data pipeline + cost model | 2 weeks   | Data flowing, costs modelled |
| 2     | Algo engine + backtester   | 4 weeks   | 55%+ win rate, 1.5R+ in WF backtest |
| 3     | Paper trading              | 8–12 weeks| <15% divergence from backtest |
| 4     | AI layer + live deployment | 4 weeks   | Paper trading passed    |
| 5     | Broker migration (Shoonya) | 1 week    | System stable 3+ months |

---

## Key rules

1. **Never skip paper trading.** A strategy that works in backtest but fails in paper trading
   is not ready. Period.

2. **Position sizing from risk, not conviction.** Risk 1% of capital per trade maximum.
   For ₹1,00,000 capital that's ₹1,000 max loss per trade.

3. **The kill switch is non-negotiable.** If daily loss hits 3%, the bot stops for the day.
   No overrides. No "just one more trade."

4. **Costs are not optional.** Every backtest must include the full cost model.
   A 60% win rate strategy that ignores costs can be a losing strategy in reality.

5. **Log everything.** Every order, every signal, every decision. You cannot improve
   what you cannot measure.
