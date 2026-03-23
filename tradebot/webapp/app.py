# ============================================================
#  tradebot / webapp / app.py
#  Local Flask web application.
#
#  What this solves:
#    Angel One SmartAPI "Add App" form asks for a Redirect URL.
#    For PERSONAL use with TOTP login, this URL is NEVER called.
#    But Angel One's form still requires something in the field.
#    This app runs locally on http://localhost:5000 and:
#      - Provides a real local URL for the form (http://localhost:5000/callback)
#      - Serves the live trading dashboard
#      - Provides REST API endpoints for the trading engine
#      - Shows system status, logs, and P&L in a browser
#
#  How to fill the Angel One form:
#    App Name:          TradeBot (or anything)
#    Redirect URL:      http://localhost:5000/callback
#    Post back URL:     (leave blank)
#    Primary Static IP: (your public IP — get from: curl ifconfig.me)
#    Secondary IP:      (leave blank)
#
#  To find your public IP:
#    Open terminal → type: curl ifconfig.me
#    Copy that IP into the form.
#
#  Running:
#    python -m webapp.app              (development)
#    python -m webapp.app --port 8080  (custom port)
# ============================================================

import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)


def create_app():
    try:
        from flask import Flask, jsonify, render_template_string, request, redirect
    except ImportError:
        print("Flask not installed. Run: pip install flask")
        sys.exit(1)

    app = Flask(__name__)
    BASE = Path(__file__).parent.parent

    # ── Helper: load latest trade data ───────────────────────

    def _load_trades(symbol="BANKNIFTY"):
        import pandas as pd
        proc = BASE / "data" / "processed"
        frames = []
        for pattern in [f"paper_{symbol}_*.csv", f"live_{symbol}_*.csv", f"wf_trades_{symbol}_*.csv"]:
            for f in sorted(proc.glob(pattern)):
                try:
                    df = pd.read_csv(f)
                    df["_source"] = f.stem
                    frames.append(df)
                except Exception:
                    pass
        if not frames:
            return None
        import pandas as pd
        combined = pd.concat(frames, ignore_index=True)
        return combined

    def _get_metrics(trades):
        if trades is None or "net_pnl" not in trades.columns or trades.empty:
            return {"total_trades": 0, "win_rate": 0, "profit_factor": 0,
                    "total_pnl": 0, "max_drawdown": 0, "sharpe": 0, "expectancy": 0}
        import numpy as np
        pnl  = trades["net_pnl"]
        wins = pnl[pnl > 0]
        loss = pnl[pnl < 0]
        cum  = pnl.cumsum()
        dd   = (cum - cum.cummax()).min()
        pf   = wins.sum() / abs(loss.sum()) if len(loss) > 0 else 0
        wr   = len(wins) / len(pnl) * 100 if len(pnl) > 0 else 0
        sh   = pnl.mean() / pnl.std() * (252 ** 0.5) if pnl.std() > 0 else 0
        return {
            "total_trades":  int(len(pnl)),
            "win_rate":      round(wr, 1),
            "profit_factor": round(pf, 2),
            "total_pnl":     round(float(pnl.sum()), 2),
            "max_drawdown":  round(float(dd), 2),
            "sharpe":        round(float(sh), 2),
            "expectancy":    round(float(pnl.mean()), 2),
        }

    # ── Routes ────────────────────────────────────────────────

    @app.route("/")
    def index():
        """Main dashboard page."""
        return render_template_string(DASHBOARD_HTML)

    @app.route("/callback")
    def callback():
        """
        Angel One OAuth callback handler.
        For TOTP-based personal login this is never actually called.
        But having a real URL satisfies the SmartAPI app registration form.
        """
        auth_token = request.args.get("auth_token", "")
        feed_token = request.args.get("feed_token", "")
        if auth_token:
            # Save tokens if they arrive via OAuth
            (BASE / ".angel_auth_token").write_text(auth_token)
            if feed_token:
                (BASE / ".angel_feed_token").write_text(feed_token)
            logger.info("Angel One OAuth callback received and tokens saved")
            return render_template_string("""
            <html><body style="font-family:sans-serif;padding:40px;background:#0f1117;color:#e2e8f0;">
            <h2 style="color:#2dd4bf">✓ Angel One connected</h2>
            <p>Tokens saved. You can close this window and return to the terminal.</p>
            </body></html>
            """)
        return render_template_string("""
        <html><body style="font-family:sans-serif;padding:40px;background:#0f1117;color:#e2e8f0;">
        <h2 style="color:#2dd4bf">TradeBot — Callback endpoint</h2>
        <p style="color:#8892a4">This URL is registered with Angel One SmartAPI.<br>
        For TOTP-based login, tokens are generated automatically — no action needed here.</p>
        </body></html>
        """)

    @app.route("/api/status")
    def api_status():
        """System health check — used by dashboard."""
        checks = {}

        # Check broker tokens
        for name in ["angel_auth_token", "angel_feed_token", "zerodha_token", "fyers_token"]:
            p = BASE / f".{name}"
            checks[name] = p.exists()

        # Check data cache
        db = BASE / "data" / "market_data.db"
        checks["data_db"] = db.exists()
        checks["data_db_size_mb"] = round(db.stat().st_size / 1e6, 1) if db.exists() else 0

        # Check AI models
        model_dir = BASE / "data" / "models"
        checks["ai_models"] = len(list(model_dir.glob("*.pkl"))) if model_dir.exists() else 0

        # Check trade logs
        proc = BASE / "data" / "processed"
        checks["trade_files"] = len(list(proc.glob("*.csv"))) if proc.exists() else 0

        # Engine running (check for PID file)
        pid_file = BASE / ".engine.pid"
        checks["engine_running"] = pid_file.exists()

        return jsonify({"status": "ok", "checks": checks, "timestamp": datetime.now().isoformat()})

    @app.route("/api/metrics")
    def api_metrics():
        symbol = request.args.get("symbol", "BANKNIFTY")
        trades = _load_trades(symbol)
        metrics = _get_metrics(trades)
        # Gate check
        metrics["gate"] = {
            "win_rate":      metrics["win_rate"] >= 55,
            "profit_factor": metrics["profit_factor"] >= 1.4,
            "max_drawdown":  abs(metrics["max_drawdown"]) < 12000,
            "min_trades":    metrics["total_trades"] >= 200,
        }
        metrics["gate"]["all_pass"] = all(metrics["gate"].values())
        return jsonify(metrics)

    @app.route("/api/trades")
    def api_trades():
        symbol = request.args.get("symbol", "BANKNIFTY")
        limit  = int(request.args.get("limit", 50))
        trades = _load_trades(symbol)
        if trades is None:
            return jsonify({"trades": [], "total": 0})
        recent = trades.tail(limit)
        # Serialise safely
        records = []
        for _, row in recent.iterrows():
            records.append({k: (str(v) if not isinstance(v, (int, float, bool)) else v)
                           for k, v in row.items() if not str(k).startswith("_")})
        return jsonify({"trades": records, "total": len(trades)})

    @app.route("/api/equity")
    def api_equity():
        symbol  = request.args.get("symbol", "BANKNIFTY")
        capital = float(request.args.get("capital", 100000))
        trades  = _load_trades(symbol)
        if trades is None or "net_pnl" not in trades.columns:
            return jsonify({"equity": []})
        pnl     = trades["net_pnl"].tolist()
        running = capital
        curve   = []
        for p in pnl:
            running += p
            curve.append(round(running, 2))
        return jsonify({"equity": curve, "capital": capital})

    @app.route("/api/logs")
    def api_logs():
        log_file = BASE / "logs" / "tradebot.log"
        if not log_file.exists():
            return jsonify({"lines": []})
        lines = log_file.read_text(errors="replace").splitlines()
        return jsonify({"lines": lines[-100:]})

    @app.route("/api/ip")
    def api_ip():
        """Returns this machine's public IP — paste into Angel One Static IP field."""
        import urllib.request
        try:
            ip = urllib.request.urlopen("https://ifconfig.me", timeout=5).read().decode().strip()
        except Exception:
            ip = "Could not fetch — open terminal and run: curl ifconfig.me"
        return jsonify({"public_ip": ip})

    @app.route("/setup-guide")
    def setup_guide():
        return render_template_string(SETUP_HTML)

    return app


# ── Dashboard HTML (served at /) ──────────────────────────────

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TradeBot Dashboard</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0f1117;--bg2:#161b27;--bg3:#1e2536;--border:#2a3245;--text:#e2e8f0;--muted:#8892a4;--teal:#2dd4bf;--amber:#fbbf24;--green:#4ade80;--red:#f87171;--blue:#60a5fa;--font:'Inter',-apple-system,sans-serif}
body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px;line-height:1.6}
.nav{background:var(--bg2);border-bottom:1px solid var(--border);padding:0 24px;display:flex;align-items:center;gap:24px;height:52px}
.nav-brand{font-size:1.1rem;font-weight:700;color:var(--teal)}
.nav a{color:var(--muted);text-decoration:none;font-size:13px;padding:4px 0}
.nav a:hover{color:var(--text)}
.page{max-width:1100px;margin:0 auto;padding:28px 24px}
.grid4{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:28px}
.metric{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:18px}
.metric .lbl{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.metric .val{font-size:1.9rem;font-weight:700;margin-top:4px;line-height:1}
.metric .sub{font-size:12px;color:var(--muted);margin-top:5px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:20px;margin-bottom:20px}
.card h3{font-size:.9rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:14px}
.gate-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.gate-item{padding:12px 16px;border-radius:8px;display:flex;align-items:center;gap:10px;font-size:13px}
.gate-pass{background:#0a2e18;border:1px solid var(--green);color:var(--green)}
.gate-fail{background:#3d0f0f;border:1px solid var(--red);color:var(--red)}
.gate-pend{background:#1e2536;border:1px solid var(--border);color:var(--muted)}
.dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;padding:8px 10px;background:var(--bg3);color:var(--muted);font-size:11px;font-weight:700;text-transform:uppercase;border-bottom:1px solid var(--border)}
td{padding:8px 10px;border-bottom:1px solid var(--border)}
tr:hover td{background:var(--bg3)}
.badge{font-size:11px;font-weight:600;padding:2px 8px;border-radius:4px}
.win{background:#0a2e18;color:var(--green)} .loss{background:#3d0f0f;color:var(--red)}
.log-box{background:var(--bg3);border-radius:8px;padding:12px;font-family:monospace;font-size:11.5px;max-height:280px;overflow-y:auto;color:var(--muted);line-height:1.5}
.status-row{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px}
.status-item{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:8px 14px;font-size:12px;display:flex;align-items:center;gap:8px}
canvas{width:100%!important}
.refresh-btn{background:transparent;border:1px solid var(--border);color:var(--muted);padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px}
.refresh-btn:hover{background:var(--bg3);color:var(--text)}
.section-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.section-head h3{font-size:.9rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
</style>
</head>
<body>
<nav class="nav">
  <span class="nav-brand">TradeBot</span>
  <a href="/">Dashboard</a>
  <a href="/setup-guide">Setup Guide</a>
  <a href="/api/status" target="_blank">API Status</a>
</nav>
<div class="page">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
    <div>
      <h1 style="font-size:1.4rem;font-weight:700">Live Dashboard</h1>
      <div style="font-size:12px;color:var(--muted)" id="last-update">Loading...</div>
    </div>
    <button class="refresh-btn" onclick="loadAll()">Refresh</button>
  </div>

  <!-- System status -->
  <div class="status-row" id="status-row">
    <div class="status-item"><span class="dot" style="background:var(--muted)"></span> Loading...</div>
  </div>

  <!-- Metrics -->
  <div class="grid4">
    <div class="metric"><div class="lbl">Total P&L</div><div class="val" id="m-pnl" style="color:var(--teal)">—</div><div class="sub">net after costs</div></div>
    <div class="metric"><div class="lbl">Win rate</div><div class="val" id="m-wr" style="color:var(--green)">—</div><div class="sub">target: ≥55%</div></div>
    <div class="metric"><div class="lbl">Profit factor</div><div class="val" id="m-pf" style="color:var(--purple,#a78bfa)">—</div><div class="sub">target: ≥1.4</div></div>
    <div class="metric"><div class="lbl">Trades</div><div class="val" id="m-tr" style="color:var(--amber)">—</div><div class="sub">target: ≥200</div></div>
    <div class="metric"><div class="lbl">Sharpe</div><div class="val" id="m-sh">—</div><div class="sub">target: ≥0.8</div></div>
    <div class="metric"><div class="lbl">Max drawdown</div><div class="val" id="m-dd" style="color:var(--red)">—</div><div class="sub">limit: ₹12,000</div></div>
    <div class="metric"><div class="lbl">Expectancy</div><div class="val" id="m-ex">—</div><div class="sub">per trade avg</div></div>
  </div>

  <!-- Gate check -->
  <div class="card">
    <h3>Gate check — all must pass before live capital</h3>
    <div class="gate-grid" id="gate-grid">
      <div class="gate-item gate-pend"><span class="dot" style="background:var(--muted)"></span>Loading...</div>
    </div>
  </div>

  <!-- Equity curve -->
  <div class="card">
    <div class="section-head"><h3>Equity curve</h3></div>
    <canvas id="equity-chart" height="200"></canvas>
  </div>

  <!-- Recent trades -->
  <div class="card">
    <div class="section-head"><h3>Recent trades</h3><span style="font-size:12px;color:var(--muted)" id="trade-count"></span></div>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Time</th><th>Strategy</th><th>Dir</th><th>Entry</th><th>Exit</th><th>Reason</th><th>Net P&L</th><th>Result</th></tr></thead>
        <tbody id="trade-tbody"><tr><td colspan="8" style="color:var(--muted);text-align:center;padding:20px">No trades yet</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- System logs -->
  <div class="card">
    <div class="section-head"><h3>System log (last 100 lines)</h3><button class="refresh-btn" onclick="loadLogs()">Refresh</button></div>
    <div class="log-box" id="log-box">Loading...</div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
let equityChart = null;

async function loadStatus() {
  const r = await fetch('/api/status');
  const d = await r.json();
  const c = d.checks;
  const items = [
    { label: 'Angel One token', ok: c.angel_auth_token },
    { label: 'Data DB (' + c.data_db_size_mb + ' MB)', ok: c.data_db },
    { label: 'AI models (' + c.ai_models + ')', ok: c.ai_models > 0 },
    { label: 'Trade logs (' + c.trade_files + ')', ok: c.trade_files > 0 },
    { label: 'Engine running', ok: c.engine_running },
  ];
  document.getElementById('status-row').innerHTML = items.map(i =>
    `<div class="status-item"><span class="dot" style="background:${i.ok?'var(--green)':'var(--red)'}"></span>${i.label}</div>`
  ).join('');
}

async function loadMetrics() {
  const r = await fetch('/api/metrics');
  const d = await r.json();
  const fmt = v => v >= 0 ? '₹'+v.toLocaleString('en-IN') : '-₹'+Math.abs(v).toLocaleString('en-IN');
  document.getElementById('m-pnl').textContent = fmt(d.total_pnl);
  document.getElementById('m-wr').textContent  = d.win_rate + '%';
  document.getElementById('m-pf').textContent  = d.profit_factor;
  document.getElementById('m-tr').textContent  = d.total_trades;
  document.getElementById('m-sh').textContent  = d.sharpe;
  document.getElementById('m-dd').textContent  = fmt(d.max_drawdown);
  document.getElementById('m-ex').textContent  = fmt(d.expectancy);

  const g = d.gate;
  const gates = [
    { label: 'Win rate ≥55% (' + document.getElementById('m-wr').textContent + ')', pass: g.win_rate },
    { label: 'Profit factor ≥1.4 (' + d.profit_factor + ')', pass: g.profit_factor },
    { label: 'Max DD <₹12,000', pass: g.max_drawdown },
    { label: 'Trades ≥200 (' + d.total_trades + ')', pass: g.min_trades },
  ];
  document.getElementById('gate-grid').innerHTML = gates.map(gi =>
    `<div class="gate-item ${gi.pass?'gate-pass':'gate-fail'}">
      <span class="dot" style="background:${gi.pass?'var(--green)':'var(--red)'}"></span>${gi.label}</div>`
  ).join('') + (g.all_pass
    ? '<div class="gate-item gate-pass" style="grid-column:1/-1"><span class="dot" style="background:var(--green)"></span>ALL GATES PASSED — ready for live capital</div>'
    : '');
}

async function loadEquity() {
  const r = await fetch('/api/equity');
  const d = await r.json();
  if (!d.equity.length) return;
  const ctx = document.getElementById('equity-chart').getContext('2d');
  const labels = d.equity.map((_, i) => 'T'+i);
  const color = d.equity[d.equity.length-1] >= d.capital ? '#2dd4bf' : '#f87171';
  if (equityChart) equityChart.destroy();
  equityChart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets: [{ label:'Equity', data: d.equity, borderColor: color, borderWidth:2, fill:true, backgroundColor: color+'22', tension:.3, pointRadius:0, pointHoverRadius:3 }] },
    options: { responsive:true, maintainAspectRatio:false, plugins:{ legend:{display:false} }, scales:{ x:{display:false}, y:{ ticks:{ color:'#8892a4', callback:v=>'₹'+v.toLocaleString('en-IN') }, grid:{color:'#2a3245'} } } }
  });
}

async function loadTrades() {
  const r = await fetch('/api/trades?limit=30');
  const d = await r.json();
  document.getElementById('trade-count').textContent = d.total + ' total trades';
  if (!d.trades.length) return;
  document.getElementById('trade-tbody').innerHTML = d.trades.reverse().map(t => {
    const pnl = parseFloat(t.net_pnl || 0);
    const win = pnl > 0;
    const ts = (t.entry_time || t.time || '').toString().slice(0,16);
    return `<tr>
      <td style="color:var(--muted)">${ts}</td>
      <td>${t.strategy||'-'}</td>
      <td style="color:${(t.direction||'').includes('LONG')?'var(--green)':'var(--red)'}">${t.direction||'-'}</td>
      <td>₹${parseFloat(t.entry_price||0).toFixed(0)}</td>
      <td>₹${parseFloat(t.exit_price||0).toFixed(0)}</td>
      <td style="color:var(--muted)">${t.exit_reason||'-'}</td>
      <td style="color:${win?'var(--green)':'var(--red)'}">${win?'+':''}₹${pnl.toFixed(0)}</td>
      <td><span class="badge ${win?'win':'loss'}">${win?'WIN':'LOSS'}</span></td>
    </tr>`;
  }).join('');
}

async function loadLogs() {
  const r = await fetch('/api/logs');
  const d = await r.json();
  const box = document.getElementById('log-box');
  if (!d.lines.length) { box.textContent = 'No logs yet. Start the engine first.'; return; }
  box.innerHTML = d.lines.map(l => {
    const color = l.includes('ERROR')?'var(--red)': l.includes('WARN')?'var(--amber)': l.includes('FILLED')||l.includes('TARGET')?'var(--green)': 'var(--muted)';
    return `<div style="color:${color}">${l}</div>`;
  }).join('');
  box.scrollTop = box.scrollHeight;
}

async function loadAll() {
  document.getElementById('last-update').textContent = 'Updated: ' + new Date().toLocaleTimeString('en-IN');
  await Promise.all([loadStatus(), loadMetrics(), loadEquity(), loadTrades(), loadLogs()]);
}

loadAll();
setInterval(loadAll, 30000);
</script>
</body>
</html>
"""

# ── Setup guide HTML ──────────────────────────────────────────

SETUP_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>TradeBot — Setup Guide</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0f1117;color:#e2e8f0;font-family:-apple-system,sans-serif;font-size:14px;line-height:1.7}
.nav{background:#161b27;border-bottom:1px solid #2a3245;padding:0 24px;display:flex;align-items:center;gap:24px;height:52px}
.nav-brand{font-size:1.1rem;font-weight:700;color:#2dd4bf}
.nav a{color:#8892a4;text-decoration:none;font-size:13px}
.nav a:hover{color:#e2e8f0}
.page{max-width:800px;margin:0 auto;padding:36px 24px}
h1{font-size:1.6rem;font-weight:700;margin-bottom:6px}
h2{font-size:1.1rem;font-weight:600;color:#2dd4bf;margin:32px 0 12px}
p,li{color:#8892a4;margin-bottom:8px}
li{margin-left:20px}
.step{background:#161b27;border:1px solid #2a3245;border-radius:10px;padding:20px;margin-bottom:16px;position:relative;padding-left:60px}
.step-num{position:absolute;left:18px;top:18px;width:28px;height:28px;border-radius:50%;background:#0d3330;border:1.5px solid #2dd4bf;color:#2dd4bf;font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center}
.step h3{font-size:.95rem;font-weight:600;margin-bottom:6px;color:#e2e8f0}
pre,code{background:#1e2536;border-radius:6px;font-family:monospace;color:#2dd4bf}
pre{padding:12px 14px;overflow-x:auto;margin:10px 0;font-size:12.5px;border:1px solid #2a3245}
code{padding:2px 6px;font-size:12px}
.callout{background:#3d2e0a;border:1px solid #fbbf24;border-radius:8px;padding:14px 18px;margin:16px 0}
.callout strong{color:#fbbf24;display:block;margin-bottom:4px;font-size:12px;text-transform:uppercase;letter-spacing:.4px}
.ip-box{background:#161b27;border:1px solid #2dd4bf;border-radius:8px;padding:16px;margin:12px 0;text-align:center}
.ip-val{font-size:1.4rem;font-weight:700;color:#2dd4bf;font-family:monospace}
</style>
</head>
<body>
<nav class="nav">
  <span class="nav-brand">TradeBot</span>
  <a href="/">Dashboard</a>
  <a href="/setup-guide">Setup Guide</a>
</nav>
<div class="page">
  <h1>Setup Guide</h1>
  <p>Complete reference for setting up TradeBot with Angel One SmartAPI — step by step.</p>

  <h2>Angel One SmartAPI — how to fill the form</h2>
  <div class="callout">
    <strong>Important</strong>
    For personal algo trading with TOTP login, the Redirect URL is <strong>never called</strong>.
    You just need to put a valid URL in the form. Use this local server's callback URL below.
    The Static IP is your router's public IP — Angel One uses it as a whitelist.
  </div>

  <div class="step">
    <div class="step-num">1</div>
    <h3>Get your public IP (paste into Static IP field)</h3>
    <p>Your current public IP (fetched live):</p>
    <div class="ip-box"><div class="ip-val" id="pub-ip">Loading...</div></div>
    <p>Copy the IP above into <strong>Primary Static IP</strong> in the Angel One form.</p>
    <p>Note: if your ISP gives you a dynamic IP, it may change. If API stops working, update this in your Angel One app settings.</p>
  </div>

  <div class="step">
    <div class="step-num">2</div>
    <h3>Fill the Angel One SmartAPI form exactly like this</h3>
    <pre>App Name:          TradeBot
Redirect URL:      http://localhost:5000/callback
Post back URL:     (leave empty)
Primary Static IP: [paste your IP from above]
Secondary IP:      (leave empty)</pre>
    <p>Click <strong>Add</strong>. You'll get an <strong>API Key</strong> on the next screen. Copy it.</p>
  </div>

  <div class="step">
    <div class="step-num">3</div>
    <h3>Enable TOTP in Angel One mobile app</h3>
    <pre>1. Open Angel One app on phone
2. Go to: Profile → Settings → Enable TOTP
3. A QR code will appear
4. DO NOT scan with Google Authenticator yet
5. Tap "Can't scan QR code?" to reveal the base32 secret
6. Copy that long letter+number string (e.g. JBSWY3DPEHPK3PXP)
7. That is your totp_secret — save it somewhere safe</pre>
  </div>

  <div class="step">
    <div class="step-num">4</div>
    <h3>Fill credentials in config/settings.py</h3>
    <pre>ANGEL_ONE = {
    "api_key":     "YOUR_API_KEY",      # from step 2
    "client_id":   "YOUR_CLIENT_ID",   # your Angel One login ID
    "password":    "YOUR_4_DIGIT_PIN", # trading PIN (not login password)
    "totp_secret": "YOUR_TOTP_SECRET", # base32 string from step 3
}
DATA_SOURCE = "angel"</pre>
  </div>

  <div class="step">
    <div class="step-num">5</div>
    <h3>Test the connection</h3>
    <pre>python generate_token_angel.py</pre>
    <p>If successful you'll see: <code>Logged in as: YOUR_NAME</code></p>
    <p>This also works as a cron job — no browser interaction needed, fully automated.</p>
  </div>

  <div class="step">
    <div class="step-num">6</div>
    <h3>Run the system</h3>
    <pre># Terminal 1: Start the trading engine (paper mode)
python main.py

# Terminal 2: Start this dashboard
python -m webapp.app

# Terminal 3: (optional) Run Streamlit dashboard as well
streamlit run monitor/dashboard.py</pre>
  </div>

  <h2>Zerodha — what you need</h2>
  <div class="step">
    <div class="step-num">—</div>
    <h3>Zerodha account (for execution only)</h3>
    <p>Zerodha execution APIs are now <strong>free</strong> (since April 2025). You only need to pay if you want their historical data (₹2,000/month) — but we're using Angel One for data instead.</p>
    <pre>1. Once account is confirmed, go to kite.trade
2. Create an app — Redirect URL: http://localhost:5000/callback
3. Get API Key and Secret
4. Fill in config/settings.py:
   ZERODHA = { "api_key": "...", "api_secret": "...", "user_id": "..." }
5. Run: python generate_token.py  (requires browser once/day)
   OR set ACTIVE_BROKER = "angel" to use Angel for both data + execution</pre>
  </div>
</div>

<script>
fetch('/api/ip').then(r=>r.json()).then(d=>{
  document.getElementById('pub-ip').textContent = d.public_ip;
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import argparse
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from utils.logger import setup_logging
    setup_logging("webapp")

    parser = argparse.ArgumentParser(description="TradeBot Web App")
    parser.add_argument("--port", default=5000, type=int)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    print(f"\n{'='*55}")
    print(f"  TradeBot Web App")
    print(f"{'='*55}")
    print(f"  Dashboard:    http://localhost:{args.port}/")
    print(f"  Setup guide:  http://localhost:{args.port}/setup-guide")
    print(f"  API callback: http://localhost:{args.port}/callback")
    print(f"{'='*55}\n")
    print("  Use http://localhost:5000/callback as Redirect URL in Angel One form.")
    print("  Use the Setup Guide page to get your public IP for the Static IP field.\n")

    app = create_app()
    app.run(host=args.host, port=args.port, debug=False)
