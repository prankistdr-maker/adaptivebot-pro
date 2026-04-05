"""
AdaptiveBot PRO Dashboard
Real-time monitoring from your phone
"""
import os, json, subprocess, threading
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
bot_proc = None

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>AdaptiveBot PRO</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Outfit:wght@400;600;700;800&display=swap');
:root{--bg:#05080e;--bg2:#0b1220;--bg3:#101c2e;--border:rgba(56,189,248,0.1);--sky:#38bdf8;--teal:#2dd4bf;--green:#4ade80;--red:#f87171;--amber:#fbbf24;--purple:#a78bfa;--muted:#475569;--text:#cbd5e1;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:'Outfit',sans-serif;min-height:100vh;}
body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(56,189,248,0.025) 1px,transparent 1px),linear-gradient(90deg,rgba(56,189,248,0.025) 1px,transparent 1px);background-size:32px 32px;pointer-events:none;}
.app{position:relative;z-index:1;padding:12px;max-width:480px;margin:0 auto;}
.hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid var(--border);}
.logo{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;color:var(--sky);letter-spacing:2px;}
.badge{display:flex;align-items:center;gap:5px;padding:3px 10px;border-radius:20px;font-size:10px;font-family:'JetBrains Mono',monospace;}
.badge.on{background:rgba(74,222,128,0.08);border:1px solid rgba(74,222,128,0.25);color:var(--green);}
.badge.off{background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.25);color:var(--red);}
.dot{width:6px;height:6px;border-radius:50%;}
.on .dot{background:var(--green);animation:p 1.2s infinite;}
.off .dot{background:var(--red);}
@keyframes p{0%,100%{opacity:1}50%{opacity:.2}}

.cards{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px;}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:12px;position:relative;overflow:hidden;}
.card::after{content:'';position:absolute;top:0;left:0;right:0;height:1.5px;}
.c-sky::after{background:var(--sky);}
.c-green::after{background:var(--green);}
.c-red::after{background:var(--red);}
.c-teal::after{background:var(--teal);}
.c-purple::after{background:var(--purple);}
.c-amber::after{background:var(--amber);}
.lbl{font-size:8px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;font-family:'JetBrains Mono',monospace;margin-bottom:3px;}
.val{font-size:18px;font-weight:800;font-family:'JetBrains Mono',monospace;line-height:1;}
.sub{font-size:9px;color:var(--muted);font-family:'JetBrains Mono',monospace;margin-top:2px;}

.sec{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:10px;}
.sec-t{font-size:8px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;font-family:'JetBrains Mono',monospace;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;}
.sec-t span{color:var(--sky);}

.price-row{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid rgba(56,189,248,0.05);}
.price-row:last-child{border:none;}
.pair-name{font-size:11px;font-family:'JetBrains Mono',monospace;color:var(--text);}
.price-val{font-size:12px;font-weight:700;font-family:'JetBrains Mono',monospace;}
.chg{font-size:9px;padding:1px 5px;border-radius:3px;}
.up{color:var(--green);background:rgba(74,222,128,0.1);}
.dn{color:var(--red);background:rgba(248,113,113,0.1);}

.trade{padding:8px 0;border-bottom:1px solid rgba(56,189,248,0.05);}
.trade:last-child{border:none;}
.tr1{display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;}
.tr2{display:flex;justify-content:space-between;font-size:9px;color:var(--muted);font-family:'JetBrains Mono',monospace;margin-bottom:2px;}
.tr3{font-size:9px;color:var(--muted);font-family:'JetBrains Mono',monospace;line-height:1.4;}
.chip{padding:2px 6px;border-radius:3px;font-size:9px;font-weight:700;font-family:'JetBrains Mono',monospace;}
.chip-buy{background:rgba(74,222,128,0.1);color:var(--green);}
.chip-sell{background:rgba(248,113,113,0.1);color:var(--red);}
.chip-open{background:rgba(56,189,248,0.1);color:var(--sky);}
.chip-paper{background:rgba(251,191,36,0.1);color:var(--amber);}
.t-pair{font-size:11px;font-family:'JetBrains Mono',monospace;}
.t-price{font-size:10px;font-family:'JetBrains Mono',monospace;color:var(--text);}

.ind-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;}
.ind{background:var(--bg3);border:1px solid var(--border);border-radius:7px;padding:8px 10px;}
.ind-n{font-size:8px;letter-spacing:1.5px;color:var(--muted);text-transform:uppercase;font-family:'JetBrains Mono',monospace;margin-bottom:3px;}
.ind-v{font-size:13px;font-weight:700;font-family:'JetBrains Mono',monospace;}
.ind-s{font-size:9px;font-family:'JetBrains Mono',monospace;margin-top:1px;}

.chart-box{height:140px;margin-bottom:6px;}

.btn-row{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px;}
.btn{padding:11px;border-radius:8px;border:none;font-family:'Outfit',sans-serif;font-size:12px;font-weight:700;cursor:pointer;letter-spacing:1px;}
.btn-start{background:linear-gradient(135deg,#4ade80,#16a34a);color:#fff;}
.btn-stop{background:linear-gradient(135deg,#f87171,#b91c1c);color:#fff;}
.btn-reset{background:var(--bg3);border:1px solid var(--border);color:var(--muted);}

.log-box{font-size:9px;font-family:'JetBrains Mono',monospace;color:var(--muted);line-height:1.9;white-space:pre-wrap;max-height:150px;overflow-y:auto;}
.log-box::-webkit-scrollbar{width:2px;}
.log-box::-webkit-scrollbar-thumb{background:var(--border);}

.pos-card{background:rgba(56,189,248,0.05);border:1px solid rgba(56,189,248,0.2);border-radius:8px;padding:10px;margin-bottom:6px;}
.pos-top{display:flex;justify-content:space-between;margin-bottom:4px;}
.pos-detail{font-size:9px;font-family:'JetBrains Mono',monospace;color:var(--muted);line-height:1.7;}
.pnl-live{font-size:12px;font-weight:700;font-family:'JetBrains Mono',monospace;}

.ts{font-size:9px;color:var(--muted);font-family:'JetBrains Mono',monospace;text-align:center;margin-top:6px;}
</style>
</head>
<body>
<div class="app">

<div class="hdr">
  <div class="logo">ADAPTIVEBOT PRO</div>
  <div class="badge off" id="statusBadge">
    <div class="dot"></div>
    <span id="statusTxt">STOPPED</span>
  </div>
</div>

<!-- STATS -->
<div class="cards">
  <div class="card c-sky"><div class="lbl">Balance</div><div class="val" id="sBalance" style="color:var(--sky)">₹830</div><div class="sub">paper money</div></div>
  <div class="card c-green"><div class="lbl">P&L</div><div class="val" id="sPnl" style="color:var(--green)">+₹0</div><div class="sub" id="sPct">+0.00%</div></div>
  <div class="card c-teal"><div class="lbl">Win Rate</div><div class="val" id="sWr" style="color:var(--teal)">—</div><div class="sub" id="sTrades">0 trades</div></div>
  <div class="card c-amber"><div class="lbl">AI Mode</div><div class="val" id="sAi" style="color:var(--amber);font-size:12px;">—</div><div class="sub" id="sWs">ws: —</div></div>
</div>

<!-- BUTTONS -->
<div class="btn-row">
  <button class="btn btn-start" onclick="startBot()">▶ START</button>
  <button class="btn btn-stop" onclick="stopBot()">⏹ STOP</button>
</div>

<!-- LIVE PRICES -->
<div class="sec">
  <div class="sec-t">LIVE PRICES <span id="priceTs">--</span></div>
  <div id="priceList">Loading...</div>
</div>

<!-- OPEN POSITIONS -->
<div class="sec" id="posSection" style="display:none">
  <div class="sec-t">OPEN POSITIONS</div>
  <div id="posList"></div>
</div>

<!-- P&L CHART -->
<div class="sec">
  <div class="sec-t">P&L CURVE</div>
  <div class="chart-box"><canvas id="pnlChart"></canvas></div>
</div>

<!-- INDICATORS (last analyzed pair) -->
<div class="sec">
  <div class="sec-t">LAST SIGNAL INDICATORS <span id="indPair">--</span></div>
  <div class="ind-grid">
    <div class="ind"><div class="ind-n">RSI(14)</div><div class="ind-v" id="iRsi">--</div><div class="ind-s" id="iRsiS">--</div></div>
    <div class="ind"><div class="ind-n">EMA Cross</div><div class="ind-v" id="iEma">--</div><div class="ind-s" id="iEmaS">--</div></div>
    <div class="ind"><div class="ind-n">MACD Hist</div><div class="ind-v" id="iMacd">--</div><div class="ind-s" id="iMacdS">--</div></div>
    <div class="ind"><div class="ind-n">ATR Vol%</div><div class="ind-v" id="iAtr">--</div><div class="ind-s" id="iAtrS">--</div></div>
    <div class="ind"><div class="ind-n">VWAP</div><div class="ind-v" id="iVwap" style="font-size:10px">--</div><div class="ind-s" id="iVwapS">--</div></div>
    <div class="ind"><div class="ind-n">Confluence</div><div class="ind-v" id="iConf">--</div><div class="ind-s" id="iConfS">--</div></div>
  </div>
</div>

<!-- TRADE LOG -->
<div class="sec">
  <div class="sec-t">TRADE LOG <span id="tradeCount">0</span></div>
  <div id="tradeList"><div style="color:var(--muted);font-size:10px;font-family:'JetBrains Mono',monospace;padding:10px 0;">No trades yet...</div></div>
</div>

<!-- BOT LOG -->
<div class="sec">
  <div class="sec-t">BOT LOG</div>
  <div class="log-box" id="logBox">—</div>
</div>

<div class="ts" id="lastTs">Last updated: —</div>

</div><!-- /app -->

<script>
const START_BAL = 830;
let pnlData = [START_BAL];
let pnlLabels = ['start'];
let wins = 0, losses = 0;

// Chart
const ctx = document.getElementById('pnlChart').getContext('2d');
const chart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: pnlLabels,
    datasets: [{
      data: pnlData,
      borderColor: '#38bdf8',
      backgroundColor: 'rgba(56,189,248,0.05)',
      borderWidth: 1.5,
      pointRadius: 0,
      fill: true,
      tension: 0.4
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    animation: { duration: 300 },
    plugins: { legend: { display: false } },
    scales: {
      x: { display: false },
      y: {
        grid: { color: 'rgba(56,189,248,0.04)' },
        ticks: { color: '#475569', font: { family: 'JetBrains Mono', size: 9 }, callback: v => '₹' + v.toFixed(0) }
      }
    }
  }
});

async function refresh() {
  try {
    const [status, trades, log, prices] = await Promise.all([
      fetch('/api/status').then(r=>r.json()).catch(()=>({})),
      fetch('/api/trades').then(r=>r.json()).catch(()=>[]),
      fetch('/api/log').then(r=>r.json()).catch(()=>({lines:[]})),
      fetch('/api/prices').then(r=>r.json()).catch(()=>({}))
    ]);

    const now = new Date().toLocaleTimeString('en-IN');
    document.getElementById('lastTs').textContent = 'Updated: ' + now;

    // Status badge
    const running = status.running;
    const badge = document.getElementById('statusBadge');
    badge.className = 'badge ' + (running ? 'on' : 'off');
    document.getElementById('statusTxt').textContent = running ? 'LIVE' : 'STOPPED';
    document.getElementById('sAi').textContent = status.claude ? 'CLAUDE AI' : 'RULE-BASED';
    document.getElementById('sWs').textContent = 'ws: ' + (status.ws ? '✅' : '❌');

    // Prices
    const priceEl = document.getElementById('priceList');
    if (Object.keys(prices).length) {
      priceEl.innerHTML = Object.entries(prices).map(([p,d])=>`
        <div class="price-row">
          <span class="pair-name">${p.replace('USDT','/USDT')}</span>
          <span class="price-val">$${Number(d.price).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}</span>
          <span class="chg ${d.chg>=0?'up':'dn'}">${d.chg>=0?'+':''}${d.chg}%</span>
          <span style="font-size:9px;color:var(--muted);font-family:JetBrains Mono,monospace">${d.tf}</span>
        </div>`).join('');
      document.getElementById('priceTs').textContent = now;
    }

    // Trades
    wins = trades.filter(t=>t.pnl>0).length;
    losses = trades.filter(t=>t.pnl<0).length;
    const total = trades.length;
    const wr = total>0 ? Math.round(wins/total*100)+'%' : '—';
    document.getElementById('sWr').textContent = wr;
    document.getElementById('sTrades').textContent = total + ' total';

    // P&L
    const totalPnl = trades.reduce((s,t)=>s+(t.pnl||0), 0);
    const bal = START_BAL + totalPnl;
    const pct = (totalPnl/START_BAL*100).toFixed(2);
    const balEl = document.getElementById('sBalance');
    balEl.textContent = '₹'+bal.toFixed(2);
    balEl.style.color = bal>=START_BAL?'var(--sky)':'var(--red)';
    const pnlEl = document.getElementById('sPnl');
    pnlEl.textContent = (totalPnl>=0?'+':'')+'₹'+totalPnl.toFixed(2);
    pnlEl.style.color = totalPnl>=0?'var(--green)':'var(--red)';
    document.getElementById('sPct').textContent = (pct>=0?'+':'')+pct+'%';

    // Chart
    if(trades.length > pnlData.length-1) {
      let running_bal = START_BAL;
      pnlData = [START_BAL];
      pnlLabels = ['start'];
      [...trades].reverse().forEach(t => {
        running_bal += (t.pnl||0);
        pnlData.push(parseFloat(running_bal.toFixed(2)));
        pnlLabels.push(new Date(t.time).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'}));
      });
      chart.data.labels = pnlLabels;
      chart.data.datasets[0].data = pnlData;
      chart.data.datasets[0].borderColor = bal>=START_BAL?'#4ade80':'#f87171';
      chart.data.datasets[0].backgroundColor = bal>=START_BAL?'rgba(74,222,128,0.05)':'rgba(248,113,113,0.05)';
      chart.update('none');
    }

    // Open positions
    if(status.positions && Object.keys(status.positions).length > 0) {
      document.getElementById('posSection').style.display='block';
      document.getElementById('posList').innerHTML = Object.entries(status.positions).map(([pair,pos])=>{
        const currentPrice = prices[pair]?.price || pos.entry_price;
        const rawPnl = pos.direction==='BUY'
          ? (currentPrice - pos.entry_price) / pos.entry_price * 100 * pos.leverage
          : (pos.entry_price - currentPrice) / pos.entry_price * 100 * pos.leverage;
        const pnlColor = rawPnl>=0?'var(--green)':'var(--red)';
        return `<div class="pos-card">
          <div class="pos-top">
            <span class="chip ${pos.direction==='BUY'?'chip-buy':'chip-sell'}">${pos.direction}</span>
            <span class="t-pair">${pair.replace('USDT','/USDT')}</span>
            <span class="pnl-live" style="color:${pnlColor}">${rawPnl>=0?'+':''}${rawPnl.toFixed(2)}%</span>
          </div>
          <div class="pos-detail">
            Entry: $${Number(pos.entry_price).toLocaleString()} | Current SL: $${Number(pos.current_sl).toFixed(2)}<br>
            TP: $${Number(pos.tp_price).toFixed(2)} | Lev: ${pos.leverage}x | Trail: ${pos.trail_pct}%
          </div>
        </div>`;
      }).join('');
    } else {
      document.getElementById('posSection').style.display='none';
    }

    // Indicators
    if(status.last_ind) {
      const ind = status.last_ind;
      document.getElementById('indPair').textContent = status.last_pair||'';
      const rsiEl = document.getElementById('iRsi');
      rsiEl.textContent = ind.rsi || '--';
      rsiEl.style.color = ind.rsi<30?'var(--green)':ind.rsi>70?'var(--red)':'var(--amber)';
      document.getElementById('iRsiS').textContent = ind.rsi<30?'OVERSOLD':ind.rsi>70?'OVERBOUGHT':'NEUTRAL';
      document.getElementById('iEma').textContent = ind.ema_bull?'BULL ↑':'BEAR ↓';
      document.getElementById('iEma').style.color = ind.ema_bull?'var(--green)':'var(--red)';
      document.getElementById('iEmaS').textContent = 'EMA9 '+(ind.ema_bull?'>':'<')+' EMA21';
      const macdEl = document.getElementById('iMacd');
      macdEl.textContent = Number(ind.macd_hist).toFixed(4);
      macdEl.style.color = ind.macd_bull?'var(--green)':'var(--red)';
      document.getElementById('iMacdS').textContent = ind.macd_bull?'BULLISH':'BEARISH';
      document.getElementById('iAtr').textContent = ind.atr_pct+'%';
      document.getElementById('iAtrS').textContent = ind.atr_pct>2?'HIGH VOL':ind.atr_pct>0.5?'NORMAL':'LOW VOL';
      document.getElementById('iVwap').textContent = '$'+Number(ind.vwap).toLocaleString();
      document.getElementById('iVwapS').textContent = ind.above_vwap?'ABOVE VWAP':'BELOW VWAP';
      document.getElementById('iVwapS').style.color = ind.above_vwap?'var(--green)':'var(--red)';
    }

    // Trade list
    if(trades.length > 0) {
      document.getElementById('tradeCount').textContent = trades.length;
      document.getElementById('tradeList').innerHTML = trades.slice(0,15).map(t=>`
        <div class="trade">
          <div class="tr1">
            <span class="chip ${t.direction==='BUY'?'chip-buy':'chip-sell'}">${t.direction}</span>
            <span class="t-pair">${t.pair?.replace('USDT','/USDT')}</span>
            <span class="t-price">$${Number(t.price).toLocaleString()}</span>
            <span class="chip ${t.status==='OPEN'?'chip-open':'chip-paper'}">${t.status}</span>
          </div>
          <div class="tr2">
            <span>SL:${t.sl_pct}% → TP:${t.tp_pct}% | ${t.leverage}x | ${t.timeframe}</span>
            <span>${new Date(t.time).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}</span>
          </div>
          <div class="tr3">🤖 ${t.reason}</div>
        </div>`).join('');
    }

    // Log
    document.getElementById('logBox').textContent = (log.lines||[]).slice(-15).join('\n');

  } catch(e) {
    console.error('Refresh error:', e);
  }
}

async function startBot() {
  await fetch('/api/start', {method:'POST'});
  refresh();
}
async function stopBot() {
  await fetch('/api/stop', {method:'POST'});
  refresh();
}

refresh();
setInterval(refresh, 5000); // refresh every 5 seconds
</script>
</body>
</html>"""

# ─── STATE BRIDGE ─────────────────────────────────────────
# Import bot state if running together
def get_bot_state():
    try:
        import bot
        return bot.state
    except:
        return None

@app.route("/")
def dashboard():
    return HTML

@app.route("/api/status")
def api_status():
    global bot_proc
    running = bot_proc is not None and bot_proc.poll() is None
    s = get_bot_state()
    return jsonify({
        "running": running,
        "ws": getattr(s, 'ws_connected', False) if s else False,
        "claude": bool(os.getenv("CLAUDE_API_KEY")),
        "positions": getattr(s, 'positions', {}) if s else {},
        "last_ind": getattr(s, 'last_ind', None) if s else None,
        "last_pair": getattr(s, 'last_pair', None) if s else None
    })

@app.route("/api/trades")
def api_trades():
    try:
        return jsonify(json.load(open("trades.json")) if os.path.exists("trades.json") else [])
    except:
        return jsonify([])

@app.route("/api/prices")
def api_prices():
    s = get_bot_state()
    if not s:
        return jsonify({})
    result = {}
    for pair in s.last_price:
        price = s.last_price[pair]
        if price > 0:
            result[pair] = {
                "price": price,
                "chg": 0,
                "tf": s.current_tf.get(pair, "5m")
            }
    return jsonify(result)

@app.route("/api/log")
def api_log():
    lines = []
    if os.path.exists("bot.log"):
        with open("bot.log") as f:
            lines = f.readlines()[-20:]
    return jsonify({"lines": [l.strip() for l in lines]})

@app.route("/api/start", methods=["POST"])
def api_start():
    global bot_proc
    if not bot_proc or bot_proc.poll() is not None:
        bot_proc = subprocess.Popen(["python", "bot.py"])
    return jsonify({"ok": True})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    global bot_proc
    if bot_proc:
        bot_proc.terminate()
        bot_proc = None
    return jsonify({"ok": True})

if __name__ == "__main__":
    import threading
    from bot import run as bot_run
    
    # Start bot in background thread automatically
    bot_thread = threading.Thread(target=bot_run, daemon=True)
    bot_thread.start()
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
