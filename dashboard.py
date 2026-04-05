"""
AdaptiveBot PRO - All In One File
Bot starts automatically - no button needed
Works perfectly on Render free tier
"""

import os, sys, json, time, hmac, hashlib, threading, logging
import requests
from datetime import datetime
from collections import deque
from flask import Flask, jsonify, render_template_string

logging.basicConfig(level=logging.INFO,format="%(asctime)s [%(levelname)s] %(message)s",handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("AdaptiveBot")

DELTA_API_KEY    = os.getenv("DELTA_API_KEY", "")
DELTA_API_SECRET = os.getenv("DELTA_API_SECRET", "")
CLAUDE_API_KEY   = os.getenv("CLAUDE_API_KEY", "")
DELTA_BASE       = "https://cdn-ind.testnet.deltaex.org"
BINANCE_REST     = "https://api.binance.com"

PAIRS = {
    "BTCUSDT": {"product_id": 27},
    "ETHUSDT": {"product_id": 3},
    "SOLUSDT": {"product_id": 185},
}

STARTING_BALANCE = 830
MAX_RISK_PCT     = 0.04
MIN_CONFIDENCE   = 7
TRADE_COOLDOWN   = 300
SCAN_INTERVAL    = 60

class BotState:
    def __init__(self):
        self.last_price = {p: 0.0 for p in PAIRS}
        self.last_trade = {p: 0   for p in PAIRS}
        self.positions  = {}
        self.trades     = []
        self.balance    = STARTING_BALANCE
        self.running    = False
        self.last_ind   = {}
        self.last_pair  = ""
        self.prices_ext = {}
        self.lock       = threading.Lock()

state = BotState()

def fetch_candles(pair, interval="5m", limit=100):
    try:
        r = requests.get(f"{BINANCE_REST}/api/v3/klines",params={"symbol":pair,"interval":interval,"limit":limit},timeout=10)
        return [{"open":float(c[1]),"high":float(c[2]),"low":float(c[3]),"close":float(c[4]),"volume":float(c[5])} for c in r.json()]
    except Exception as e:
        log.error(f"Candle error {pair}: {e}")
        return []

def fetch_price(pair):
    try:
        r = requests.get(f"{BINANCE_REST}/api/v3/ticker/24hr",params={"symbol":pair},timeout=5)
        d = r.json()
        return {"price":float(d["lastPrice"]),"chg":round(float(d["priceChangePercent"]),2)}
    except:
        return {"price":0,"chg":0}

def ema_calc(values, period):
    if len(values) < period: return []
    k = 2/(period+1)
    r = [sum(values[:period])/period]
    for v in values[period:]: r.append(v*k+r[-1]*(1-k))
    return r

def calc_rsi(closes, period=14):
    if len(closes) < period+1: return 50
    gains,losses = [],[]
    for i in range(1,len(closes)):
        d = closes[i]-closes[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    ag = sum(gains[-period:])/period
    al = sum(losses[-period:])/period
    return round(100-(100/(1+ag/al)),2) if al>0 else 100

def calculate_indicators(candles):
    if len(candles)<30: return None
    closes  = [c["close"]  for c in candles]
    highs   = [c["high"]   for c in candles]
    lows    = [c["low"]    for c in candles]
    volumes = [c["volume"] for c in candles]
    ind = {}
    ind["price"]      = round(closes[-1],4)
    ind["prev_close"] = round(closes[-2],4)
    ind["change_pct"] = round((closes[-1]-closes[-2])/closes[-2]*100,3)
    ind["rsi"]        = calc_rsi(closes)
    e9  = ema_calc(closes,9);  ind["ema9"]  = round(e9[-1],4)  if e9  else 0
    e21 = ema_calc(closes,21); ind["ema21"] = round(e21[-1],4) if e21 else 0
    e50 = ema_calc(closes,50); ind["ema50"] = round(e50[-1],4) if e50 else 0
    ind["ema_bull"]        = ind["ema9"] > ind["ema21"]
    ind["ema_strong_bull"] = ind["ema9"] > ind["ema21"] > ind["ema50"]
    e12 = ema_calc(closes,12); e26 = ema_calc(closes,26)
    if e12 and e26:
        ml  = [e12[i+len(e12)-len(e26)]-e26[i] for i in range(len(e26))]
        sig = ema_calc(ml,9)
        ind["macd_hist"] = round(ml[-1]-sig[-1],6) if sig else 0
    else: ind["macd_hist"] = 0
    ind["macd_bull"] = ind["macd_hist"] > 0
    trs = [max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,len(candles))]
    ind["atr"]     = round(sum(trs[-14:])/14,4) if len(trs)>=14 else 0
    ind["atr_pct"] = round(ind["atr"]/ind["price"]*100,3) if ind["price"] else 0
    typical = [(highs[i]+lows[i]+closes[i])/3 for i in range(len(candles))]
    ind["vwap"]       = round(sum(t*v for t,v in zip(typical,volumes))/sum(volumes),4)
    ind["above_vwap"] = ind["price"] > ind["vwap"]
    avg_vol = sum(volumes[-20:])/20
    ind["vol_ratio"]   = round(volumes[-1]/avg_vol,2) if avg_vol else 1
    ind["high_volume"] = ind["vol_ratio"] > 1.5
    ind["support"]    = round(min(lows[-20:]),4)
    ind["resistance"] = round(max(highs[-20:]),4)
    ind["near_support"]    = abs(ind["price"]-ind["support"])/ind["price"] < 0.005
    ind["near_resistance"] = abs(ind["price"]-ind["resistance"])/ind["price"] < 0.005
    ind["fvg_bull"] = lows[-1]>highs[-3]  if len(candles)>=3 else False
    ind["fvg_bear"] = highs[-1]<lows[-3]  if len(candles)>=3 else False
    if len(candles)>=5:
        opens = [c["open"] for c in candles]
        ind["ob_bull"] = (closes[-1]-opens[-1])/opens[-1]*100>0.5 and closes[-2]<opens[-2]
        ind["ob_bear"] = (opens[-1]-closes[-1])/opens[-1]*100>0.5 and closes[-2]>opens[-2]
    else: ind["ob_bull"]=ind["ob_bear"]=False
    if len(closes)>=6:
        ind["higher_highs"] = max(highs[-3:])>max(highs[-6:-3])
        ind["higher_lows"]  = min(lows[-3:])>min(lows[-6:-3])
        ind["lower_lows"]   = min(lows[-3:])<min(lows[-6:-3])
        ind["lower_highs"]  = max(highs[-3:])<max(highs[-6:-3])
    else: ind["higher_highs"]=ind["higher_lows"]=ind["lower_lows"]=ind["lower_highs"]=False
    if ind["higher_highs"] and ind["higher_lows"]:   ind["trend"]="STRONG_BULL"
    elif ind["lower_lows"] and ind["lower_highs"]:   ind["trend"]="STRONG_BEAR"
    elif ind["ema_bull"]:                             ind["trend"]="BULL"
    else:                                             ind["trend"]="BEAR"
    return ind

def claude_decide(pair, ind, tf):
    if not CLAUDE_API_KEY: return rule_based(ind)
    prompt = f"""You are a pro crypto trading AI. Analyze REAL live data.
PAIR:{pair} TF:{tf}
Price:${ind['price']} Change:{ind['change_pct']}% Trend:{ind['trend']}
RSI:{ind['rsi']} EMABull:{ind['ema_bull']} StrongBull:{ind['ema_strong_bull']}
MACDHist:{ind['macd_hist']} Bull:{ind['macd_bull']}
ATR:{ind['atr_pct']}% VWAP:{ind['vwap']} AboveVWAP:{ind['above_vwap']}
Vol:{ind['vol_ratio']}x HighVol:{ind['high_volume']}
Support:{ind['support']} NearSupport:{ind['near_support']}
Resistance:{ind['resistance']} NearResistance:{ind['near_resistance']}
FVGBull:{ind['fvg_bull']} FVGBear:{ind['fvg_bear']}
OBBull:{ind['ob_bull']} OBBear:{ind['ob_bear']}
HH:{ind['higher_highs']} HL:{ind['higher_lows']} LL:{ind['lower_lows']} LH:{ind['lower_highs']}

Small account ₹830. Max leverage 5x. Loss must be smaller than profit. Only trade when 3+ signals agree.
Return ONLY JSON: {{"direction":"BUY|SELL|NO_TRADE","confidence":8,"stop_loss_pct":1.2,"take_profit_pct":3.5,"leverage":2,"trail_pct":0.5,"timeframe":"5m","entry_reason":"reason","risk_note":"note"}}"""
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"Content-Type":"application/json","x-api-key":CLAUDE_API_KEY,"anthropic-version":"2023-06-01"},
            json={"model":"claude-haiku-4-5-20251001","max_tokens":300,"messages":[{"role":"user","content":prompt}]},timeout=15)
        text = r.json()["content"][0]["text"]
        d = json.loads(text[text.find("{"):text.rfind("}")+1])
        log.info(f"🤖 Claude [{pair}]: {d['direction']} conf:{d['confidence']}/10 | {d['entry_reason']}")
        return d
    except Exception as e:
        log.warning(f"Claude failed: {e}")
        return rule_based(ind)

def rule_based(ind):
    bull=bear=0
    if ind["rsi"]<35:    bull+=2
    elif ind["rsi"]<45:  bull+=1
    if ind["rsi"]>65:    bear+=2
    elif ind["rsi"]>55:  bear+=1
    if ind["ema_bull"]:  bull+=1
    else:                bear+=1
    if ind["ema_strong_bull"]: bull+=1
    if ind["macd_bull"]: bull+=1
    else:                bear+=1
    if ind["above_vwap"]:bull+=1
    else:                bear+=1
    if ind["high_volume"]:
        if bull>bear: bull+=1
        else:         bear+=1
    if ind["fvg_bull"]:  bull+=1
    if ind["fvg_bear"]:  bear+=1
    if ind["ob_bull"]:   bull+=1
    if ind["ob_bear"]:   bear+=1
    if ind["near_support"]:    bull+=1
    if ind["near_resistance"]: bear+=1
    if ind["higher_highs"] and ind["higher_lows"]: bull+=1
    if ind["lower_lows"]   and ind["lower_highs"]: bear+=1
    atr=ind["atr_pct"]
    sl=max(0.4,min(2.5,atr*1.5)); tp=sl*3.0
    lev=1 if atr>2 else (3 if atr<0.5 else 2)
    trail=round(sl*0.4,2) if (bull>=5 or bear>=5) else 0
    direction="NO_TRADE"; confidence=5
    if bull>=4 and bull>bear:   direction="BUY";  confidence=min(5+bull,10)
    elif bear>=4 and bear>bull: direction="SELL"; confidence=min(5+bear,10)
    return {"direction":direction,"confidence":confidence,"stop_loss_pct":round(sl,2),"take_profit_pct":round(tp,2),"leverage":lev,"trail_pct":trail,"timeframe":"5m","entry_reason":f"Bull={bull} Bear={bear} RSI={ind['rsi']} ATR={atr}%","risk_note":"Rule-based"}

def place_order(pair_info, side, size, price, sl_pct, tp_pct, trail_pct, leverage):
    if not DELTA_API_KEY: return None
    sl=price*(1-sl_pct/100) if side=="buy" else price*(1+sl_pct/100)
    tp=price*(1+tp_pct/100) if side=="buy" else price*(1-tp_pct/100)
    body={"product_id":pair_info["product_id"],"size":size,"side":side,"order_type":"market_order","leverage":str(leverage),
          "bracket_stop_loss_price":str(round(sl,2)),"bracket_stop_loss_limit_price":str(round(sl*0.999,2)),
          "bracket_take_profit_price":str(round(tp,2)),"bracket_take_profit_limit_price":str(round(tp*1.001,2)),
          "bracket_trail_amount":str(round(price*trail_pct/100,2)) if trail_pct>0 else "0"}
    ts=str(int(time.time())); msg="POST"+ts+"/v2/orders"+json.dumps(body)
    sig=hmac.new(DELTA_API_SECRET.encode(),msg.encode(),hashlib.sha256).hexdigest()
    try:
        r=requests.post(DELTA_BASE+"/v2/orders",headers={"api-key":DELTA_API_KEY,"timestamp":ts,"signature":sig,"Content-Type":"application/json"},json=body,timeout=10)
        return r.json()
    except Exception as e:
        log.error(f"Delta error: {e}"); return None

def save_trade(pair, decision, price, size, order_id, sl_price, tp_price):
    trade={"id":order_id or f"paper_{int(time.time())}","time":datetime.now().isoformat(),"pair":pair,
           "direction":decision["direction"],"price":price,"size":size,"sl_price":round(sl_price,2),
           "tp_price":round(tp_price,2),"sl_pct":decision["stop_loss_pct"],"tp_pct":decision["take_profit_pct"],
           "leverage":decision["leverage"],"trail_pct":decision["trail_pct"],"confidence":decision["confidence"],
           "timeframe":decision.get("timeframe","5m"),"reason":decision["entry_reason"],
           "risk_note":decision.get("risk_note",""),"pnl":0,"status":"OPEN" if order_id else "PAPER"}
    with state.lock: state.trades.insert(0,trade)
    try:
        existing=json.load(open("trades.json")) if os.path.exists("trades.json") else []
        existing.insert(0,trade)
        json.dump(existing[:500],open("trades.json","w"),indent=2)
    except: pass

def monitor_trailing():
    while state.running:
        try:
            with state.lock: positions=dict(state.positions)
            for pair,pos in positions.items():
                price=state.last_price.get(pair,0); trail=pos.get("trail_pct",0)
                if price==0 or trail==0: continue
                dist=price*trail/100
                if pos["direction"]=="BUY":
                    new_sl=price-dist
                    if new_sl>pos.get("current_sl",0):
                        with state.lock:
                            if pair in state.positions: state.positions[pair]["current_sl"]=new_sl
                        log.info(f"🔺 Trail UP {pair}: SL→${new_sl:,.2f}")
                elif pos["direction"]=="SELL":
                    new_sl=price+dist
                    if new_sl<pos.get("current_sl",float("inf")):
                        with state.lock:
                            if pair in state.positions: state.positions[pair]["current_sl"]=new_sl
                        log.info(f"🔻 Trail DOWN {pair}: SL→${new_sl:,.2f}")
        except Exception as e: log.error(f"Trail error: {e}")
        time.sleep(3)

def bot_loop():
    log.info("="*50)
    log.info("AdaptiveBot PRO — Bot Starting Automatically")
    log.info(f"Claude AI: {'✅ Active' if CLAUDE_API_KEY else '🔧 Rule-based fallback'}")
    log.info(f"Delta API: {'✅ Connected' if DELTA_API_KEY else '📝 Paper only'}")
    log.info("="*50)
    threading.Thread(target=monitor_trailing,daemon=True).start()
    current_tf={p:"5m" for p in PAIRS}

    # Initial price fetch
    for pair in PAIRS:
        d=fetch_price(pair)
        if d["price"]>0: state.last_price[pair]=d["price"]

    while state.running:
        for pair,info in PAIRS.items():
            if not state.running: break
            try:
                now=time.time()
                if now-state.last_trade[pair]<TRADE_COOLDOWN: continue
                if len(state.positions)>=2: continue
                if pair in state.positions: continue

                tf=current_tf[pair]
                log.info(f"Scanning {pair} {tf}...")
                candles=fetch_candles(pair,interval=tf,limit=100)
                if len(candles)<30: continue

                state.last_price[pair]=candles[-1]["close"]
                ind=calculate_indicators(candles)
                if not ind: continue

                log.info(f"{pair}: ${ind['price']} RSI={ind['rsi']} MACD={ind['macd_hist']:.4f} ATR={ind['atr_pct']}% Trend={ind['trend']}")
                with state.lock: state.last_ind=ind; state.last_pair=pair

                decision=claude_decide(pair,ind,tf)
                new_tf=decision.get("timeframe",tf)
                if new_tf!=current_tf[pair]: log.info(f"{pair}: TF {current_tf[pair]}→{new_tf}"); current_tf[pair]=new_tf
                if decision["direction"]=="NO_TRADE": log.info(f"{pair}: NO_TRADE — {decision['entry_reason']}"); continue
                if decision["confidence"]<MIN_CONFIDENCE: log.info(f"{pair}: Low conf {decision['confidence']}/10"); continue

                price=ind["price"]
                sl_price=price*(1-decision["stop_loss_pct"]/100) if decision["direction"]=="BUY" else price*(1+decision["stop_loss_pct"]/100)
                tp_price=price*(1+decision["take_profit_pct"]/100) if decision["direction"]=="BUY" else price*(1-decision["take_profit_pct"]/100)
                size=max(1,int((state.balance*MAX_RISK_PCT)/(price*decision["stop_loss_pct"]/100)))

                log.info(f"📊 {decision['direction']} {pair} @ ${price:,} | SL:{decision['stop_loss_pct']}% TP:{decision['take_profit_pct']}% {decision['leverage']}x")

                order_id=None
                if DELTA_API_KEY:
                    side="buy" if decision["direction"]=="BUY" else "sell"
                    resp=place_order(info,side,size,price,decision["stop_loss_pct"],decision["take_profit_pct"],decision["trail_pct"],decision["leverage"])
                    if resp and resp.get("success"): order_id=resp["result"]["id"]; log.info(f"✅ Order placed ID={order_id}")
                    else: log.error(f"❌ Order failed: {resp}")

                with state.lock:
                    state.positions[pair]={"direction":decision["direction"],"entry_price":price,"sl_price":sl_price,"tp_price":tp_price,"current_sl":sl_price,"trail_pct":decision["trail_pct"],"leverage":decision["leverage"],"size":size,"order_id":order_id,"entry_time":datetime.now().isoformat()}
                    state.last_trade[pair]=now
                save_trade(pair,decision,price,size,order_id,sl_price,tp_price)

            except Exception as e: log.error(f"{pair} error: {e}")

        # Update prices
        for pair in PAIRS:
            try:
                d=fetch_price(pair)
                if d["price"]>0:
                    state.last_price[pair]=d["price"]
                    state.prices_ext[pair]=d
            except: pass

        log.info(f"Prices: {' | '.join(f'{p}=${state.last_price[p]:,.0f}' for p in PAIRS if state.last_price[p]>0)} | Sleeping {SCAN_INTERVAL}s")
        time.sleep(SCAN_INTERVAL)

# ─── FLASK APP ─────────────────────────────────────────────
app = Flask(__name__)

HTML = open(os.path.join(os.path.dirname(__file__),"dashboard_ui.html")).read() if os.path.exists("dashboard_ui.html") else """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AdaptiveBot PRO</title><style>body{background:#05080e;color:#cbd5e1;font-family:monospace;padding:20px;}.card{background:#0b1220;border:1px solid rgba(56,189,248,0.15);border-radius:10px;padding:16px;margin-bottom:12px;}.green{color:#4ade80;}.red{color:#f87171;}.sky{color:#38bdf8;}.muted{color:#475569;}.big{font-size:22px;font-weight:700;}.small{font-size:11px;}.row{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid rgba(56,189,248,0.06);}.chip{padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700;}.buy{background:rgba(74,222,128,0.12);color:#4ade80;}.sell{background:rgba(248,113,113,0.12);color:#f87171;}.paper{background:rgba(251,191,36,0.12);color:#fbbf24;}.open{background:rgba(56,189,248,0.12);color:#38bdf8;}</style></head><body>
<h2 class="sky" style="margin-bottom:16px;letter-spacing:2px;">ADAPTIVEBOT PRO</h2>
<div class="card"><div class="small muted">STATUS</div><div class="big green" id="status">● RUNNING</div><div class="small muted" id="ai_mode"></div></div>
<div class="card"><div class="small muted">BALANCE</div><div class="big sky" id="balance">₹830.00</div><div class="small" id="pnl" style="color:#4ade80">+₹0.00 (+0.00%)</div></div>
<div class="card"><div class="small muted" style="margin-bottom:8px;">LIVE PRICES</div><div id="prices">Loading...</div></div>
<div class="card"><div class="small muted" style="margin-bottom:8px;">INDICATORS — <span id="ind_pair" class="sky"></span></div>
  <div class="row"><span class="muted">RSI(14)</span><span id="rsi" class="big" style="font-size:16px">--</span><span id="rsi_s" class="small">--</span></div>
  <div class="row"><span class="muted">EMA Cross</span><span id="ema">--</span><span id="ema_s" class="small">--</span></div>
  <div class="row"><span class="muted">MACD Hist</span><span id="macd">--</span><span id="macd_s" class="small">--</span></div>
  <div class="row"><span class="muted">ATR Vol%</span><span id="atr">--</span><span id="atr_s" class="small">--</span></div>
  <div class="row"><span class="muted">VWAP</span><span id="vwap">--</span><span id="vwap_s" class="small">--</span></div>
  <div class="row" style="border:none"><span class="muted">Trend</span><span id="trend">--</span></div>
</div>
<div class="card"><div class="small muted" style="margin-bottom:8px;">TRADE LOG — <span id="trade_count" class="sky">0 trades</span></div><div id="trades"><div class="muted small">Waiting for signals...</div></div></div>
<div class="card"><div class="small muted" style="margin-bottom:8px;">BOT LOG</div><pre id="log_box" style="font-size:9px;color:#475569;white-space:pre-wrap;max-height:150px;overflow-y:auto;">—</pre></div>
<div class="small muted" id="ts" style="text-align:center;padding:10px;">—</div>
<script>
const SB=830;
async function refresh(){
  try{
    const[s,t,l,p]=await Promise.all([fetch('/api/status').then(r=>r.json()).catch(()=>({})),fetch('/api/trades').then(r=>r.json()).catch(()=>[]),fetch('/api/log').then(r=>r.json()).catch(()=>({lines:[]})),fetch('/api/prices').then(r=>r.json()).catch(()=>({}))]);
    document.getElementById('ts').textContent='Updated: '+new Date().toLocaleTimeString('en-IN');
    document.getElementById('ai_mode').textContent=(s.claude?'Claude AI':'Rule-based')+' | WS: REST polling';
    if(Object.keys(p).length)document.getElementById('prices').innerHTML=Object.entries(p).map(([pair,d])=>`<div class="row"><span>${pair.replace('USDT','/USDT')}</span><span style="font-weight:700">$${Number(d.price).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}</span><span class="${d.chg>=0?'green':'red'}">${d.chg>=0?'+':''}${d.chg}%</span></div>`).join('');
    const pnl=t.reduce((s,x)=>s+(x.pnl||0),0),bal=SB+pnl,pct=(pnl/SB*100).toFixed(2),wins=t.filter(x=>x.pnl>0).length;
    document.getElementById('balance').textContent='₹'+bal.toFixed(2);
    document.getElementById('balance').style.color=bal>=SB?'#38bdf8':'#f87171';
    document.getElementById('pnl').textContent=(pnl>=0?'+':'')+'₹'+pnl.toFixed(2)+' ('+(pct>=0?'+':'')+pct+'%)';
    document.getElementById('pnl').style.color=pnl>=0?'#4ade80':'#f87171';
    document.getElementById('trade_count').textContent=t.length+' trades | WR: '+(t.length>0?Math.round(wins/t.length*100)+'%':'—');
    if(s.last_ind){const i=s.last_ind;document.getElementById('ind_pair').textContent=s.last_pair||'';
      const rEl=document.getElementById('rsi');rEl.textContent=i.rsi;rEl.style.color=i.rsi<30?'#4ade80':i.rsi>70?'#f87171':'#fbbf24';
      document.getElementById('rsi_s').textContent=i.rsi<30?'OVERSOLD':i.rsi>70?'OVERBOUGHT':'NEUTRAL';
      document.getElementById('ema').textContent=i.ema_bull?'BULL ↑':'BEAR ↓';document.getElementById('ema').style.color=i.ema_bull?'#4ade80':'#f87171';
      document.getElementById('ema_s').textContent='EMA9 '+(i.ema_bull?'>':'<')+' EMA21';
      document.getElementById('macd').textContent=Number(i.macd_hist||0).toFixed(4);document.getElementById('macd').style.color=i.macd_bull?'#4ade80':'#f87171';
      document.getElementById('macd_s').textContent=i.macd_bull?'BULLISH':'BEARISH';
      document.getElementById('atr').textContent=i.atr_pct+'%';document.getElementById('atr_s').textContent=i.atr_pct>2?'HIGH VOL':i.atr_pct>0.5?'NORMAL':'LOW VOL';
      document.getElementById('vwap').textContent='$'+Number(i.vwap||0).toLocaleString(undefined,{maximumFractionDigits:2});
      document.getElementById('vwap_s').textContent=i.above_vwap?'↑ ABOVE':'↓ BELOW';document.getElementById('vwap_s').style.color=i.above_vwap?'#4ade80':'#f87171';
      document.getElementById('trend').textContent=i.trend||'--';document.getElementById('trend').style.color=i.trend?.includes('BULL')?'#4ade80':'#f87171';}
    if(t.length>0)document.getElementById('trades').innerHTML=t.slice(0,10).map(x=>`<div style="padding:8px 0;border-bottom:1px solid rgba(56,189,248,0.06)"><div class="row" style="border:none;padding:2px 0"><span class="chip ${x.direction==='BUY'?'buy':'sell'}">${x.direction}</span><span>${(x.pair||'').replace('USDT','/USDT')}</span><span>$${Number(x.price).toLocaleString()}</span><span class="chip ${x.status==='OPEN'?'open':'paper'}">${x.status}</span></div><div class="small muted">SL:${x.sl_pct}% TP:${x.tp_pct}% ${x.leverage}x ${x.timeframe} | ${new Date(x.time).toLocaleTimeString('en-IN')}</div><div class="small muted">🤖 ${x.reason}</div></div>`).join('');
    document.getElementById('log_box').textContent=(l.lines||[]).slice(-12).join('\n');
  }catch(e){console.error(e);}
}
refresh();setInterval(refresh,8000);
</script></body></html>"""

@app.route("/") 
def dashboard(): return HTML

@app.route("/api/status")
def api_status():
    return jsonify({"running":state.running,"claude":bool(CLAUDE_API_KEY),"positions":state.positions,"last_ind":state.last_ind,"last_pair":state.last_pair})

@app.route("/api/trades")
def api_trades():
    try: return jsonify(json.load(open("trades.json")) if os.path.exists("trades.json") else [])
    except: return jsonify(state.trades)

@app.route("/api/prices")
def api_prices():
    result={}
    for pair in PAIRS:
        p=state.last_price.get(pair,0)
        ext=state.prices_ext.get(pair,{})
        if p>0: result[pair]={"price":p,"chg":ext.get("chg",0),"tf":"5m"}
    return jsonify(result)

@app.route("/api/log")
def api_log():
    lines=[]
    if os.path.exists("bot.log"):
        with open("bot.log") as f: lines=f.readlines()[-20:]
    return jsonify({"lines":[l.strip() for l in lines]})

if __name__=="__main__":
    state.running=True
    threading.Thread(target=bot_loop,daemon=True).start()
    log.info("✅ Bot started automatically in background")
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port,debug=False)
