from __future__ import annotations


SETUP_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TradeBot Setup</title>
  <style>
    *{box-sizing:border-box}body{margin:0;font-family:Segoe UI,Arial,sans-serif;background:#09111d;color:#dce6f2}
    nav{display:flex;align-items:center;gap:16px;padding:14px 20px;background:#0f1b2d;border-bottom:1px solid #22324b}
    nav a{color:#89a2c0;text-decoration:none}nav strong{color:#9bd2be}
    .page{max-width:880px;margin:0 auto;padding:28px 18px 40px}
    .panel{background:#0f1b2d;border:1px solid #22324b;border-radius:16px;padding:20px;margin-bottom:16px}
    h1,h2{margin:0 0 12px}.tag{display:inline-block;padding:5px 10px;border-radius:999px;background:#183049;color:#9bd2be;font-size:12px}
    pre{background:#07101b;border:1px solid #22324b;border-radius:12px;padding:14px;overflow:auto;color:#c5d5e6}
    .warn{border-color:#5d4217;background:#24180b;color:#f1c678}
  </style>
</head>
<body>
  <nav>
    <strong>TradeBot</strong>
    <a href="/">Dashboard</a>
    <a href="/setup">Setup</a>
  </nav>
  <div class="page">
    <div class="panel">
      <span class="tag">Railway Relay</span>
      <h1>Production Dashboard Setup</h1>
      <p>This relay accepts engine pushes, runs the market simulator, and serves the analysis dashboard.</p>
    </div>
    <div class="panel">
      <h2>Core Environment</h2>
      <pre>TRADEBOT_KEY=your_secret
WEBAPP_URL={{ base_url }}
MARKET={{ market }}
RISK_CAPITAL=100000
MAX_DAILY_LOSS_PCT=3.0
MAX_DD_PCT=10.0</pre>
    </div>
    <div class="panel">
      <h2>Callback</h2>
      <pre>{{ callback_url }}</pre>
    </div>
    <div class="panel warn">
      <h2>Production Note</h2>
      <p>Keep <code>TRADEBOT_KEY</code> only in Railway environment variables and local <code>.env</code>. Do not hardcode it in committed files.</p>
    </div>
  </div>
</body>
</html>"""


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TradeBot Professional Dashboard</title>
  <style>
    *{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top,#12243c 0%,#08111d 42%,#050b14 100%);color:#e4eef8;font-family:Segoe UI,Arial,sans-serif}
    :root{--panel:#0d1828;--panel2:#132238;--border:#243955;--muted:#8ea4bf;--accent:#29c08f;--warn:#ffb252;--danger:#ff6d6d;--info:#54b3ff}
    nav{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:18px;padding:14px 18px;background:rgba(5,11,20,.92);backdrop-filter:blur(12px);border-bottom:1px solid var(--border)}
    .brand{font-size:20px;font-weight:700;color:#9bd2be}.subtle{color:var(--muted);font-size:12px}
    .tabs{display:flex;gap:8px;flex-wrap:wrap}.tab-btn{border:1px solid var(--border);background:#0b1523;color:#bfd1e6;border-radius:999px;padding:8px 14px;cursor:pointer}
    .tab-btn.active{background:#12314a;color:white;border-color:#3d6b96}
    .status-pill{margin-left:auto;padding:7px 12px;border-radius:999px;border:1px solid var(--border);font-size:12px}.status-pill.live{color:var(--accent);border-color:#1f7b60;background:#0c241d}.status-pill.halt{color:var(--danger);border-color:#883737;background:#261214}.status-pill.sim{color:#8fc9ff;border-color:#335f84;background:#0d1f31}
    .page{max-width:1440px;margin:0 auto;padding:18px}
    .hero{display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-bottom:18px}
    .panel{background:linear-gradient(180deg,rgba(19,34,56,.92),rgba(11,20,34,.94));border:1px solid var(--border);border-radius:18px;padding:18px;box-shadow:0 18px 40px rgba(0,0,0,.22)}
    .panel h2,.panel h3{margin:0 0 12px}.hero h1{margin:0;font-size:34px;line-height:1.05}.hero p{color:#b6c7d9;max-width:800px}
    .grid-4{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.grid-3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.grid-2{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
    .kpi{padding:16px;border-radius:16px;background:rgba(7,13,22,.65);border:1px solid #20314b}.kpi-label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}.kpi-value{margin-top:8px;font-size:28px;font-weight:700}.kpi-sub{margin-top:6px;font-size:12px;color:var(--muted)}
    .flow{display:grid;grid-template-columns:repeat(7,1fr);gap:10px}.flow-step{padding:12px;border-radius:14px;background:#0a1320;border:1px solid #20314b;min-height:92px}.flow-step.ready{border-color:#296b52}.flow-step .step-name{font-size:12px;color:#cfe1f2}.flow-step .step-detail{margin-top:10px;font-size:12px;color:var(--muted)}
    .section-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.badge{display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;background:#183049;color:#9bd2be}
    .tab-pane{display:none}.tab-pane.active{display:block}
    .market-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.market-card{padding:16px;border-radius:16px;background:#0a1320;border:1px solid #20314b}.market-card .flag{font-size:12px;color:#9bd2be;letter-spacing:.12em;text-transform:uppercase}.market-card .status{margin-top:10px;font-size:12px}.market-card .status.open{color:var(--accent)}.market-card .status.closed{color:var(--danger)}.market-card .status.lunch{color:var(--warn)}
    .instrument-row,.table-row{display:grid;gap:12px;align-items:center}.instrument-row{grid-template-columns:1.1fr .9fr .7fr}.table-head,.table-row{padding:10px 0;border-bottom:1px solid rgba(36,57,85,.65)}.table-head{color:var(--muted);font-size:12px;text-transform:uppercase}.table-row:last-child{border-bottom:none}
    table{width:100%;border-collapse:collapse}th,td{padding:10px 8px;border-bottom:1px solid rgba(36,57,85,.6);text-align:left;font-size:13px}th{color:var(--muted);font-size:12px;text-transform:uppercase}
    .positive{color:var(--accent)}.negative{color:var(--danger)}.neutral{color:var(--muted)}
    .score{display:flex;align-items:center;gap:10px}.score-bar{flex:1;height:8px;border-radius:999px;background:#0a1320;border:1px solid #20314b;overflow:hidden}.score-fill{height:100%;background:linear-gradient(90deg,#ff6d6d,#ffcf5f,#29c08f)}
    .controls{display:flex;gap:10px;flex-wrap:wrap}.btn,.select{border:1px solid var(--border);background:#0a1320;color:#e4eef8;border-radius:12px;padding:10px 14px}.btn{cursor:pointer}.btn.primary{background:#12314a;border-color:#3d6b96}.btn.good{background:#0d2b21;border-color:#1d6b51}.btn.danger{background:#2d1418;border-color:#81353f}.btn.warn{background:#2f2414;border-color:#8e6730}
    .small{font-size:12px;color:var(--muted)}
    .chart-box{height:360px;border-radius:16px;background:#07101b;border:1px solid #20314b}
    .mini-list{display:flex;flex-direction:column;gap:10px}.news-item{padding:12px;border-radius:12px;background:#0a1320;border:1px solid #20314b}.news-item .meta{font-size:11px;color:var(--muted);margin-bottom:6px}
    .logs{height:420px;overflow:auto;padding:14px;border-radius:16px;background:#07101b;border:1px solid #20314b;font-family:Cascadia Mono,Consolas,monospace;font-size:12px}
    .toast-wrap{position:fixed;right:18px;bottom:18px;display:flex;flex-direction:column;gap:10px;z-index:30}.toast{padding:12px 14px;border-radius:12px;background:#0f1b2d;border:1px solid #27415f;min-width:280px}
    .report-cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
    @media (max-width:1200px){.market-grid,.grid-4,.report-cards,.flow{grid-template-columns:repeat(2,1fr)}.hero{grid-template-columns:1fr}.grid-3,.grid-2{grid-template-columns:1fr}}
    @media (max-width:720px){.market-grid,.grid-4,.report-cards,.flow{grid-template-columns:1fr}.tabs{overflow:auto}.page{padding:12px}}
  </style>
</head>
<body>
  <nav>
    <div>
      <div class="brand">TradeBot</div>
      <div class="subtle">Professional multi-asset dashboard</div>
    </div>
    <div class="tabs">
      <button class="tab-btn active" data-tab="dashboard">Dashboard</button>
      <button class="tab-btn" data-tab="markets">Markets</button>
      <button class="tab-btn" data-tab="analysis">Analysis</button>
      <button class="tab-btn" data-tab="signals">Signals</button>
      <button class="tab-btn" data-tab="reports">Reports</button>
      <button class="tab-btn" data-tab="logs">Logs</button>
    </div>
    <div class="status-pill" id="engine-pill">Connecting</div>
  </nav>
  <div class="page">
    <div class="hero">
      <div class="panel">
        <span class="badge">24h Markets • Analysis Flow • Automation</span>
        <h1>Unified analysis, execution simulation, and drill-down reporting.</h1>
        <p>The dashboard now follows the full workflow: instrument selection, sentiment analysis, fundamental analysis, realtime news synthesis, trend chart analysis, entry and exit prediction, and automated trade readiness.</p>
      </div>
      <div class="panel">
        <div class="section-title"><h3>Simulator</h3><span class="small" id="clock"></span></div>
        <div class="controls">
          <select class="select" id="sim-market"></select>
          <select class="select" id="sim-speed">
            <option value="2">Turbo 2s</option>
            <option value="5" selected>Normal 5s</option>
            <option value="10">Slow 10s</option>
          </select>
          <button class="btn good" id="sim-start">Start</button>
          <button class="btn danger" id="sim-stop">Stop</button>
          <button class="btn warn" id="sim-reset">Reset</button>
        </div>
        <div class="small" id="sim-summary" style="margin-top:12px">Waiting for simulator.</div>
      </div>
    </div>

    <section class="tab-pane active" id="tab-dashboard">
      <div class="grid-4" id="kpi-grid"></div>
      <div class="grid-2" style="margin-top:16px">
        <div class="panel">
          <div class="section-title"><h3>Equity Curve</h3><span class="small" id="eq-info"></span></div>
          <div class="chart-box"><canvas id="equity-chart"></canvas></div>
        </div>
        <div class="panel">
          <div class="section-title"><h3>Risk Guardrails</h3><span class="small" id="risk-state"></span></div>
          <div id="risk-grid"></div>
          <div style="margin-top:18px"><canvas id="drawdown-chart"></canvas></div>
        </div>
      </div>
      <div class="panel" style="margin-top:16px">
        <div class="section-title"><h3>Recent Trades</h3><span class="small" id="trade-count"></span></div>
        <table>
          <thead><tr><th>Symbol</th><th>Market</th><th>Strategy</th><th>Direction</th><th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Confidence</th></tr></thead>
          <tbody id="trade-body"></tbody>
        </table>
      </div>
    </section>

    <section class="tab-pane" id="tab-markets">
      <div class="panel">
        <div class="section-title"><h3>Live Market Coverage</h3><span class="small">Cash, FX, and Crypto with session-aware status</span></div>
        <div class="market-grid" id="market-grid"></div>
      </div>
      <div class="grid-2" style="margin-top:16px">
        <div class="panel">
          <div class="section-title"><h3>Market Instrument Board</h3><span class="small" id="market-board-title">Select a market</span></div>
          <div id="instrument-board"></div>
        </div>
        <div class="panel">
          <div class="section-title"><h3>Coverage Notes</h3></div>
          <div class="mini-list">
            <div class="news-item"><div class="meta">24x5 Forex</div><div>Session overlays track Sydney, Tokyo, London, and New York handoff so intraday volatility shifts are visible.</div></div>
            <div class="news-item"><div class="meta">24x7 Crypto</div><div>Weekend and off-hours flows remain active, with analysis and automation still available.</div></div>
            <div class="news-item"><div class="meta">Cash Markets</div><div>Lunch pauses and after-hours states are preserved for Asia and cash equities.</div></div>
          </div>
        </div>
      </div>
    </section>

    <section class="tab-pane" id="tab-analysis">
      <div class="panel">
        <div class="section-title"><h3>Instrument Analysis Workflow</h3><span class="small">Production-style drill down from idea to automated execution readiness</span></div>
        <div class="controls">
          <select class="select" id="analysis-symbol"></select>
          <button class="btn primary" id="analysis-refresh">Run Analysis</button>
        </div>
        <div class="flow" id="workflow-grid" style="margin-top:16px"></div>
      </div>
      <div class="grid-3" style="margin-top:16px">
        <div class="panel"><div class="section-title"><h3>Technicals</h3><span class="small" id="technical-score"></span></div><div id="technical-panel"></div></div>
        <div class="panel"><div class="section-title"><h3>Fundamentals</h3><span class="small" id="fundamental-score"></span></div><div id="fundamental-panel"></div></div>
        <div class="panel"><div class="section-title"><h3>Sentiment & News</h3><span class="small" id="sentiment-label"></span></div><div id="sentiment-panel"></div></div>
      </div>
      <div class="grid-2" style="margin-top:16px">
        <div class="panel">
          <div class="section-title"><h3>Trend Chart Analysis</h3><span class="small">Entry, stop, and target overlays</span></div>
          <div class="chart-box" id="price-chart"></div>
        </div>
        <div class="panel">
          <div class="section-title"><h3>Signal & Automation</h3><span class="small" id="signal-headline"></span></div>
          <div id="signal-panel"></div>
        </div>
      </div>
    </section>

    <section class="tab-pane" id="tab-signals">
      <div class="panel">
        <div class="section-title"><h3>Signal Log</h3><span class="small">Confidence-ranked entry and exit plans</span></div>
        <table>
          <thead><tr><th>Time</th><th>Symbol</th><th>Direction</th><th>Confidence</th><th>Entry</th><th>Stop</th><th>Target</th><th>Status</th></tr></thead>
          <tbody id="signal-body"></tbody>
        </table>
      </div>
    </section>

    <section class="tab-pane" id="tab-reports">
      <div class="report-cards" id="report-cards"></div>
      <div class="grid-2" style="margin-top:16px">
        <div class="panel">
          <div class="section-title"><h3>By Strategy</h3></div>
          <div id="report-strategy"></div>
        </div>
        <div class="panel">
          <div class="section-title"><h3>By Market / Hour</h3></div>
          <div id="report-market"></div>
          <div style="margin-top:18px" id="report-hour"></div>
        </div>
      </div>
    </section>

    <section class="tab-pane" id="tab-logs">
      <div class="panel">
        <div class="section-title"><h3>Operations Log</h3><button class="btn" id="log-refresh">Refresh</button></div>
        <div class="logs" id="log-box"></div>
      </div>
    </section>
  </div>
  <div class="toast-wrap" id="toast-wrap"></div>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
  <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
  <script>
    let C="{{ CUR }}",L="{{ LOCALE }}",TZ="{{ JS_TZ }}",TZL="{{ TZ_LABEL }}",DEFAULT_MARKET="{{ MARKET }}";
    let equityChart=null,drawdownChart=null,priceChart=null,candleSeries=null,lineSeries=null;
    let marketCache=[];
    let activeMarket=DEFAULT_MARKET;
    let activeSymbol="";
    const fmt=(value)=>{if(value===null||value===undefined||isNaN(value))return C+"0";return value<0?"-"+C+Math.abs(value).toLocaleString(L,{maximumFractionDigits:2}):C+value.toLocaleString(L,{maximumFractionDigits:2})};
    const pct=(value)=>`${value>=0?'+':''}${Number(value||0).toFixed(2)}%`;
    const cls=(value)=>value>0?'positive':value<0?'negative':'neutral';
    const q=(id)=>document.getElementById(id);

    async function fetchJSON(url, options){const response=await fetch(url,options);if(!response.ok)throw new Error(url+': '+response.status);return response.json();}
    function toast(message){const el=document.createElement('div');el.className='toast';el.textContent=message;q('toast-wrap').appendChild(el);setTimeout(()=>el.remove(),3500);}
    function setTab(name){document.querySelectorAll('.tab-btn').forEach(btn=>btn.classList.toggle('active',btn.dataset.tab===name));document.querySelectorAll('.tab-pane').forEach(pane=>pane.classList.toggle('active',pane.id===`tab-${name}`));window.location.hash=name;if(name==='analysis'&&activeSymbol)loadAnalysis(activeSymbol);if(name==='signals')loadSignals();if(name==='reports')loadReports();if(name==='logs')loadLogs();}
    document.querySelectorAll('.tab-btn').forEach(btn=>btn.addEventListener('click',()=>setTab(btn.dataset.tab)));
    window.addEventListener('hashchange',()=>{const tab=(window.location.hash||'#dashboard').slice(1);setTab(tab||'dashboard');});

    function renderKpis(metrics){
      const items=[
        ['NAV',fmt(metrics.nav),`${metrics.total_trades} trades tracked`],
        ['Daily P&L',fmt(metrics.daily_pnl),`live engine heartbeat`],
        ['Total P&L',fmt(metrics.total_pnl),`expectancy ${fmt(metrics.expectancy)}`],
        ['Win Rate',`${metrics.win_rate}%`,`profit factor ${metrics.profit_factor}`],
        ['Sharpe',metrics.sharpe,`sortino ${metrics.sortino}`],
        ['Max Drawdown',fmt(Math.abs(metrics.max_drawdown)),`calmar ${metrics.calmar}`],
        ['Gate',metrics.gate.all_pass?'Ready':'Not Ready',`min trades 200`],
        ['Automation',metrics.gate.all_pass?'Eligible':'Guarded',`threshold based`],
      ];
      q('kpi-grid').innerHTML=items.map(([label,value,sub])=>`<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div><div class="kpi-sub">${sub}</div></div>`).join('');
    }

    function renderRisk(status, metrics){
      const engine=status.engine||{};
      q('risk-state').textContent=engine.halted?'HALTED':'ACTIVE';
      const bars=[
        ['Daily Loss',Math.abs(engine.daily_pnl||0),status.risk_config.max_capital*status.risk_config.max_daily_loss_pct/100],
        ['Max Drawdown',Math.abs(metrics.max_drawdown||0),status.risk_config.max_capital*status.risk_config.max_dd_pct/100],
        ['Positions',engine.open_positions||0,status.risk_config.max_positions],
        ['Trades / Day',engine.trades_today||0,status.risk_config.max_trades_day],
      ];
      q('risk-grid').innerHTML=bars.map(([label,current,max])=>{const width=Math.min((current/max)*100,100)||0;return `<div style="margin-bottom:14px"><div class="small" style="display:flex;justify-content:space-between"><span>${label}</span><span>${Number(current).toFixed(1)} / ${Number(max).toFixed(1)}</span></div><div class="score-bar" style="margin-top:6px"><div class="score-fill" style="width:${width}%"></div></div></div>`}).join('');
    }

    function renderEquity(data){
      q('eq-info').textContent=`Peak ${fmt(data.peak)} • Current ${fmt(data.equity[data.equity.length-1]||0)}`;
      if(equityChart)equityChart.destroy();
      equityChart=new Chart(q('equity-chart'),{type:'line',data:{labels:data.equity.map((_,i)=>i),datasets:[{data:data.equity,borderColor:'#29c08f',backgroundColor:'rgba(41,192,143,.16)',fill:true,tension:.25,pointRadius:0}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{display:false},y:{ticks:{color:'#8ea4bf'}}}}});
      if(drawdownChart)drawdownChart.destroy();
      drawdownChart=new Chart(q('drawdown-chart'),{type:'bar',data:{labels:data.dd_pct.map((_,i)=>i),datasets:[{data:data.dd_pct,backgroundColor:'rgba(255,109,109,.45)',borderColor:'#ff6d6d'}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{display:false},y:{ticks:{color:'#8ea4bf'}}}}});
    }

    function renderTrades(data){
      q('trade-count').textContent=`${data.total} total trades`;
      q('trade-body').innerHTML=data.trades.length?data.trades.map(trade=>`<tr><td>${trade.symbol||''}</td><td>${trade.market_id||''}</td><td>${trade.strategy||''}</td><td class="${trade.direction==='LONG'?'positive':'negative'}">${trade.direction||''}</td><td>${fmt(Number(trade.entry_price||0))}</td><td>${fmt(Number(trade.exit_price||0))}</td><td class="${cls(Number(trade.net_pnl||0))}">${fmt(Number(trade.net_pnl||0))}</td><td>${Number(trade.confidence||0).toFixed(1)}</td></tr>`).join(''):`<tr><td colspan="8" class="small">No trades yet.</td></tr>`;
    }

    function renderMarkets(markets){
      marketCache=markets;
      q('sim-market').innerHTML=markets.map(m=>`<option value="${m.id}" ${m.id===activeMarket?'selected':''}>${m.name}</option>`).join('');
      q('analysis-symbol').innerHTML=markets.map(m=>`<optgroup label="${m.name}">${m.instrument_data.map(i=>`<option value="${i.symbol}">${i.symbol}</option>`).join('')}</optgroup>`).join('');
      if(!activeSymbol&&markets[0]&&markets[0].instrument_data[0]){activeSymbol=markets[0].instrument_data[0].symbol;q('analysis-symbol').value=activeSymbol;}
      q('market-grid').innerHTML=markets.map(m=>`<div class="market-card" data-market="${m.id}"><div class="flag">${m.flag} • ${m.type}</div><h3>${m.name}</h3><div class="small">${m.index}</div><div class="status ${m.status}">${m.status.toUpperCase()} • ${m.session}</div><div class="small" style="margin-top:6px">${m.local_time} • ${m.schedule_label}</div><div class="small" style="margin-top:10px">${m.instrument_count} instruments</div></div>`).join('');
      document.querySelectorAll('.market-card').forEach(card=>card.addEventListener('click',()=>selectMarket(card.dataset.market)));
      selectMarket(activeMarket);
    }

    function selectMarket(marketId){
      activeMarket=marketId;
      const market=marketCache.find(m=>m.id===marketId);
      if(!market)return;
      q('market-board-title').textContent=`${market.name} • ${market.session}`;
      q('instrument-board').innerHTML=`<div class="table-head instrument-row"><div>Instrument</div><div>Last Price</div><div>Change</div></div>`+market.instrument_data.map(item=>`<div class="instrument-row table-row"><div>${item.symbol}</div><div>${fmt(Number(item.price||0))}</div><div class="${cls(item.change_pct)}">${pct(item.change_pct)}</div></div>`).join('');
      q('sim-market').value=marketId;
    }

    function renderWorkflow(items){q('workflow-grid').innerHTML=items.map(item=>`<div class="flow-step ${item.status}"><div class="step-name">${item.step}</div><div class="step-detail">${item.detail}</div></div>`).join('');}
    function renderMetricsBlock(scores){return Object.entries(scores).map(([label,value])=>`<div style="margin-bottom:12px"><div class="small" style="display:flex;justify-content:space-between"><span>${label}</span><span>${Number(value).toFixed(1)}</span></div><div class="score-bar" style="margin-top:6px"><div class="score-fill" style="width:${Number(value)}%"></div></div></div>`).join('');}

    function renderPriceChart(history, signal){
      const container=q('price-chart');
      container.innerHTML='';
      priceChart=LightweightCharts.createChart(container,{layout:{background:{color:'#07101b'},textColor:'#9cb1c7'},grid:{vertLines:{color:'#122034'},horzLines:{color:'#122034'}},rightPriceScale:{borderColor:'#20314b'},timeScale:{borderColor:'#20314b'},crosshair:{mode:1},width:container.clientWidth,height:360});
      candleSeries=priceChart.addCandlestickSeries({upColor:'#29c08f',downColor:'#ff6d6d',borderVisible:false,wickUpColor:'#29c08f',wickDownColor:'#ff6d6d'});
      lineSeries=priceChart.addLineSeries({color:'#54b3ff',lineWidth:2});
      candleSeries.setData(history.map(bar=>({time:Math.floor(new Date(bar.ts).getTime()/1000),open:Number(bar.open),high:Number(bar.high),low:Number(bar.low),close:Number(bar.close)})));
      lineSeries.setData(history.map(bar=>({time:Math.floor(new Date(bar.ts).getTime()/1000),value:Number(bar.close)})));
      candleSeries.createPriceLine({price:Number(signal.entry_price),color:'#54b3ff',lineWidth:2,lineStyle:2,title:'Entry'});
      candleSeries.createPriceLine({price:Number(signal.stop_loss),color:'#ff6d6d',lineWidth:2,lineStyle:2,title:'Stop'});
      candleSeries.createPriceLine({price:Number(signal.target_price),color:'#29c08f',lineWidth:2,lineStyle:2,title:'Target'});
      setTimeout(()=>priceChart.timeScale().fitContent(),30);
    }

    async function loadAnalysis(symbol){
      activeSymbol=symbol||q('analysis-symbol').value;
      q('analysis-symbol').value=activeSymbol;
      const report=await fetchJSON(`/api/analysis/${encodeURIComponent(activeSymbol)}`);
      renderWorkflow(report.workflow);
      q('technical-score').textContent=`Technical ${report.technicals.scores.technical}`;
      q('fundamental-score').textContent=`Fundamental ${report.fundamentals.scores.fundamental}`;
      q('sentiment-label').textContent=`${report.sentiment.label} • ${report.sentiment.aggregate}`;
      q('technical-panel').innerHTML=renderMetricsBlock(report.technicals.scores)+`<div class="small">EMA9 ${report.technicals.trend.ema9} • EMA21 ${report.technicals.trend.ema21} • ADX ${report.technicals.trend.adx}</div><div class="small" style="margin-top:10px">RSI ${report.technicals.momentum.rsi} • MACD hist ${report.technicals.momentum.macd_hist} • BB width ${report.technicals.volatility.bb_width}</div>`;
      q('fundamental-panel').innerHTML=renderMetricsBlock(report.fundamentals.scores)+`<div class="small">Sector ${report.fundamentals.details.sector} • Market ${report.market_name}</div><div class="small" style="margin-top:10px">ROE ${report.fundamentals.details.roe} • Margin ${report.fundamentals.details.profit_margin} • D/E ${report.fundamentals.details.debt_to_equity}</div>`;
      q('sentiment-panel').innerHTML=renderMetricsBlock(report.sentiment.sources)+`<div class="mini-list" style="margin-top:14px">${report.sentiment.headlines.map(item=>`<div class="news-item"><div class="meta">${item.source} • ${item.ts.slice(11,16)}</div><div>${item.headline}</div></div>`).join('')}</div>`;
      q('signal-headline').textContent=`${report.signal.direction} • confidence ${report.signal.confidence}`;
      q('signal-panel').innerHTML=`<div class="score"><strong>${report.signal.direction}</strong><span class="small">${report.signal.status}</span></div><div style="margin-top:12px">${renderMetricsBlock({confidence:report.signal.confidence})}</div><div class="small">Entry ${fmt(Number(report.signal.entry_price))}</div><div class="small">Stop ${fmt(Number(report.signal.stop_loss))}</div><div class="small">Target ${fmt(Number(report.signal.target_price))}</div><div class="small">R:R ${report.signal.risk_reward}</div><div class="small" style="margin-top:10px">${report.signal.rationale}</div><div style="margin-top:16px" class="badge">${report.signal.automation_ready?'Auto-entry ready':'Monitor only'}</div>`;
      renderPriceChart(report.price_history, report.signal);
    }

    async function loadSignals(){
      const items=await fetchJSON('/api/signals');
      q('signal-body').innerHTML=items.length?items.map(item=>`<tr><td>${item.ts.slice(0,16).replace('T',' ')}</td><td>${item.symbol}</td><td class="${item.direction==='LONG'?'positive':'negative'}">${item.direction}</td><td>${Number(item.confidence).toFixed(1)}</td><td>${fmt(Number(item.entry_price))}</td><td>${fmt(Number(item.stop_loss))}</td><td>${fmt(Number(item.target_price))}</td><td>${item.status}</td></tr>`).join(''):`<tr><td colspan="8" class="small">No signals yet.</td></tr>`;
    }

    async function loadReports(){
      const report=await fetchJSON('/api/reports');
      const summary=report.summary;
      q('report-cards').innerHTML=[['Total P&L',fmt(summary.total_pnl)],['Best Trade',fmt(summary.best_trade)],['Worst Trade',fmt(summary.worst_trade)],['Trade Count',summary.trade_count]].map(([label,value])=>`<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div></div>`).join('');
      q('report-strategy').innerHTML=`<table><thead><tr><th>Strategy</th><th>Trades</th><th>Win Rate</th><th>Avg P&amp;L</th><th>Total</th></tr></thead><tbody>${report.by_strategy.map(row=>`<tr><td>${row.strategy}</td><td>${row.trades}</td><td>${row.win_rate}%</td><td>${fmt(row.avg_pnl)}</td><td class="${cls(row.total_pnl)}">${fmt(row.total_pnl)}</td></tr>`).join('')}</tbody></table>`;
      q('report-market').innerHTML=`<table><thead><tr><th>Market</th><th>Total P&amp;L</th></tr></thead><tbody>${report.by_market.map(row=>`<tr><td>${row.market}</td><td class="${cls(row.total_pnl)}">${fmt(row.total_pnl)}</td></tr>`).join('')}</tbody></table>`;
      q('report-hour').innerHTML=`<table><thead><tr><th>Hour</th><th>Total P&amp;L</th></tr></thead><tbody>${report.by_hour.map(row=>`<tr><td>${row.hour}:00</td><td class="${cls(row.total_pnl)}">${fmt(row.total_pnl)}</td></tr>`).join('')}</tbody></table>`;
    }

    async function loadLogs(){const payload=await fetchJSON('/api/logs');q('log-box').innerHTML=payload.lines.map(line=>`<div class="${line.includes('ERROR')?'negative':line.includes('WARN')?'neutral':'positive'}">${line}</div>`).join('');}

    async function loadStatus(){
      const [status,metrics,equity,trades,sim]=await Promise.all([fetchJSON('/api/status'),fetchJSON('/api/metrics'),fetchJSON('/api/equity'),fetchJSON('/api/trades?limit=18'),fetchJSON('/api/sim/status')]);
      const engine=status.engine||{};
      const pill=q('engine-pill');
      pill.className='status-pill '+(engine.mode==='simulation'?'sim':engine.halted?'halt':engine.running?'live':'');
      pill.textContent=engine.mode==='simulation'?'Simulation':engine.halted?'Halted':engine.running?'Engine Live':'Engine Offline';
      q('sim-summary').textContent=sim.running?`${sim.market} • ${sim.trades} trades • ${fmt(sim.daily_pnl)} • threshold ${sim.threshold}`:'Simulator idle';
      renderKpis(metrics);renderRisk(status,metrics);renderEquity(equity);renderTrades(trades);
    }

    async function loadMarkets(){const markets=await fetchJSON('/api/markets');renderMarkets(markets);}
    async function refreshAll(){await Promise.all([loadStatus(),loadMarkets(),loadSignals()]);if(activeSymbol){await loadAnalysis(activeSymbol);}if((window.location.hash||'#dashboard').slice(1)==='reports'){await loadReports();}if((window.location.hash||'#dashboard').slice(1)==='logs'){await loadLogs();}}

    q('analysis-refresh').addEventListener('click',()=>loadAnalysis(q('analysis-symbol').value));
    q('analysis-symbol').addEventListener('change',event=>loadAnalysis(event.target.value));
    q('sim-start').addEventListener('click',async()=>{const payload={market:q('sim-market').value,speed:Number(q('sim-speed').value),auto_trade:true,threshold:68};await fetchJSON('/api/sim/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});toast('Simulation started');loadStatus();});
    q('sim-stop').addEventListener('click',async()=>{await fetchJSON('/api/sim/stop',{method:'POST'});toast('Simulation stopped');loadStatus();});
    q('sim-reset').addEventListener('click',async()=>{await fetchJSON('/api/sim/reset',{method:'POST'});toast('Simulation reset');refreshAll();});
    q('log-refresh').addEventListener('click',loadLogs);
    setInterval(()=>{q('clock').textContent=new Date().toLocaleString(L,{timeZone:TZ});},1000);
    refreshAll().then(()=>{const initial=(window.location.hash||'#dashboard').slice(1);setTab(initial||'dashboard');}).catch(err=>toast(err.message));
    setInterval(()=>loadStatus().catch(()=>{}),15000);
    setInterval(()=>{if(activeSymbol&&(window.location.hash||'#dashboard').slice(1)==='analysis')loadAnalysis(activeSymbol).catch(()=>{});},20000);
  </script>
</body>
</html>"""