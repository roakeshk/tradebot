"""
TradeBot Relay — Railway Dashboard
Standalone Flask app. No tradebot deps. Receives pushes from the engine,
stores in SQLite, serves an institutional-grade live dashboard with
world market status and built-in simulation engine.

Env vars: MARKET (US|INDIA), TRADEBOT_KEY, MAX_DD, RISK_CAPITAL, etc.
"""
import json, os, sqlite3, math, random, time, threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, render_template_string, request
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# ── Market-aware config ───────────────────────────────────────
_MARKET   = os.environ.get("MARKET", "US")
_CUR      = "$"                if _MARKET == "US" else "\u20b9"
_LOCALE   = "en-US"            if _MARKET == "US" else "en-IN"
_TZ_LABEL = "ET"               if _MARKET == "US" else "IST"
_JS_TZ    = "America/New_York" if _MARKET == "US" else "Asia/Kolkata"

# Risk guardrail defaults (mirrors config/settings.py RISK dict)
_RISK_CAPITAL       = float(os.environ.get("RISK_CAPITAL", "100000"))
_MAX_DAILY_LOSS_PCT = float(os.environ.get("MAX_DAILY_LOSS_PCT", "3.0"))
_MAX_DD_PCT         = float(os.environ.get("MAX_DD_PCT", "10.0"))
_MAX_DD             = float(os.environ.get("MAX_DD", str(_RISK_CAPITAL * _MAX_DD_PCT / 100)))
_MAX_DD_K           = f"{_CUR}{_MAX_DD/1000:.0f}k"
_MAX_POSITIONS      = int(os.environ.get("MAX_POSITIONS", "3"))
_MAX_TRADES_DAY     = int(os.environ.get("MAX_TRADES_DAY", "15"))
_MIN_RR             = float(os.environ.get("MIN_RR", "1.5"))

# JS fmt() function — US: millions/thousands; India: lakhs
if _MARKET == "US":
    _FMT_JS = (
        r"const fmt=v=>{if(v==null||isNaN(v))return'CUR0';const a=Math.abs(v),"
        r"s=a>=1e6?'CUR'+(a/1e6).toFixed(2)+'M':"
        r"a>=1e3?'CUR'+(a/1e3).toFixed(1)+'k':'CUR'+Math.round(a).toLocaleString('LOCALE');"
        r"return v<0?'-'+s:s};"
    ).replace("CUR", _CUR).replace("LOCALE", _LOCALE)
else:
    _FMT_JS = (
        r"const fmt=v=>{if(v==null||isNaN(v))return'CUR0';const a=Math.abs(v),"
        r"s=a>=1e5?'CUR'+(a/1e5).toFixed(1)+'L':'CUR'+Math.round(a).toLocaleString('LOCALE');"
        r"return v<0?'-'+s:s};"
    ).replace("CUR", _CUR).replace("LOCALE", _LOCALE)

# JS makeFmt() factory — used when user switches market in the UI
_FMT_FACTORY_JS = r"""function makeFmt(cur,loc){return function(v){if(v==null||isNaN(v))return cur+'0';var a=Math.abs(v),s=a>=1e6?cur+(a/1e6).toFixed(2)+'M':a>=1e3?cur+(a/1e3).toFixed(1)+'k':cur+Math.round(a).toLocaleString(loc);return v<0?'-'+s:s};}"""

RAILWAY_URL = os.environ.get("WEBAPP_URL", "https://tradebot-production-c63c.up.railway.app")
DB_PATH = Path(os.environ.get("DATA_DIR", "/tmp")) / "tradebot_web.db"

# ── World Markets ─────────────────────────────────────────────
WORLD_MARKETS = {
    "US": {
        "name": "NYSE / NASDAQ", "flag": "\U0001f1fa\U0001f1f8", "index": "S&P 500",
        "currency": "$", "locale": "en-US", "tz": "America/New_York", "tz_label": "ET",
        "open": (9, 30), "close": (16, 0), "lunch": None,
        "instruments": [
            {"s": "SPY", "p": 520}, {"s": "QQQ", "p": 440}, {"s": "AAPL", "p": 190},
            {"s": "MSFT", "p": 420}, {"s": "TSLA", "p": 250}, {"s": "NVDA", "p": 130},
            {"s": "AMZN", "p": 190}, {"s": "GOOGL", "p": 165}, {"s": "META", "p": 500},
            {"s": "JPM", "p": 200},
        ],
    },
    "UK": {
        "name": "London SE", "flag": "\U0001f1ec\U0001f1e7", "index": "FTSE 100",
        "currency": "\u00a3", "locale": "en-GB", "tz": "Europe/London", "tz_label": "GMT",
        "open": (8, 0), "close": (16, 30), "lunch": None,
        "instruments": [
            {"s": "SHEL", "p": 2700}, {"s": "AZN", "p": 11500}, {"s": "HSBA", "p": 640},
            {"s": "BP", "p": 480}, {"s": "RIO", "p": 5200}, {"s": "ULVR", "p": 4200},
        ],
    },
    "INDIA": {
        "name": "NSE India", "flag": "\U0001f1ee\U0001f1f3", "index": "NIFTY 50",
        "currency": "\u20b9", "locale": "en-IN", "tz": "Asia/Kolkata", "tz_label": "IST",
        "open": (9, 15), "close": (15, 30), "lunch": None,
        "instruments": [
            {"s": "RELIANCE", "p": 2500}, {"s": "TCS", "p": 3800}, {"s": "INFY", "p": 1500},
            {"s": "HDFCBANK", "p": 1600}, {"s": "ITC", "p": 440}, {"s": "SBIN", "p": 780},
        ],
    },
    "JAPAN": {
        "name": "Tokyo SE", "flag": "\U0001f1ef\U0001f1f5", "index": "Nikkei 225",
        "currency": "\u00a5", "locale": "ja-JP", "tz": "Asia/Tokyo", "tz_label": "JST",
        "open": (9, 0), "close": (15, 0), "lunch": ((11, 30), (12, 30)),
        "instruments": [
            {"s": "7203.T", "p": 2800}, {"s": "6758.T", "p": 13000}, {"s": "9984.T", "p": 8500},
            {"s": "6861.T", "p": 52000}, {"s": "8306.T", "p": 1400},
        ],
    },
    "CHINA": {
        "name": "Shanghai SE", "flag": "\U0001f1e8\U0001f1f3", "index": "SSE Composite",
        "currency": "\u00a5", "locale": "zh-CN", "tz": "Asia/Shanghai", "tz_label": "CST",
        "open": (9, 30), "close": (15, 0), "lunch": ((11, 30), (13, 0)),
        "instruments": [
            {"s": "600519", "p": 1700}, {"s": "601318", "p": 48}, {"s": "600036", "p": 35},
            {"s": "601888", "p": 70}, {"s": "600276", "p": 25},
        ],
    },
    "AUSTRALIA": {
        "name": "ASX", "flag": "\U0001f1e6\U0001f1fa", "index": "ASX 200",
        "currency": "A$", "locale": "en-AU", "tz": "Australia/Sydney", "tz_label": "AEST",
        "open": (10, 0), "close": (16, 0), "lunch": None,
        "instruments": [
            {"s": "BHP", "p": 46}, {"s": "CBA", "p": 120}, {"s": "CSL", "p": 280},
            {"s": "WBC", "p": 26}, {"s": "NAB", "p": 35},
        ],
    },
}


def _market_status(mkt_id):
    """Return (status, local_time_str) for a market."""
    m = WORLD_MARKETS[mkt_id]
    tz = ZoneInfo(m["tz"])
    now = datetime.now(tz)
    t = now.hour * 60 + now.minute
    o = m["open"][0] * 60 + m["open"][1]
    c = m["close"][0] * 60 + m["close"][1]
    lt = now.strftime("%-I:%M %p ") + m["tz_label"] if os.name != "nt" else now.strftime("%#I:%M %p ") + m["tz_label"]

    # Weekend check
    if now.weekday() >= 5:
        return "closed", lt

    # Lunch break check
    if m["lunch"]:
        ls = m["lunch"][0][0] * 60 + m["lunch"][0][1]
        le = m["lunch"][1][0] * 60 + m["lunch"][1][1]
        if ls <= t < le:
            return "lunch", lt

    if o <= t < c:
        return "open", lt
    return "closed", lt


# ── Database layer ────────────────────────────────────────────
def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def _init_db():
    with _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, strategy TEXT, direction TEXT,
            entry_price REAL, exit_price REAL, net_pnl REAL,
            exit_reason TEXT, entry_time TEXT, lots INTEGER, source TEXT);
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, level TEXT, message TEXT);
        """)
_init_db()

def kv_set(key, val):
    with _db() as c:
        c.execute("INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                  (key, json.dumps(val), datetime.now().isoformat()))

def kv_get(key, default=None):
    with _db() as c:
        r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

def _check_key():
    k = request.headers.get("X-API-Key", "")
    expected = os.environ.get("TRADEBOT_KEY", "")
    return bool(expected) and k == expected


# ── Demo Simulator ────────────────────────────────────────────
class DemoSimulator(threading.Thread):
    """Background thread that generates realistic trades via GBM."""

    def __init__(self, market_id="US", speed=5):
        super().__init__(daemon=True)
        self.market_id = market_id
        self.speed = max(1, speed)
        self._stop_event = threading.Event()
        mkt = WORLD_MARKETS.get(market_id, WORLD_MARKETS["US"])
        self.instruments = mkt["instruments"]
        self.currency = mkt["currency"]
        self.capital = _RISK_CAPITAL
        self.daily_pnl = 0.0
        self.trades_count = 0
        self.open_positions = 0
        self.started_at = None
        self.strategies = ["momentum", "mean_rev", "breakout", "trend"]
        self.exit_reasons = ["target", "stop", "trail", "signal"]

    def stop(self):
        self._stop_event.set()

    @property
    def running(self):
        return self.is_alive() and not self._stop_event.is_set()

    def run(self):
        self.started_at = datetime.now().isoformat()
        mu, sigma = 0.0001, 0.02

        while not self._stop_event.is_set():
            # Pick a random instrument
            inst = random.choice(self.instruments)
            sym = inst["s"]
            base_price = inst["p"]

            # GBM entry price (slight random walk from typical)
            entry = base_price * math.exp(random.gauss(0, 0.01))
            direction = random.choice(["LONG", "SHORT"])
            strategy = random.choice(self.strategies)
            lots = random.randint(1, 5)

            # Update engine status: position open
            self.open_positions += 1
            self._push_status()

            # Hold for 3-8 bars
            hold_bars = random.randint(3, 8)
            price = entry
            for _ in range(hold_bars):
                if self._stop_event.is_set():
                    break
                self._stop_event.wait(self.speed)
                dt = 1.0 / 252
                price *= math.exp((mu - 0.5 * sigma**2) * dt + sigma * math.sqrt(dt) * random.gauss(0, 1))

            if self._stop_event.is_set():
                self.open_positions = max(0, self.open_positions - 1)
                break

            exit_price = price
            if direction == "LONG":
                pnl = (exit_price - entry) * lots * 100
            else:
                pnl = (entry - exit_price) * lots * 100
            pnl = round(pnl, 2)

            exit_reason = random.choice(self.exit_reasons)
            entry_time = datetime.now().isoformat()

            # Write trade to SQLite
            with _db() as c:
                c.execute(
                    "INSERT INTO trades(symbol,strategy,direction,entry_price,"
                    "exit_price,net_pnl,exit_reason,entry_time,lots,source) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (sym, strategy, direction, round(entry, 2), round(exit_price, 2),
                     pnl, exit_reason, entry_time, lots, "simulation"),
                )
                # Log the trade
                msg = f"SIM {direction} {sym} x{lots} | entry={self.currency}{entry:.2f} exit={self.currency}{exit_price:.2f} pnl={pnl:+.2f} ({exit_reason})"
                c.execute("INSERT INTO logs(ts,level,message) VALUES(?,?,?)",
                          (datetime.now().isoformat(), "INFO", msg))

            self.capital += pnl
            self.daily_pnl += pnl
            self.trades_count += 1
            self.open_positions = max(0, self.open_positions - 1)
            self._push_status()

            # Brief pause between trades
            self._stop_event.wait(self.speed * 0.5)

    def _push_status(self):
        kv_set("engine_status", {
            "running": True,
            "halted": False,
            "capital": round(self.capital, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "open_positions": self.open_positions,
            "trades_today": self.trades_count,
            "max_daily_loss": 0,
            "updated_at": datetime.now().isoformat(),
            "mode": "simulation",
            "market": self.market_id,
        })

    def status_dict(self):
        return {
            "running": self.running,
            "market": self.market_id,
            "speed": self.speed,
            "trades": self.trades_count,
            "capital": round(self.capital, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "started_at": self.started_at,
            "elapsed": str(timedelta(seconds=int(time.time() - time.mktime(
                datetime.fromisoformat(self.started_at).timetuple())))) if self.started_at else "0:00:00",
        }


_simulator = None
_sim_lock = threading.Lock()


# ── Public routes ─────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML,
        CUR=_CUR, LOCALE=_LOCALE, JS_TZ=_JS_TZ, TZ_LABEL=_TZ_LABEL,
        MAX_DD_K=_MAX_DD_K, FMT_JS=_FMT_JS, FMT_FACTORY_JS=_FMT_FACTORY_JS,
        MARKET=_MARKET,
        RISK_CAPITAL=_RISK_CAPITAL, MAX_DAILY_LOSS_PCT=_MAX_DAILY_LOSS_PCT,
        MAX_DD_PCT=_MAX_DD_PCT, MAX_POSITIONS=_MAX_POSITIONS,
        MAX_TRADES_DAY=_MAX_TRADES_DAY, MIN_RR=_MIN_RR)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

@app.route("/callback")
def callback():
    auth = request.args.get("auth_token", "") or request.args.get("token", "")
    src  = request.args.get("source", "broker")
    if auth:
        kv_set("broker_auth_token", auth)
        kv_set("broker_token_time", datetime.now().isoformat())
        kv_set("broker_token_source", src)
        return ("<html><body style='background:#0b0e11;color:#26a69a;"
                "font-family:sans-serif;padding:40px'>"
                f"<h2>&#10003; Connected ({src})</h2>"
                "<p style='color:#787b86'>Token saved. Close this tab.</p>"
                "</body></html>")
    base = request.host_url.rstrip("/")
    return (f"<html><body style='background:#0b0e11;color:#d1d4dc;"
            f"font-family:sans-serif;padding:40px'>"
            f"<h2 style='color:#26a69a'>TradeBot Callback</h2>"
            f"<p style='color:#787b86'>Endpoint: <code style='color:#26a69a'>"
            f"{base}/callback</code><br><a href='/' style='color:#26a69a'>&#8592; Dashboard</a></p>"
            f"</body></html>")

@app.route("/setup")
def setup():
    base = request.host_url.rstrip("/")
    return render_template_string(SETUP_HTML, callback_url=base + "/callback",
                                  base_url=base, MARKET=_MARKET, CUR=_CUR)

# ── Push endpoints (engine → Railway) ─────────────────────────
@app.route("/api/push/trade", methods=["POST"])
def push_trade():
    if not _check_key(): return jsonify({"error": "unauthorized"}), 401
    d = request.get_json()
    with _db() as c:
        c.execute("INSERT INTO trades(symbol,strategy,direction,entry_price,"
                  "exit_price,net_pnl,exit_reason,entry_time,lots,source) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (d.get("symbol"), d.get("strategy"), d.get("direction"),
                   d.get("entry_price"), d.get("exit_price"), d.get("net_pnl"),
                   d.get("exit_reason"), d.get("entry_time"),
                   d.get("lots", 1), d.get("source", "live")))
    return jsonify({"status": "ok"})

@app.route("/api/push/status", methods=["POST"])
def push_status():
    if not _check_key(): return jsonify({"error": "unauthorized"}), 401
    kv_set("engine_status", request.get_json())
    return jsonify({"status": "ok"})

@app.route("/api/push/log", methods=["POST"])
def push_log():
    if not _check_key(): return jsonify({"error": "unauthorized"}), 401
    d = request.get_json()
    with _db() as c:
        c.execute("INSERT INTO logs(ts,level,message) VALUES(?,?,?)",
                  (datetime.now().isoformat(), d.get("level", "INFO"), d.get("message", "")))
        c.execute("DELETE FROM logs WHERE id NOT IN "
                  "(SELECT id FROM logs ORDER BY id DESC LIMIT 500)")
    return jsonify({"status": "ok"})

# ── Read endpoints (dashboard polls) ──────────────────────────
@app.route("/api/metrics")
def api_metrics():
    with _db() as c:
        rows = c.execute("SELECT net_pnl FROM trades").fetchall()
    pnls = [r[0] for r in rows if r[0] is not None]
    eng  = kv_get("engine_status", {})
    cap  = eng.get("capital", _RISK_CAPITAL)

    if not pnls:
        z = {"total_trades": 0, "win_rate": 0, "profit_factor": 0,
             "total_pnl": 0, "max_drawdown": 0, "sharpe": 0, "sortino": 0,
             "calmar": 0, "expectancy": 0, "daily_pnl": eng.get("daily_pnl", 0),
             "nav": cap}
        z["gate"] = {"win_rate": False, "profit_factor": False,
                     "max_drawdown": False, "min_trades": False, "all_pass": False}
        return jsonify(z)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n = len(pnls)
    wr = len(wins) / n * 100
    pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 99
    avg = sum(pnls) / n
    std = math.sqrt(sum((p - avg) ** 2 for p in pnls) / n) if n > 1 else 1
    sh = (avg / std) * (252 ** .5) if std > 0 else 0

    # Sortino: uses downside deviation only
    neg = [p for p in pnls if p < 0]
    dsd = math.sqrt(sum(p ** 2 for p in neg) / n) if neg else 1
    sortino = (avg / dsd) * (252 ** .5) if dsd > 0 else 0

    # Max drawdown
    cum = peak = 0
    max_dd = 0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        if cum - peak < max_dd:
            max_dd = cum - peak

    # Calmar: annualized return / |max DD|
    total_pnl = sum(pnls)
    ann_ret = (total_pnl / _RISK_CAPITAL) * 100
    calmar = ann_ret / abs(max_dd / _RISK_CAPITAL * 100) if max_dd != 0 else 0

    gate = {"win_rate": wr >= 55, "profit_factor": pf >= 1.4,
            "max_drawdown": abs(max_dd) < _MAX_DD, "min_trades": n >= 200}
    gate["all_pass"] = all(gate.values())

    return jsonify({
        "total_trades": n, "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2), "total_pnl": round(total_pnl, 2),
        "max_drawdown": round(max_dd, 2), "sharpe": round(sh, 2),
        "sortino": round(sortino, 2), "calmar": round(calmar, 2),
        "expectancy": round(avg, 2), "gate": gate,
        "daily_pnl": eng.get("daily_pnl", 0),
        "nav": round(cap + total_pnl, 2),
    })

@app.route("/api/trades")
def api_trades():
    limit = int(request.args.get("limit", 50))
    with _db() as c:
        rows  = c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        total = c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    return jsonify({"trades": [dict(r) for r in rows], "total": total})

@app.route("/api/equity")
def api_equity():
    capital = float(request.args.get("capital", _RISK_CAPITAL))
    with _db() as c:
        rows = c.execute("SELECT net_pnl FROM trades ORDER BY id ASC").fetchall()
    running = capital
    curve = [capital]
    for r in rows:
        if r[0]:
            running += r[0]
        curve.append(round(running, 2))
    # Compute drawdown series
    peak = capital
    dd_vals = []
    dd_pcts = []
    for eq in curve:
        if eq > peak:
            peak = eq
        dd = eq - peak
        dd_vals.append(round(dd, 2))
        dd_pcts.append(round(dd / peak * 100, 2) if peak > 0 else 0)
    return jsonify({"equity": curve, "drawdown": dd_vals,
                    "dd_pct": dd_pcts, "capital": capital, "peak": peak})

@app.route("/api/logs")
def api_logs():
    with _db() as c:
        rows = c.execute("SELECT ts,level,message FROM logs ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify({"lines": [f"[{r[0][:19]}] {r[1]:8s} {r[2]}" for r in reversed(rows)]})

@app.route("/api/status")
def api_status():
    status = kv_get("engine_status", {})
    with _db() as c:
        n = c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    return jsonify({
        "engine": status,
        "broker_token_set": bool(kv_get("broker_auth_token")),
        "broker_token_time": kv_get("broker_token_time", ""),
        "db_trades": n, "market": _MARKET,
        "server_time": datetime.now().isoformat(),
        "railway_url": RAILWAY_URL,
        "risk_config": {
            "max_capital": _RISK_CAPITAL,
            "max_daily_loss_pct": _MAX_DAILY_LOSS_PCT,
            "max_dd_pct": _MAX_DD_PCT,
            "max_positions": _MAX_POSITIONS,
            "max_trades_day": _MAX_TRADES_DAY,
            "min_rr": _MIN_RR,
        }
    })

# ── Markets + Simulation API ─────────────────────────────────
@app.route("/api/markets")
def api_markets():
    result = []
    for mid, m in WORLD_MARKETS.items():
        status, lt = _market_status(mid)
        result.append({
            "id": mid, "name": m["name"], "flag": m["flag"], "index": m["index"],
            "status": status, "local_time": lt, "currency": m["currency"],
            "locale": m["locale"], "tz": m["tz"], "tz_label": m["tz_label"],
            "instruments": [x["s"] for x in m["instruments"]],
        })
    return jsonify(result)

@app.route("/api/sim/start", methods=["POST"])
def sim_start():
    global _simulator
    d = request.get_json(silent=True) or {}
    market = d.get("market", "US")
    speed = int(d.get("speed", 5))
    if market not in WORLD_MARKETS:
        return jsonify({"error": "unknown market"}), 400
    with _sim_lock:
        if _simulator and _simulator.running:
            _simulator.stop()
            _simulator.join(timeout=3)
        _simulator = DemoSimulator(market_id=market, speed=speed)
        _simulator.start()
    # Log start
    with _db() as c:
        c.execute("INSERT INTO logs(ts,level,message) VALUES(?,?,?)",
                  (datetime.now().isoformat(), "INFO",
                   f"Simulation started: {market} market, {speed}s/bar"))
    return jsonify({"status": "started", "market": market, "speed": speed})

@app.route("/api/sim/stop", methods=["POST"])
def sim_stop():
    global _simulator
    with _sim_lock:
        if _simulator and _simulator.running:
            _simulator.stop()
            _simulator.join(timeout=3)
            # Update engine status to stopped
            kv_set("engine_status", {
                "running": False, "halted": False,
                "capital": round(_simulator.capital, 2),
                "daily_pnl": round(_simulator.daily_pnl, 2),
                "open_positions": 0,
                "trades_today": _simulator.trades_count,
                "max_daily_loss": 0,
                "updated_at": datetime.now().isoformat(),
                "mode": "simulation",
            })
            with _db() as c:
                c.execute("INSERT INTO logs(ts,level,message) VALUES(?,?,?)",
                          (datetime.now().isoformat(), "INFO", "Simulation stopped"))
    return jsonify({"status": "stopped"})

@app.route("/api/sim/status")
def sim_status():
    global _simulator
    if _simulator:
        return jsonify(_simulator.status_dict())
    return jsonify({"running": False, "market": None, "trades": 0})

@app.route("/api/sim/reset", methods=["POST"])
def sim_reset():
    global _simulator
    with _sim_lock:
        if _simulator and _simulator.running:
            _simulator.stop()
            _simulator.join(timeout=3)
        _simulator = None
    # Clear all data
    with _db() as c:
        c.execute("DELETE FROM trades")
        c.execute("DELETE FROM logs")
        c.execute("DELETE FROM kv")
    _init_db()
    return jsonify({"status": "reset"})


# ── Setup Guide ───────────────────────────────────────────────
_SETUP_US = r"""
<h1>TradeBot &#8212; US Market Setup</h1>
<div class="done">&#10003; Dashboard live at <strong>{{ base_url }}</strong></div>
<h2>Option A &#8212; Simulation (no broker)</h2>
<div class="step"><div class="sn">1</div><h3>Clone & install</h3>
<pre>git clone https://github.com/roakeshk/tradebot.git
cd tradebot && python -m venv .venv && .venv\Scripts\activate
pip install -r tradebot/requirements.txt</pre></div>
<div class="step"><div class="sn">2</div><h3>Run simulation</h3>
<pre>set MARKET=US
cd tradebot
python main.py --mode simulate --days 90
python main.py --mode options-sim --days 90
python main.py --mode analyze --symbol AAPL</pre></div>
<div class="step"><div class="sn">3</div><h3>Railway env vars</h3>
<pre>TRADEBOT_KEY = your_secret_key
MARKET       = US
WEBAPP_URL   = {{ base_url }}</pre></div>
<h2>Option B &#8212; Alpaca Paper Trading (free)</h2>
<div class="step"><div class="sn">4</div><h3>Alpaca setup</h3>
<pre>1. alpaca.markets &#8594; sign up (free)
2. Paper Trading &#8594; API Keys
ALPACA_KEY=your_key
ALPACA_SECRET=your_secret
python main.py --mode paper --symbol SPY</pre></div>
"""

_SETUP_INDIA = r"""
<h1>TradeBot &#8212; India Market Setup</h1>
<div class="done">&#10003; Dashboard live at <strong>{{ base_url }}</strong></div>
<h2>Callback URL</h2><div class="hi"><code>{{ callback_url }}</code></div>
<div class="warn"><strong>Static IP required (April 2026+)</strong>
Angel One requires API orders from a registered static IP.</div>
<h2>Steps</h2>
<div class="step"><div class="sn">1</div><h3>smartapi.angelone.in &#8594; Add App</h3>
<pre>App Name: TradeBot
Redirect URL: {{ callback_url }}</pre></div>
<div class="step"><div class="sn">2</div><h3>Enable TOTP & fill settings</h3>
<pre>MARKET=INDIA
DATA_SOURCE=angel
ACTIVE_BROKER=paper</pre></div>
"""

SETUP_HTML = (
    r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Setup</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{background:#0b0e11;color:#d1d4dc;font-family:-apple-system,sans-serif;font-size:14px;line-height:1.7}
nav{background:#131722;border-bottom:1px solid #2a2e39;padding:0 20px;display:flex;align-items:center;gap:20px;height:48px}
.brand{font-size:1rem;font-weight:700;color:#26a69a}nav a{color:#787b86;text-decoration:none;font-size:13px}
.page{max-width:720px;margin:0 auto;padding:28px 16px}
h1{font-size:1.4rem;font-weight:700;margin-bottom:4px}
h2{font-size:.85rem;font-weight:700;color:#26a69a;margin:22px 0 8px;text-transform:uppercase;letter-spacing:.5px}
.step{background:#131722;border:1px solid #2a2e39;border-radius:8px;padding:14px 14px 14px 52px;margin-bottom:10px;position:relative}
.sn{position:absolute;left:12px;top:13px;width:26px;height:26px;border-radius:50%;background:#0d2b2b;border:1.5px solid #26a69a;color:#26a69a;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center}
.step h3{font-size:.9rem;margin-bottom:6px}
pre{background:#1e222d;border:1px solid #2a2e39;border-radius:5px;padding:10px 12px;font-family:monospace;font-size:12px;overflow-x:auto;margin:6px 0;line-height:1.5}
.hi{background:#0d2b2b;border:1px solid #26a69a;border-radius:6px;padding:12px;margin:8px 0;word-break:break-all}
.hi code{color:#26a69a;font-family:monospace;font-size:.95rem;font-weight:700}
.done{background:#0a2818;border:1px solid #26a69a;border-radius:6px;padding:10px 14px;margin:8px 0;color:#26a69a;font-size:13px}
.warn{background:#3d2e0a;border:1px solid #ff9800;border-radius:6px;padding:10px 14px;margin:10px 0;font-size:13px}
.warn strong{color:#ff9800;display:block;margin-bottom:2px}
</style></head><body>
<nav><span class="brand">TradeBot</span><a href="/">Dashboard</a><a href="/setup">Setup</a></nav>
<div class="page">"""
    + "{% if MARKET == 'US' %}" + _SETUP_US + "{% else %}" + _SETUP_INDIA + "{% endif %}"
    + r"</div></body></html>"
)

# ── Dashboard ─────────────────────────────────────────────────
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TradeBot</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0b0e11;--sf:#131722;--sf2:#1e222d;--bd:#2a2e39;--tx:#d1d4dc;--tx2:#787b86;
--up:#26a69a;--dn:#ef5350;--ac:#2962ff;--wn:#ff9800}
body{background:var(--bg);color:var(--tx);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px}
a{color:var(--tx2);text-decoration:none}a:hover{color:var(--tx)}

/* Nav */
nav{background:var(--sf);border-bottom:1px solid var(--bd);padding:0 16px;display:flex;align-items:center;gap:14px;height:48px;position:sticky;top:0;z-index:10}
.brand{font-size:1rem;font-weight:700;color:var(--up);letter-spacing:-.3px}
.nav-link{font-size:12px;color:var(--tx2)}
.badge{font-size:10px;background:#1a1a2e;border:1px solid #5b5fc7;border-radius:3px;padding:1px 7px;color:#8b8ff5}
.ep{border-radius:12px;padding:2px 10px;font-size:11px;font-weight:600;border:1px solid var(--bd);color:var(--tx2);background:var(--sf2)}
.ep.on{background:#0a2818;border-color:var(--up);color:var(--up)}
.ep.halt{background:#3d0f0f;border-color:var(--dn);color:var(--dn)}
.ep.sim{background:#1a1a2e;border-color:#5b5fc7;color:#8b8ff5}
.clk{font-size:11px;color:var(--tx2);font-variant-numeric:tabular-nums}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}

/* Layout */
.mx{max-width:1120px;margin:0 auto;padding:16px 14px 40px}
.hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:8px}
.title{font-size:1.15rem;font-weight:700}.sub{font-size:11px;color:var(--tx2);margin-top:1px}
.btn{background:transparent;border:1px solid var(--bd);color:var(--tx2);padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px;transition:all .2s}
.btn:hover{background:var(--sf2);color:var(--tx)}
.btn-up{border-color:var(--up);color:var(--up)}.btn-up:hover{background:#0a2818}
.btn-dn{border-color:var(--dn);color:var(--dn)}.btn-dn:hover{background:#3d0f0f}
.btn-wn{border-color:var(--wn);color:var(--wn)}.btn-wn:hover{background:#3d2e0a}
.btn-ac{border-color:var(--ac);color:var(--ac)}.btn-ac:hover{background:#0d1a3d}

/* Market cards */
.market-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}
.mc{background:var(--sf2);border:1px solid var(--bd);border-radius:6px;padding:10px;cursor:pointer;transition:all .2s;border-left:3px solid var(--bd)}
.mc:hover{border-color:var(--ac);background:#1e222d}
.mc.selected{border-color:var(--ac);box-shadow:0 0 8px #2962ff30;background:#131722}
.mc.st-open{border-left-color:var(--up)}.mc.st-closed{border-left-color:var(--dn)}.mc.st-lunch{border-left-color:var(--wn)}
.mc-flag{font-size:1.2rem;margin-bottom:2px}
.mc-name{font-size:10px;font-weight:700;color:var(--tx);text-transform:uppercase;letter-spacing:.3px}
.mc-idx{font-size:10px;color:var(--tx2)}
.mc-status{display:flex;align-items:center;gap:4px;margin-top:4px;font-size:10px}
.mc-time{font-size:9px;color:var(--tx2);margin-top:2px}

/* Sim controls */
.sim-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.sim-status{font-size:12px;font-weight:600;padding:4px 10px;border-radius:12px;border:1px solid var(--bd);background:var(--sf2);color:var(--tx2)}
.sim-status.running{background:#1a1a2e;border-color:#5b5fc7;color:#8b8ff5;animation:pulse 2s infinite}
.sim-sel{background:var(--sf2);border:1px solid var(--bd);color:var(--tx);padding:4px 8px;border-radius:4px;font-size:11px}
.sim-info{font-size:10px;color:var(--tx2);margin-left:auto}

/* KPI strip */
.kpi{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:14px}
.kc{background:var(--sf);border:1px solid var(--bd);border-radius:8px;padding:12px 14px;border-left:3px solid var(--bd);min-height:72px}
.kl{font-size:10px;color:var(--tx2);text-transform:uppercase;letter-spacing:.5px;font-weight:600}
.kv{font-size:1.4rem;font-weight:700;margin-top:2px;line-height:1;font-variant-numeric:tabular-nums}
.ks{font-size:10px;color:var(--tx2);margin-top:3px}

/* Risk guardrails */
.card{background:var(--sf);border:1px solid var(--bd);border-radius:8px;padding:14px;margin-bottom:12px}
.card.halt{border-color:var(--dn);box-shadow:0 0 12px #ef535030}
.ch{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.ch h3{font-size:10px;color:var(--tx2);text-transform:uppercase;letter-spacing:.5px;font-weight:700}
.rg{display:grid;grid-template-columns:1fr 1fr;gap:8px 16px}
.rr{display:flex;align-items:center;gap:8px;font-size:12px}
.rl{width:90px;color:var(--tx2);font-size:11px;flex-shrink:0}
.pb{height:6px;border-radius:3px;background:var(--sf2);overflow:hidden;flex:1}
.pb i{display:block;height:100%;border-radius:3px;transition:width .5s ease}
.rv{width:100px;text-align:right;font-size:11px;color:var(--tx);font-variant-numeric:tabular-nums}
.halt-banner{display:none;background:#3d0f0f;border:1px solid var(--dn);border-radius:4px;padding:8px 12px;margin-top:10px;color:var(--dn);font-size:12px;font-weight:600;text-align:center}
.halt-banner.show{display:block}

/* Charts */
.chart-wrap{position:relative}
.eq-chart{height:180px}.dd-chart{height:55px;border-top:1px solid var(--bd);margin-top:2px;padding-top:2px}

/* Gate check */
.gs{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.gp{padding:4px 10px;border-radius:12px;font-size:11px;font-weight:600;display:inline-flex;align-items:center;gap:5px}
.gp.pass{background:#0a2818;border:1px solid var(--up);color:var(--up)}
.gp.fail{background:#3d0f0f;border:1px solid var(--dn);color:var(--dn)}
.gp.banner{background:#0d2b2b;border:1px solid var(--up);color:var(--up);padding:6px 14px}
.dot{width:7px;height:7px;border-radius:50%;display:inline-block}
.dot.g{background:var(--up)}.dot.r{background:var(--dn)}.dot.m{background:var(--tx2)}.dot.y{background:var(--wn)}
.dot.live{animation:pulse 2s infinite}

/* Trade table */
.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:6px 8px;background:var(--sf2);color:var(--tx2);font-size:10px;font-weight:700;text-transform:uppercase;border-bottom:1px solid var(--bd);white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid #1e222d}
tr:last-child td{border-bottom:none}tr:hover td{background:#1e222d08}
.bw{background:#0a2818;color:var(--up);font-size:9px;font-weight:700;padding:2px 5px;border-radius:3px}
.bl{background:#3d0f0f;color:var(--dn);font-size:9px;font-weight:700;padding:2px 5px;border-radius:3px}

/* Bottom grid */
.btm{display:grid;grid-template-columns:280px 1fr;gap:12px}
.si{display:flex;align-items:center;gap:7px;font-size:12px;padding:6px 0;border-bottom:1px solid var(--bd)}
.si:last-child{border-bottom:none}
.log{background:var(--sf2);border-radius:5px;padding:8px 10px;font-family:'Cascadia Mono',monospace;font-size:11px;max-height:200px;overflow-y:auto;color:var(--tx2);line-height:1.5}

/* Responsive */
@media(max-width:768px){
.kpi{grid-template-columns:repeat(2,1fr)}
.market-grid{grid-template-columns:repeat(2,1fr)}
.rg{grid-template-columns:1fr}
.btm{grid-template-columns:1fr}
.eq-chart{height:140px}.dd-chart{height:45px}
}
@media(max-width:480px){
.market-grid{grid-template-columns:1fr 1fr}
}
</style></head><body>

<nav>
  <span class="brand">TradeBot</span>
  <a class="nav-link" href="/">Dashboard</a>
  <a class="nav-link" href="/setup">Setup</a>
  <span class="badge">v2</span>
  <div style="flex:1"></div>
  <span class="ep" id="ep">connecting...</span>
  <span class="clk" id="clk"></span>
</nav>

<div class="mx">
  <!-- Header -->
  <div class="hdr">
    <div><div class="title">Trading Dashboard</div><div class="sub" id="ud">Connecting...</div></div>
    <button class="btn" onclick="loadAll()">&#8635; Refresh</button>
  </div>

  <!-- World Markets -->
  <div class="card">
    <div class="ch"><h3>World Markets</h3><span style="font-size:10px;color:var(--tx2)" id="mt">Live status</span></div>
    <div class="market-grid" id="ml">Loading markets...</div>
  </div>

  <!-- Simulation Controls -->
  <div class="card">
    <div class="ch"><h3>Simulation</h3><span class="sim-status" id="ss">Stopped</span></div>
    <div class="sim-row">
      <select class="sim-sel" id="sp">
        <option value="2">Turbo (2s)</option>
        <option value="5" selected>Normal (5s)</option>
        <option value="10">Slow (10s)</option>
      </select>
      <button class="btn btn-up" onclick="simStart()">&#9654; Start</button>
      <button class="btn btn-dn" onclick="simStop()">&#9632; Stop</button>
      <button class="btn btn-wn" onclick="simReset()">&#8634; Reset</button>
      <span class="sim-info" id="si2"></span>
    </div>
  </div>

  <!-- KPI Strip -->
  <div class="kpi" id="kpi">
    <div class="kc"><div class="kl">NAV</div><div class="kv">&#8212;</div></div>
    <div class="kc"><div class="kl">Daily P&amp;L</div><div class="kv">&#8212;</div></div>
    <div class="kc"><div class="kl">Total P&amp;L</div><div class="kv">&#8212;</div></div>
    <div class="kc"><div class="kl">Win Rate</div><div class="kv">&#8212;</div></div>
    <div class="kc"><div class="kl">Sharpe</div><div class="kv">&#8212;</div></div>
    <div class="kc"><div class="kl">Max Drawdown</div><div class="kv">&#8212;</div></div>
  </div>

  <!-- Risk Guardrails -->
  <div class="card" id="rg-card">
    <div class="ch"><h3>Risk Guardrails</h3><span id="rg-state" style="font-size:11px;color:var(--up)">ACTIVE</span></div>
    <div class="rg" id="rg"></div>
    <div class="halt-banner" id="hb">&#9888; TRADING HALTED &#8212; Daily loss limit reached</div>
  </div>

  <!-- Equity + Drawdown -->
  <div class="card">
    <div class="ch"><h3>Equity Curve</h3><span style="font-size:11px;color:var(--tx2)" id="eq-info"></span></div>
    <div class="chart-wrap eq-chart"><canvas id="ec"></canvas></div>
    <div class="ch" style="margin-top:8px;margin-bottom:4px"><h3>Drawdown</h3></div>
    <div class="chart-wrap dd-chart"><canvas id="dc"></canvas></div>
  </div>

  <!-- Gate Check -->
  <div class="card">
    <div class="ch"><h3>Go/No-Go &#8212; Live Capital Gate</h3></div>
    <div class="gs" id="gs"><span class="gp fail"><span class="dot m"></span>Loading...</span></div>
  </div>

  <!-- Trade Table -->
  <div class="card">
    <div class="ch"><h3>Trade Log</h3><span style="font-size:11px;color:var(--tx2)" id="tc">0 trades</span></div>
    <div class="tbl-wrap"><table>
      <thead><tr><th>Symbol</th><th>Time</th><th>Strategy</th><th>Dir</th><th style="text-align:right">Entry</th><th style="text-align:right">Exit</th><th>Reason</th><th style="text-align:right">Net P&amp;L</th><th></th></tr></thead>
      <tbody id="tb"><tr><td colspan="9" style="color:var(--tx2);text-align:center;padding:20px">No trades yet</td></tr></tbody>
    </table></div>
  </div>

  <!-- Status + Logs -->
  <div class="btm">
    <div class="card">
      <div class="ch"><h3>System Status</h3></div>
      <div id="si"></div>
    </div>
    <div class="card">
      <div class="ch"><h3>Engine Log</h3><button class="btn" onclick="loadLogs()">&#8635;</button></div>
      <div class="log" id="lg">Waiting for engine...</div>
    </div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
/* ── Config (injected — use let for market switching) ── */
let C="{{ CUR }}",L="{{ LOCALE }}",TZ="{{ JS_TZ }}",TZL="{{ TZ_LABEL }}",MK="{{ MARKET }}";
const RC={cap:{{ RISK_CAPITAL }},mdl:{{ MAX_DAILY_LOSS_PCT }},mdd:{{ MAX_DD_PCT }},mxp:{{ MAX_POSITIONS }},mxt:{{ MAX_TRADES_DAY }},rr:{{ MIN_RR }}};
{{ FMT_JS|safe }}
{{ FMT_FACTORY_JS|safe }}
let selectedMarket=MK;

let ecChart=null,dcChart=null;
const $=id=>document.getElementById(id);

/* ── Clock ── */
setInterval(()=>{try{$('clk').textContent=new Date().toLocaleTimeString(L,{timeZone:TZ,hour:'2-digit',minute:'2-digit',second:'2-digit'})+' '+TZL}catch(e){}},1000);

/* ── Helpers ── */
function pbc(pct){return pct>80?'var(--dn)':pct>50?'var(--wn)':'var(--up)'}
function clr(v){return v>0?'var(--up)':v<0?'var(--dn)':'var(--tx2)'}
function sign(v){return v>0?'+'+fmt(v):v<0?'-'+fmt(Math.abs(v)):fmt(0)}

/* ── Load Markets ── */
async function loadMarkets(){
  try{
    const mkts=await fetch('/api/markets').then(r=>r.json());
    const dotCls={open:'g live',closed:'r',lunch:'y'};
    const dotLbl={open:'Open',closed:'Closed',lunch:'Lunch'};
    $('ml').innerHTML=mkts.map(m=>{
      const sel=m.id===selectedMarket?' selected':'';
      return`<div class="mc st-${m.status}${sel}" onclick="selectMarket('${m.id}',this)" data-mid="${m.id}">
        <div class="mc-flag">${m.flag}</div>
        <div class="mc-name">${m.name}</div>
        <div class="mc-idx">${m.index}</div>
        <div class="mc-status"><span class="dot ${dotCls[m.status]||'m'}"></span><span style="color:${m.status==='open'?'var(--up)':m.status==='lunch'?'var(--wn)':'var(--dn)'}">${dotLbl[m.status]||m.status}</span></div>
        <div class="mc-time">${m.local_time}</div>
      </div>`;
    }).join('');
    const openCount=mkts.filter(m=>m.status==='open').length;
    $('mt').textContent=openCount+' market'+(openCount!==1?'s':'')+' open';
  }catch(e){console.error('markets error',e);}
}

/* ── Select Market ── */
function selectMarket(id,el){
  selectedMarket=id;
  document.querySelectorAll('.mc').forEach(c=>c.classList.remove('selected'));
  if(el)el.classList.add('selected');
  /* We don't change fmt/currency here since trades are stored server-side.
     The market selection is used for simulation start. */
}

/* ── Simulation Controls ── */
async function simStart(){
  const speed=$('sp').value;
  try{
    const r=await fetch('/api/sim/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({market:selectedMarket,speed:parseInt(speed)})});
    const d=await r.json();
    if(d.status==='started'){
      $('ss').textContent='Running ('+selectedMarket+')';$('ss').className='sim-status running';
      setTimeout(loadAll,2000);
    }
  }catch(e){console.error('sim start error',e);}
}

async function simStop(){
  try{
    await fetch('/api/sim/stop',{method:'POST'});
    $('ss').textContent='Stopped';$('ss').className='sim-status';
    setTimeout(loadAll,500);
  }catch(e){console.error('sim stop error',e);}
}

async function simReset(){
  try{
    await fetch('/api/sim/reset',{method:'POST'});
    $('ss').textContent='Stopped';$('ss').className='sim-status';
    $('si2').textContent='';
    if(ecChart){ecChart.destroy();ecChart=null;}
    if(dcChart){dcChart.destroy();dcChart=null;}
    setTimeout(loadAll,500);
  }catch(e){console.error('sim reset error',e);}
}

async function loadSimStatus(){
  try{
    const d=await fetch('/api/sim/status').then(r=>r.json());
    if(d.running){
      $('ss').textContent='Running ('+d.market+')';$('ss').className='sim-status running';
      $('si2').textContent=d.trades+' trades | '+C+Math.round(d.daily_pnl)+' P&L | '+d.elapsed;
    }else if(d.trades>0){
      $('ss').textContent='Stopped';$('ss').className='sim-status';
      $('si2').textContent=d.trades+' trades completed';
    }else{
      $('ss').textContent='Stopped';$('ss').className='sim-status';
      $('si2').textContent='Select a market and click Start';
    }
  }catch(e){}
}

/* ── Load Status ── */
async function loadStatus(){
  const d=await fetch('/api/status').then(r=>r.json());
  const e=d.engine||{};
  const ep=$('ep');
  if(e.mode==='simulation'){ep.className='ep sim';ep.textContent='Simulation';}
  else if(e.halted){ep.className='ep halt';ep.textContent='HALTED';}
  else if(e.running){ep.className='ep on';ep.textContent='Engine Live';}
  else{ep.className='ep';ep.textContent='Engine Offline';}
  $('si').innerHTML=[
    {l:'Engine',ok:!!e.running,live:true},
    {l:'Broker Token',ok:d.broker_token_set},
    {l:'Database ('+d.db_trades+' trades)',ok:d.db_trades>0},
    {l:MK+' Market',ok:true},
    {l:'Railway',ok:true},
  ].map(i=>`<div class="si"><span class="dot ${i.ok?(i.live?'g live':'g'):'r'}"></span>${i.l}<span style="margin-left:auto;font-size:10px;color:${i.ok?'var(--up)':'var(--dn)'}">${i.ok?'OK':'&#8212;'}</span></div>`).join('');
  return d;
}

/* ── Load KPIs ── */
async function loadKPI(){
  const[m,s]=await Promise.all([fetch('/api/metrics').then(r=>r.json()),fetch('/api/status').then(r=>r.json())]);
  const e=s.engine||{};
  const dp=m.daily_pnl||e.daily_pnl||0;
  const cards=[
    {l:'NAV',v:fmt(m.nav||RC.cap),c:'var(--ac)',s:C+' account'},
    {l:'Daily P&L',v:sign(dp),c:clr(dp),s:'today'},
    {l:'Total P&L',v:sign(m.total_pnl),c:clr(m.total_pnl),s:m.total_trades+' trades'},
    {l:'Win Rate',v:m.win_rate+'%',c:m.win_rate>=55?'var(--up)':'var(--dn)',s:'target \u226555%'},
    {l:'Sharpe',v:m.sharpe,c:m.sharpe>=0.8?'var(--up)':'var(--tx2)',s:'Sortino: '+m.sortino},
    {l:'Max Drawdown',v:fmt(Math.abs(m.max_drawdown)),c:'var(--dn)',s:'limit: {{ MAX_DD_K }}'},
  ];
  $('kpi').innerHTML=cards.map(c=>`<div class="kc" style="border-left-color:${c.c}"><div class="kl">${c.l}</div><div class="kv" style="color:${c.c}">${c.v}</div><div class="ks">${c.s}</div></div>`).join('');
  return{m,s};
}

/* ── Load Risk ── */
function loadRisk(s){
  const e=(s?s.engine:null)||{};
  const rc=(s?s.risk_config:null)||RC;
  const mdl=rc.max_capital*rc.max_daily_loss_pct/100;
  const mdd=rc.max_capital*rc.max_dd_pct/100;
  const halted=!!e.halted;
  const bars=[
    {l:'Daily Loss',cur:Math.abs(e.daily_pnl||0),max:mdl,f:true},
    {l:'Max DD',cur:Math.abs(e.max_daily_loss||0),max:mdd,f:true},
    {l:'Positions',cur:e.open_positions||0,max:rc.max_positions,f:false},
    {l:'Trades/Day',cur:e.trades_today||0,max:rc.max_trades_day,f:false},
  ];
  $('rg').innerHTML=bars.map(b=>{
    const pct=Math.min(b.cur/b.max*100,100)||0;
    const col=pbc(pct);
    const vt=b.f?C+Math.round(b.cur)+' / '+C+Math.round(b.max):b.cur+' / '+b.max;
    return`<div class="rr"><span class="rl">${b.l}</span><div class="pb"><i style="width:${pct}%;background:${col}"></i></div><span class="rv">${vt}</span></div>`;
  }).join('');
  const card=$('rg-card');
  const hb=$('hb');
  const st=$('rg-state');
  if(halted){card.classList.add('halt');hb.classList.add('show');st.textContent='HALTED';st.style.color='var(--dn)';}
  else{card.classList.remove('halt');hb.classList.remove('show');st.textContent='ACTIVE';st.style.color='var(--up)';}
}

/* ── Load Equity ── */
async function loadEquity(){
  const d=await fetch('/api/equity').then(r=>r.json());
  if(!d.equity||d.equity.length<2)return;
  const last=d.equity[d.equity.length-1];
  const col=last>=d.capital?'#26a69a':'#ef5350';
  $('eq-info').textContent='Peak: '+fmt(d.peak)+' | Current: '+fmt(last);

  const labels=d.equity.map((_,i)=>i);
  const cfg={responsive:true,maintainAspectRatio:false,animation:{duration:600},
    plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>C+Math.round(ctx.raw).toLocaleString(L)}}},
    scales:{x:{display:false},y:{ticks:{color:'#787b86',font:{size:10},callback:v=>fmt(v)},grid:{color:'#2a2e3940'},border:{display:false}}}};

  if(ecChart)ecChart.destroy();
  ecChart=new Chart($('ec'),{type:'line',data:{labels,datasets:[{data:d.equity,borderColor:col,borderWidth:1.5,
    fill:true,backgroundColor:col+'15',tension:.3,pointRadius:0,pointHoverRadius:3}]},options:cfg});

  if(dcChart)dcChart.destroy();
  const ddCfg={...cfg,scales:{x:{display:false},y:{ticks:{color:'#787b86',font:{size:9},callback:v=>v.toFixed(1)+'%'},grid:{color:'#2a2e3920'},border:{display:false}}}};
  dcChart=new Chart($('dc'),{type:'line',data:{labels,datasets:[{data:d.dd_pct,borderColor:'#ef5350',borderWidth:1,
    fill:true,backgroundColor:'#ef535018',tension:.3,pointRadius:0,pointHoverRadius:2}]},options:ddCfg});
}

/* ── Load Gate ── */
function loadGate(m){
  const g=m.gate||{};
  const items=[
    {l:'Win Rate \u226555%',v:m.win_rate+'%',p:g.win_rate},
    {l:'PF \u22651.4',v:m.profit_factor,p:g.profit_factor},
    {l:'DD <{{ MAX_DD_K }}',v:fmt(Math.abs(m.max_drawdown)),p:g.max_drawdown},
    {l:'Trades \u2265200',v:m.total_trades,p:g.min_trades},
  ];
  $('gs').innerHTML=items.map(x=>`<span class="gp ${x.p?'pass':'fail'}"><span class="dot ${x.p?'g':'r'}"></span>${x.l} (${x.v})</span>`).join('')+
    (g.all_pass?'<span class="gp banner"><span class="dot g"></span>ALL GATES PASSED &#8212; Ready for live capital</span>':'');
}

/* ── Load Trades ── */
async function loadTrades(){
  const d=await fetch('/api/trades?limit=30').then(r=>r.json());
  $('tc').textContent='Showing '+Math.min(d.trades.length,30)+' of '+d.total+' trades';
  if(!d.trades.length){$('tb').innerHTML='<tr><td colspan="9" style="color:var(--tx2);text-align:center;padding:20px">No trades yet &#8212; start a simulation above</td></tr>';return;}
  const tfmt=t=>{try{const dt=new Date(t);return dt.toLocaleDateString(L,{month:'short',day:'numeric',timeZone:TZ})+' '+dt.toLocaleTimeString(L,{hour:'2-digit',minute:'2-digit',timeZone:TZ})}catch(e){return(t||'').slice(0,16)}};
  $('tb').innerHTML=d.trades.map(t=>{
    const p=parseFloat(t.net_pnl||0),w=p>0;
    const dc=t.direction==='LONG'?'var(--up)':t.direction==='SHORT'?'var(--dn)':'var(--ac)';
    return`<tr>
      <td style="font-weight:600">${t.symbol||'SPY'}</td>
      <td style="color:var(--tx2);white-space:nowrap;font-size:11px">${tfmt(t.entry_time)}</td>
      <td style="font-size:11px">${t.strategy||'-'}</td>
      <td style="color:${dc};font-weight:600;font-size:11px">${t.direction||'-'}</td>
      <td style="text-align:right;font-variant-numeric:tabular-nums">${C}${parseFloat(t.entry_price||0).toFixed(2)}</td>
      <td style="text-align:right;font-variant-numeric:tabular-nums">${C}${parseFloat(t.exit_price||0).toFixed(2)}</td>
      <td style="color:var(--tx2);font-size:11px">${t.exit_reason||'-'}</td>
      <td style="text-align:right;color:${clr(p)};font-weight:600;font-variant-numeric:tabular-nums">${sign(p)}</td>
      <td><span class="${w?'bw':'bl'}">${w?'WIN':'LOSS'}</span></td></tr>`;
  }).join('');
}

/* ── Load Logs ── */
async function loadLogs(){
  const d=await fetch('/api/logs').then(r=>r.json());
  const b=$('lg');
  if(!d.lines.length){b.textContent='No logs yet.';return;}
  b.innerHTML=d.lines.map(l=>{
    const c=l.includes('ERROR')?'var(--dn)':l.includes('WARN')?'var(--wn)':l.match(/WIN|FILL|TARGET/)?'var(--up)':'var(--tx2)';
    return`<div style="color:${c}">${l}</div>`;
  }).join('');
  b.scrollTop=b.scrollHeight;
}

/* ── Master loader ── */
async function loadAll(){
  $('ud').textContent='Updated '+new Date().toLocaleTimeString(L,{timeZone:TZ})+' '+TZL;
  try{
    const[kpi,,]=await Promise.all([loadKPI(),loadEquity(),loadTrades(),loadLogs(),loadStatus(),loadMarkets(),loadSimStatus()]);
    loadGate(kpi.m);
    loadRisk(kpi.s);
  }catch(e){console.error('poll error',e);}
}
loadAll();setInterval(loadAll,30000);
</script></body></html>"""

# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  TradeBot Relay  [{_MARKET} market]")
    print(f"  Dashboard:   http://localhost:{port}/")
    print(f"  Setup guide: http://localhost:{port}/setup")
    print(f"  Callback:    http://localhost:{port}/callback\n")
    app.run(host="0.0.0.0", port=port, debug=False)
