"""
TradeBot Web App — LIVE on Railway
Dashboard: https://tradebot-production-c63c.up.railway.app/

ProxyFix added so request.host_url returns https:// (not http://) behind Railway proxy.
TRADEBOT_KEY read from Railway environment variable (not hardcoded).
Supports MARKET=US (default) or MARKET=INDIA via Railway env var.
"""
import json, os, sqlite3, math
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, render_template_string, request
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
# Fix: Railway sits behind a reverse proxy — this makes request.host_url return https://
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# ── Market-aware display config (standalone relay app — no tradebot deps) ──
_MARKET   = os.environ.get("MARKET", "US")
_CUR      = "$"              if _MARKET == "US" else "₹"
_LOCALE   = "en-US"          if _MARKET == "US" else "en-IN"
_TZ_LABEL = "ET"             if _MARKET == "US" else "IST"
_JS_TZ    = "America/New_York" if _MARKET == "US" else "Asia/Kolkata"
_MAX_DD   = float(os.environ.get("MAX_DD", "5000" if _MARKET == "US" else "12000"))
_MAX_DD_K = f"{_CUR}{_MAX_DD/1000:.0f}k"

# US: fmt in millions/thousands; India: in lakhs/thousands
if _MARKET == "US":
    _FMT_JS = (
        r"const fmt=v=>{const a=Math.abs(v),"
        r"s=a>=1000000?'CUR'+(a/1000000).toFixed(2)+'M':"
        r"a>=1000?'CUR'+(a/1000).toFixed(1)+'k':'CUR'+Math.round(a).toLocaleString('LOCALE');"
        r"return v<0?'-'+s:s};"
    ).replace("CUR", _CUR).replace("LOCALE", _LOCALE)
else:
    _FMT_JS = (
        r"const fmt=v=>{const a=Math.abs(v),"
        r"s=a>=100000?'CUR'+(a/100000).toFixed(1)+'L':'CUR'+Math.round(a).toLocaleString('LOCALE');"
        r"return v<0?'-'+s:s};"
    ).replace("CUR", _CUR).replace("LOCALE", _LOCALE)

RAILWAY_URL = "https://tradebot-production-c63c.up.railway.app"

DB_PATH = Path(os.environ.get("DATA_DIR", "/tmp")) / "tradebot_web.db"

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
        c.execute("INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                  (key, json.dumps(val), datetime.now().isoformat()))

def kv_get(key, default=None):
    with _db() as c:
        r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

def _check_key():
    k = request.headers.get("X-API-Key","")
    expected = os.environ.get("TRADEBOT_KEY", "")
    return bool(expected) and k == expected

# ── Public routes ──────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML,
        CUR=_CUR, LOCALE=_LOCALE, JS_TZ=_JS_TZ, TZ_LABEL=_TZ_LABEL,
        MAX_DD_K=_MAX_DD_K, FMT_JS=_FMT_JS, MARKET=_MARKET)

@app.route("/health")
def health():
    return jsonify({"status":"ok","time":datetime.now().isoformat()})

@app.route("/callback")
def callback():
    """OAuth / token callback endpoint — broker-agnostic."""
    auth = request.args.get("auth_token","") or request.args.get("token","")
    source = request.args.get("source","broker")
    if auth:
        kv_set("broker_auth_token", auth)
        kv_set("broker_token_time", datetime.now().isoformat())
        kv_set("broker_token_source", source)
        return (
            "<html><body style='background:#0f1117;color:#2dd4bf;"
            "font-family:sans-serif;padding:40px'>"
            f"<h2>&#10003; Connected ({source})</h2>"
            "<p style='color:#8892a4'>Token saved. Close this tab.</p>"
            "</body></html>"
        )
    base = request.host_url.rstrip("/")
    return (
        f"<html><body style='background:#0f1117;color:#e2e8f0;"
        f"font-family:sans-serif;padding:40px'>"
        f"<h2 style='color:#2dd4bf'>TradeBot Callback</h2>"
        f"<p style='color:#8892a4'>Callback URL registered with your broker.<br>"
        f"Endpoint: <code style='color:#2dd4bf'>{base}/callback</code><br>"
        f"<a href='/' style='color:#2dd4bf'>&#8592; Dashboard</a></p>"
        f"</body></html>"
    )

@app.route("/setup")
def setup():
    base = request.host_url.rstrip("/")
    cb   = base + "/callback"
    return render_template_string(SETUP_HTML, callback_url=cb, base_url=base,
                                  MARKET=_MARKET, CUR=_CUR)

# ── Push endpoints (local engine → Railway) ───────────────────
@app.route("/api/push/trade", methods=["POST"])
def push_trade():
    if not _check_key(): return jsonify({"error":"unauthorized"}),401
    d = request.get_json()
    with _db() as c:
        c.execute("INSERT INTO trades(symbol,strategy,direction,entry_price,exit_price,net_pnl,exit_reason,entry_time,lots,source) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (d.get("symbol"),d.get("strategy"),d.get("direction"),
             d.get("entry_price"),d.get("exit_price"),d.get("net_pnl"),
             d.get("exit_reason"),d.get("entry_time"),d.get("lots",1),d.get("source","live")))
    return jsonify({"status":"ok"})

@app.route("/api/push/status", methods=["POST"])
def push_status():
    if not _check_key(): return jsonify({"error":"unauthorized"}),401
    kv_set("engine_status", request.get_json())
    return jsonify({"status":"ok"})

@app.route("/api/push/log", methods=["POST"])
def push_log():
    if not _check_key(): return jsonify({"error":"unauthorized"}),401
    d = request.get_json()
    with _db() as c:
        c.execute("INSERT INTO logs(ts,level,message) VALUES(?,?,?)",
                  (datetime.now().isoformat(), d.get("level","INFO"), d.get("message","")))
        c.execute("DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 500)")
    return jsonify({"status":"ok"})

# ── Read endpoints (dashboard polls these) ────────────────────
@app.route("/api/metrics")
def api_metrics():
    with _db() as c:
        rows = c.execute("SELECT net_pnl FROM trades").fetchall()
    pnls = [r[0] for r in rows if r[0] is not None]
    if not pnls:
        e = {"total_trades":0,"win_rate":0,"profit_factor":0,"total_pnl":0,
             "max_drawdown":0,"sharpe":0,"expectancy":0}
        e["gate"] = {"win_rate":False,"profit_factor":False,
                     "max_drawdown":False,"min_trades":False,"all_pass":False}
        return jsonify(e)
    wins=[p for p in pnls if p>0]; losses=[p for p in pnls if p<0]; n=len(pnls)
    wr=len(wins)/n*100; pf=sum(wins)/abs(sum(losses)) if losses else 0
    avg=sum(pnls)/n; std=math.sqrt(sum((p-avg)**2 for p in pnls)/n) if n>1 else 1
    sh=(avg/std)*(252**.5) if std>0 else 0
    cum=0; peak=0; max_dd=0
    for p in pnls:
        cum+=p
        if cum>peak: peak=cum
        if cum-peak<max_dd: max_dd=cum-peak
    gate={"win_rate":wr>=55,"profit_factor":pf>=1.4,
          "max_drawdown":abs(max_dd)<_MAX_DD,"min_trades":n>=200}
    gate["all_pass"]=all(gate.values())
    return jsonify({"total_trades":n,"win_rate":round(wr,1),"profit_factor":round(pf,2),
                    "total_pnl":round(sum(pnls),2),"max_drawdown":round(max_dd,2),
                    "sharpe":round(sh,2),"expectancy":round(avg,2),"gate":gate})

@app.route("/api/trades")
def api_trades():
    limit = int(request.args.get("limit",50))
    with _db() as c:
        rows  = c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
        total = c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    return jsonify({"trades":[dict(r) for r in rows],"total":total})

@app.route("/api/equity")
def api_equity():
    capital = float(request.args.get("capital",100000))
    with _db() as c:
        rows = c.execute("SELECT net_pnl FROM trades ORDER BY id ASC").fetchall()
    running=capital; curve=[]
    for r in rows:
        if r[0]: running+=r[0]; curve.append(round(running,2))
    return jsonify({"equity":curve,"capital":capital})

@app.route("/api/logs")
def api_logs():
    with _db() as c:
        rows = c.execute("SELECT ts,level,message FROM logs ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify({"lines":[f"[{r[0][:19]}] {r[1]:8s} {r[2]}" for r in reversed(rows)]})

@app.route("/api/status")
def api_status():
    status = kv_get("engine_status",{})
    with _db() as c:
        n = c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    return jsonify({"engine":status,
                    "broker_token_set":bool(kv_get("broker_auth_token")),
                    "broker_token_time":kv_get("broker_token_time",""),
                    "db_trades":n,
                    "market": _MARKET,
                    "server_time":datetime.now().isoformat(),
                    "railway_url": RAILWAY_URL})

# ── Setup Guide ────────────────────────────────────────────────
_SETUP_US = r"""
<h1>TradeBot — US Market Setup Guide</h1>
<div class="done">&#10003; Railway deployed — dashboard is live at <strong>{{ base_url }}</strong></div>

<h2>Option A — Simulation (no broker required)</h2>
<div class="step"><div class="sn">1</div>
<h3>Clone and install</h3>
<pre>git clone https://github.com/roakeshk/tradebot.git
cd tradebot
python -m venv .venv && .venv\Scripts\activate
pip install -r tradebot/requirements.txt</pre>
</div>
<div class="step"><div class="sn">2</div>
<h3>Set environment and run simulation</h3>
<pre>set MARKET=US
cd tradebot
python main.py --mode simulate --days 90

# Options simulation (no broker)
python main.py --mode options-sim --days 90

# Stock analysis
python main.py --mode analyze --symbol AAPL</pre>
<p>Simulation runs on yfinance historical data — no API key needed.</p>
</div>
<div class="step"><div class="sn">3</div>
<h3>Set Railway env vars to push live data here</h3>
<pre>Railway dashboard &#8594; Variables:
  TRADEBOT_KEY = your_secret_key_here
  MARKET       = US

In your local .env:
  TRADEBOT_KEY=your_secret_key_here
  WEBAPP_URL=https://tradebot-production-c63c.up.railway.app
  MARKET=US</pre>
</div>

<h2>Option B — Alpaca Paper Trading (free)</h2>
<div class="step"><div class="sn">4</div>
<h3>Create free Alpaca account</h3>
<pre>1. Go to alpaca.markets &#8594; sign up (free)
2. Dashboard &#8594; Paper Trading &#8594; API Keys
3. Copy API Key ID and Secret Key</pre>
</div>
<div class="step"><div class="sn">5</div>
<h3>Configure Alpaca in .env or settings.py</h3>
<pre>MARKET=US
DATA_SOURCE=alpaca
ACTIVE_BROKER=alpaca_paper
ALPACA_KEY=your_api_key_id
ALPACA_SECRET=your_secret_key
ALPACA_BASE_URL=https://paper-api.alpaca.markets</pre>
</div>
<div class="step"><div class="sn">6</div>
<h3>Run paper trading</h3>
<pre>python main.py --mode paper --symbol SPY
# Dashboard auto-updates at: {{ base_url }}</pre>
</div>
"""

_SETUP_INDIA = r"""
<h1>TradeBot — India Market Setup Guide</h1>
<div class="done">&#10003; Railway deployed — dashboard is live at <strong>{{ base_url }}</strong></div>

<h2>Your callback URL — paste into SmartAPI form</h2>
<div class="hi"><code>{{ callback_url }}</code></div>

<div class="warn"><strong>Static IP requirement (April 2026 onwards)</strong>
Angel One requires all API orders from a registered static IP.
Cheapest: Oracle Cloud free VM (Mumbai region) — static IP, free forever.</div>

<h2>Steps</h2>
<div class="step"><div class="sn">1</div>
<h3>smartapi.angelone.in &#8594; Add App</h3>
<pre>App Name:          TradeBot
Redirect URL:      {{ callback_url }}
Primary Static IP: [your static IP]</pre>
</div>
<div class="step"><div class="sn">2</div>
<h3>Enable TOTP on Angel One mobile app</h3>
<pre>Angel One App &#8594; Profile &#8594; Security &#8594; Enable TOTP
Tap "Can't scan?" &#8594; copy base32 secret</pre>
</div>
<div class="step"><div class="sn">3</div>
<h3>Fill config/settings.py with credentials</h3>
<pre>MARKET=INDIA
DATA_SOURCE=angel
ACTIVE_BROKER=paper</pre>
</div>
"""

SETUP_HTML = (
    r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Setup Guide</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{background:#0f1117;color:#e2e8f0;font-family:-apple-system,sans-serif;font-size:14px;line-height:1.7}
nav{background:#161b27;border-bottom:1px solid #2a3245;padding:0 20px;display:flex;align-items:center;gap:20px;height:50px}
.brand{font-size:1.05rem;font-weight:700;color:#2dd4bf}nav a{color:#8892a4;text-decoration:none;font-size:13px}
.page{max-width:760px;margin:0 auto;padding:32px 20px}
h1{font-size:1.5rem;font-weight:700;margin-bottom:6px}
h2{font-size:.95rem;font-weight:700;color:#2dd4bf;margin:26px 0 10px;text-transform:uppercase;letter-spacing:.5px}
p{color:#8892a4;margin-bottom:8px;font-size:13.5px}
.step{background:#161b27;border:1px solid #2a3245;border-radius:10px;padding:18px 18px 18px 56px;margin-bottom:12px;position:relative}
.sn{position:absolute;left:14px;top:16px;width:28px;height:28px;border-radius:50%;background:#0d3330;border:1.5px solid #2dd4bf;color:#2dd4bf;font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center}
.step h3{font-size:.95rem;font-weight:600;margin-bottom:8px;color:#e2e8f0}
pre{background:#1e2536;border:1px solid #2a3245;border-radius:6px;padding:12px 14px;font-family:monospace;font-size:12px;overflow-x:auto;margin:8px 0;color:#e2e8f0;line-height:1.6}
.hi{background:#0d3330;border:1px solid #2dd4bf;border-radius:8px;padding:14px 18px;margin:10px 0;word-break:break-all}
.hi code{color:#2dd4bf;font-family:monospace;font-size:1rem;font-weight:700}
.done{background:#0a2e18;border:1px solid #4ade80;border-radius:8px;padding:12px 16px;margin:10px 0;color:#4ade80;font-size:13px}
.warn{background:#3d2e0a;border:1px solid #fbbf24;border-radius:8px;padding:12px 16px;margin:12px 0;font-size:13px}
.warn strong{color:#fbbf24;display:block;margin-bottom:4px}
</style></head><body>
<nav><span class="brand">TradeBot</span><a href="/">Dashboard</a><a href="/setup">Setup Guide</a></nav>
<div class="page">
"""
    + "{% if MARKET == 'US' %}" + _SETUP_US + "{% else %}" + _SETUP_INDIA + "{% endif %}"
    + r"</div></body></html>"
)

# ── Dashboard HTML ─────────────────────────────────────────────
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TradeBot</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0f1117;--bg2:#161b27;--bg3:#1e2536;--border:#2a3245;--text:#e2e8f0;--muted:#8892a4;--teal:#2dd4bf;--amber:#fbbf24;--green:#4ade80;--red:#f87171}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px}
nav{background:var(--bg2);border-bottom:1px solid var(--border);padding:0 18px;display:flex;align-items:center;gap:18px;height:50px;position:sticky;top:0;z-index:10}
.brand{font-size:1.05rem;font-weight:700;color:var(--teal);flex-shrink:0}
nav a{color:var(--muted);text-decoration:none;font-size:13px}nav a:hover{color:var(--text)}
.pill{border-radius:20px;padding:3px 10px;font-size:11px;font-weight:600;border:1px solid var(--border);color:var(--muted);background:var(--bg3)}
.pill.on{background:#0a2e18;border-color:var(--green);color:var(--green)}
.railway-badge{font-size:11px;background:#2d1f5e;border:1px solid #a78bfa;border-radius:4px;padding:2px 8px;color:#a78bfa}
.main{max-width:1040px;margin:0 auto;padding:20px 16px}
.hr{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:18px;flex-wrap:wrap;gap:10px}
.pt{font-size:1.25rem;font-weight:700}.ps{font-size:12px;color:var(--muted);margin-top:2px}
.btn{background:transparent;border:1px solid var(--border);color:var(--muted);padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px}.btn:hover{background:var(--bg3);color:var(--text)}
.ss{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px}
.si{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:5px 11px;font-size:12px;display:flex;align-items:center;gap:7px}
.d{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.dg{background:var(--green)}.dr{background:var(--red)}.dm{background:var(--muted)}
.mg{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:11px;margin-bottom:18px}
.mc{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px}
.mc .l{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.mc .v{font-size:1.65rem;font-weight:700;margin-top:3px;line-height:1}
.mc .s{font-size:11px;color:var(--muted);margin-top:3px}
.gg{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:8px;margin-bottom:18px}
.gi{padding:9px 13px;border-radius:8px;display:flex;align-items:center;gap:8px;font-size:13px}
.gp{background:#0a2e18;border:1px solid var(--green);color:var(--green)}
.gf{background:#3d0f0f;border:1px solid var(--red);color:var(--red)}
.gn{background:var(--bg3);border:1px solid var(--border);color:var(--muted)}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:14px}
.ch{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.ch h3{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:7px 9px;background:var(--bg3);color:var(--muted);font-size:10px;font-weight:700;text-transform:uppercase;border-bottom:1px solid var(--border)}
td{padding:7px 9px;border-bottom:1px solid var(--border)}tr:last-child td{border-bottom:none}tr:hover td{background:var(--bg3)}
.bw{background:#0a2e18;color:var(--green);font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px}
.bl{background:#3d0f0f;color:var(--red);font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px}
.log{background:var(--bg3);border-radius:7px;padding:10px 12px;font-family:monospace;font-size:11px;max-height:240px;overflow-y:auto;color:var(--muted);line-height:1.5}
.cw{position:relative;height:190px}
</style></head><body>
<nav>
  <span class="brand">TradeBot</span>
  <a href="/">Dashboard</a><a href="/setup">Setup</a>
  <span class="railway-badge">Railway Live</span>
  <div style="flex:1"></div>
  <span class="pill" id="ep">checking...</span>
  <span style="font-size:11px;color:var(--muted)" id="clk"></span>
</nav>
<div class="main">
  <div class="hr">
    <div>
      <div class="pt">TradeBot Dashboard</div>
      <div class="ps" id="upd">Loading...</div>
    </div>
    <button class="btn" onclick="loadAll()">&#8635; Refresh</button>
  </div>
  <div class="ss" id="ss"><div class="si"><span class="d dm"></span>Connecting to Railway...</div></div>
  <div class="mg">
    <div class="mc"><div class="l">Net P&amp;L</div><div class="v" id="mpnl" style="color:var(--teal)">&#8212;</div><div class="s">after costs</div></div>
    <div class="mc"><div class="l">Win rate</div><div class="v" id="mwr" style="color:var(--green)">&#8212;</div><div class="s">target &#8805;55%</div></div>
    <div class="mc"><div class="l">Prof. factor</div><div class="v" id="mpf">&#8212;</div><div class="s">target &#8805;1.4</div></div>
    <div class="mc"><div class="l">Trades</div><div class="v" id="mtr" style="color:var(--amber)">&#8212;</div><div class="s">target &#8805;200</div></div>
    <div class="mc"><div class="l">Sharpe</div><div class="v" id="msh">&#8212;</div><div class="s">target &#8805;0.8</div></div>
    <div class="mc"><div class="l">Max DD</div><div class="v" id="mdd" style="color:var(--red)">&#8212;</div><div class="s" id="m-dd-limit">limit: {{ MAX_DD_K }}</div></div>
    <div class="mc"><div class="l">Expectancy</div><div class="v" id="mex">&#8212;</div><div class="s">avg/trade</div></div>
  </div>
  <div class="card"><div class="ch"><h3>Gate check &#8212; all green before live capital</h3></div>
    <div class="gg" id="gg"><div class="gi gn"><span class="d dm"></span>Loading...</div></div></div>
  <div class="card"><div class="ch"><h3>Equity curve</h3></div><div class="cw"><canvas id="ec"></canvas></div></div>
  <div class="card">
    <div class="ch"><h3>Recent trades</h3><span style="font-size:11px;color:var(--muted)" id="tc"></span></div>
    <div style="overflow-x:auto"><table>
      <thead><tr><th>Time</th><th>Strategy</th><th>Dir</th><th>Entry</th><th>Exit</th><th>Reason</th><th>Net P&amp;L</th><th></th></tr></thead>
      <tbody id="tb"><tr><td colspan="8" style="color:var(--muted);text-align:center;padding:16px">No trades yet &#8212; start paper trading or run simulation</td></tr></tbody>
    </table></div>
  </div>
  <div class="card"><div class="ch"><h3>Engine log</h3><button class="btn" onclick="loadLogs()">Refresh</button></div>
    <div class="log" id="lg">Waiting for engine to push logs...</div></div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
const CUR="{{ CUR }}",LOCALE="{{ LOCALE }}",JS_TZ="{{ JS_TZ }}",MAX_DD_K="{{ MAX_DD_K }}";
{{ FMT_JS }}
let ec=null;
setInterval(()=>document.getElementById('clk').textContent=new Date().toLocaleTimeString(LOCALE,{timeZone:JS_TZ}),1000);
async function loadStatus(){const d=await fetch('/api/status').then(r=>r.json());const e=d.engine||{};
  document.getElementById('ep').className='pill'+(e.running?' on':'');
  document.getElementById('ep').textContent=e.running?'Engine live':'Engine offline';
  document.getElementById('ss').innerHTML=[
    {l:'Broker token',ok:d.broker_token_set},
    {l:'Engine',ok:!!e.running},
    {l:'Trades ('+d.db_trades+')',ok:d.db_trades>0},
    {l:'Railway &#10003;',ok:true},
    {l:(d.market||'US')+' market',ok:true},
  ].map(i=>`<div class="si"><span class="d ${i.ok?'dg':'dr'}"></span>${i.l}</div>`).join('');}
async function loadMetrics(){const d=await fetch('/api/metrics').then(r=>r.json());
  document.getElementById('mpnl').textContent=fmt(d.total_pnl);
  document.getElementById('mwr').textContent=d.win_rate+'%';
  document.getElementById('mpf').textContent=d.profit_factor;
  document.getElementById('mtr').textContent=d.total_trades;
  document.getElementById('msh').textContent=d.sharpe;
  document.getElementById('mdd').textContent=fmt(d.max_drawdown);
  document.getElementById('mex').textContent=fmt(d.expectancy);
  const g=d.gate;
  document.getElementById('gg').innerHTML=[
    {l:'Win rate &#8805;55% ('+d.win_rate+'%)',p:g.win_rate},
    {l:'PF &#8805;1.4 ('+d.profit_factor+')',p:g.profit_factor},
    {l:'Max DD <'+MAX_DD_K+' ('+fmt(Math.abs(d.max_drawdown))+')',p:g.max_drawdown},
    {l:'Trades &#8805;200 ('+d.total_trades+')',p:g.min_trades},
  ].map(x=>`<div class="gi ${x.p?'gp':'gf'}"><span class="d ${x.p?'dg':'dr'}"></span>${x.l}</div>`).join('')+
  (g.all_pass?'<div class="gi gp" style="grid-column:1/-1"><span class="d dg"></span>ALL GATES PASSED &#8212; ready for live capital</div>':'');}
async function loadEquity(){const d=await fetch('/api/equity').then(r=>r.json());if(!d.equity.length)return;
  const ctx=document.getElementById('ec').getContext('2d');const last=d.equity[d.equity.length-1];const col=last>=d.capital?'#2dd4bf':'#f87171';
  if(ec)ec.destroy();ec=new Chart(ctx,{type:'line',data:{labels:d.equity.map((_,i)=>i+1),datasets:[{data:d.equity,borderColor:col,borderWidth:1.5,fill:true,backgroundColor:col+'18',tension:.3,pointRadius:0,pointHoverRadius:3}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{display:false},y:{ticks:{color:'#8892a4',callback:v=>CUR+Math.round(v).toLocaleString(LOCALE)},grid:{color:'#2a3245'}}}}});}
async function loadTrades(){const d=await fetch('/api/trades?limit=30').then(r=>r.json());
  document.getElementById('tc').textContent=d.total+' total';if(!d.trades.length)return;
  document.getElementById('tb').innerHTML=d.trades.map(t=>{const p=parseFloat(t.net_pnl||0),w=p>0;
    const dc=(t.direction||'').includes('LONG')?'var(--green)':(t.direction||'').includes('OPTIONS')?'#2dd4bf':'var(--red)';
    return`<tr><td style="color:var(--muted);white-space:nowrap">${(t.entry_time||'').toString().slice(0,16)}</td><td>${t.strategy||'-'}</td><td style="color:${dc}">${t.direction||'-'}</td><td>${CUR}${Math.round(t.entry_price||0)}</td><td>${CUR}${Math.round(t.exit_price||0)}</td><td style="color:var(--muted)">${t.exit_reason||'-'}</td><td style="color:${w?'var(--green)':'var(--red)'};">${w?'+':''}${fmt(p)}</td><td><span class="${w?'bw':'bl'}">${w?'WIN':'LOSS'}</span></td></tr>`;
  }).join('');}
async function loadLogs(){const d=await fetch('/api/logs').then(r=>r.json());const b=document.getElementById('lg');
  if(!d.lines.length){b.textContent='No logs yet. Start the engine.';return;}
  b.innerHTML=d.lines.map(l=>`<div style="color:${l.includes('ERROR')?'var(--red)':l.includes('WARN')?'var(--amber)':l.includes('WIN')||l.includes('FILL')||l.includes('TARGET')?'var(--green)':'var(--muted)'}">${l}</div>`).join('');b.scrollTop=b.scrollHeight;}
async function loadAll(){document.getElementById('upd').textContent='Updated: '+new Date().toLocaleTimeString(LOCALE)+' '+CUR.replace('$','').replace('₹','')+' market';
  await Promise.all([loadStatus(),loadMetrics(),loadEquity(),loadTrades(),loadLogs()]);}
loadAll();setInterval(loadAll,30000);
</script></body></html>"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  TradeBot Web App  [{_MARKET} market]")
    print(f"  Dashboard:   http://localhost:{port}/")
    print(f"  Setup guide: http://localhost:{port}/setup")
    print(f"  Callback:    http://localhost:{port}/callback")
    print(f"  Railway URL: {RAILWAY_URL}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
