"""
AdaptiveBot PRO - Fixed with CoinGecko API
Oregon/US server compatible - no Binance blocking
"""
import os,sys,json,time,hmac,hashlib,threading,logging
import requests
from datetime import datetime
from flask import Flask,jsonify

logging.basicConfig(level=logging.INFO,format="%(asctime)s [%(levelname)s] %(message)s",handlers=[logging.StreamHandler(sys.stdout)])
log=logging.getLogger("AdaptiveBot")

DELTA_API_KEY=os.getenv("DELTA_API_KEY","")
DELTA_API_SECRET=os.getenv("DELTA_API_SECRET","")
CLAUDE_API_KEY=os.getenv("CLAUDE_API_KEY","")
DELTA_BASE="https://cdn-ind.testnet.deltaex.org"
COINGECKO_BASE="https://api.coingecko.com/api/v3"
COINGECKO_IDS={"BTCUSDT":"bitcoin","ETHUSDT":"ethereum","SOLUSDT":"solana"}
PAIRS={"BTCUSDT":{"product_id":27},"ETHUSDT":{"product_id":3},"SOLUSDT":{"product_id":185}}
STARTING_BALANCE=830;MAX_RISK_PCT=0.04;MIN_CONFIDENCE=7;TRADE_COOLDOWN=300;SCAN_INTERVAL=60

class BotState:
    def __init__(self):
        self.last_price={p:0.0 for p in PAIRS};self.last_trade={p:0 for p in PAIRS}
        self.positions={};self.trades=[];self.balance=STARTING_BALANCE
        self.running=False;self.last_ind={};self.last_pair=""
        self.prices_ext={};self.lock=threading.Lock()
state=BotState()

def fetch_candles(pair,interval="5m",limit=100):
    try:
        cid=COINGECKO_IDS.get(pair,"bitcoin")
        days="1" if interval in ["1m","3m","5m","15m"] else "7"
        r=requests.get(f"{COINGECKO_BASE}/coins/{cid}/ohlc",params={"vs_currency":"usd","days":days},timeout=15)
        r.raise_for_status();data=r.json()
        if not isinstance(data,list) or len(data)==0:return []
        candles=[{"open":float(c[1]),"high":float(c[2]),"low":float(c[3]),"close":float(c[4]),"volume":1000.0} for c in data[-limit:]]
        log.info(f"CoinGecko: {len(candles)} candles for {pair}");return candles
    except Exception as e:log.error(f"Candle error {pair}: {e}");return []

def fetch_all_prices():
    try:
        ids=",".join(COINGECKO_IDS.values())
        r=requests.get(f"{COINGECKO_BASE}/simple/price",params={"ids":ids,"vs_currencies":"usd","include_24hr_change":"true"},timeout=10)
        r.raise_for_status();data=r.json();result={}
        for pair,cid in COINGECKO_IDS.items():
            d=data.get(cid,{});price=float(d.get("usd",0));chg=round(float(d.get("usd_24h_change",0)),2)
            if price>0:result[pair]={"price":price,"chg":chg};state.last_price[pair]=price
        log.info(f"Prices: {[(p,result[p]['price']) for p in result]}");return result
    except Exception as e:log.error(f"Price error: {e}");return {}

def ema_calc(values,period):
    if len(values)<period:return []
    k=2/(period+1);r=[sum(values[:period])/period]
    for v in values[period:]:r.append(v*k+r[-1]*(1-k))
    return r

def calc_rsi(closes,period=14):
    if len(closes)<period+1:return 50
    gains,losses=[],[]
    for i in range(1,len(closes)):
        d=closes[i]-closes[i-1];gains.append(max(d,0));losses.append(max(-d,0))
    ag=sum(gains[-period:])/period;al=sum(losses[-period:])/period
    return round(100-(100/(1+ag/al)),2) if al>0 else 100

def calculate_indicators(candles):
    if len(candles)<30:return None
    closes=[c["close"] for c in candles];highs=[c["high"] for c in candles]
    lows=[c["low"] for c in candles];volumes=[c["volume"] for c in candles]
    ind={}
    ind["price"]=round(closes[-1],4);ind["prev_close"]=round(closes[-2],4)
    ind["change_pct"]=round((closes[-1]-closes[-2])/closes[-2]*100,3)
    ind["rsi"]=calc_rsi(closes)
    e9=ema_calc(closes,9);e21=ema_calc(closes,21);e50=ema_calc(closes,50)
    ind["ema9"]=round(e9[-1],4) if e9 else 0;ind["ema21"]=round(e21[-1],4) if e21 else 0;ind["ema50"]=round(e50[-1],4) if e50 else 0
    ind["ema_bull"]=ind["ema9"]>ind["ema21"];ind["ema_strong_bull"]=ind["ema9"]>ind["ema21"]>ind["ema50"]
    e12=ema_calc(closes,12);e26=ema_calc(closes,26)
    if e12 and e26:
        ml=[e12[i+len(e12)-len(e26)]-e26[i] for i in range(len(e26))];sig=ema_calc(ml,9)
        ind["macd_hist"]=round(ml[-1]-sig[-1],6) if sig else 0
    else:ind["macd_hist"]=0
    ind["macd_bull"]=ind["macd_hist"]>0
    trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,len(candles))]
    ind["atr"]=round(sum(trs[-14:])/14,4) if len(trs)>=14 else 0
    ind["atr_pct"]=round(ind["atr"]/ind["price"]*100,3) if ind["price"] else 0
    typical=[(highs[i]+lows[i]+closes[i])/3 for i in range(len(candles))]
    ind["vwap"]=round(sum(t*v for t,v in zip(typical,volumes))/sum(volumes),4)
    ind["above_vwap"]=ind["price"]>ind["vwap"]
    avg_vol=sum(volumes[-20:])/20;ind["vol_ratio"]=round(volumes[-1]/avg_vol,2) if avg_vol else 1
    ind["high_volume"]=ind["vol_ratio"]>1.5
    ind["support"]=round(min(lows[-20:]),4);ind["resistance"]=round(max(highs[-20:]),4)
    ind["near_support"]=abs(ind["price"]-ind["support"])/ind["price"]<0.005
    ind["near_resistance"]=abs(ind["price"]-ind["resistance"])/ind["price"]<0.005
    ind["fvg_bull"]=lows[-1]>highs[-3] if len(candles)>=3 else False
    ind["fvg_bear"]=highs[-1]<lows[-3] if len(candles)>=3 else False
    if len(candles)>=5:
        opens=[c["open"] for c in candles]
        ind["ob_bull"]=(closes[-1]-opens[-1])/opens[-1]*100>0.5 and closes[-2]<opens[-2]
        ind["ob_bear"]=(opens[-1]-closes[-1])/opens[-1]*100>0.5 and closes[-2]>opens[-2]
    else:ind["ob_bull"]=ind["ob_bear"]=False
    if len(closes)>=6:
        ind["higher_highs"]=max(highs[-3:])>max(highs[-6:-3]);ind["higher_lows"]=min(lows[-3:])>min(lows[-6:-3])
        ind["lower_lows"]=min(lows[-3:])<min(lows[-6:-3]);ind["lower_highs"]=max(highs[-3:])<max(highs[-6:-3])
    else:ind["higher_highs"]=ind["higher_lows"]=ind["lower_lows"]=ind["lower_highs"]=False
    if ind["higher_highs"] and ind["higher_lows"]:ind["trend"]="STRONG_BULL"
    elif ind["lower_lows"] and ind["lower_highs"]:ind["trend"]="STRONG_BEAR"
    elif ind["ema_bull"]:ind["trend"]="BULL"
    else:ind["trend"]="BEAR"
    return ind

def claude_decide(pair,ind,tf):
    if not CLAUDE_API_KEY:return rule_based(ind)
    prompt=f"""Pro crypto trading AI. Analyze REAL live data.
PAIR:{pair} TF:{tf} Price:${ind['price']} Change:{ind['change_pct']}% Trend:{ind['trend']}
RSI:{ind['rsi']} EMABull:{ind['ema_bull']} StrongBull:{ind['ema_strong_bull']}
MACDHist:{ind['macd_hist']} MACDBull:{ind['macd_bull']}
ATR:{ind['atr_pct']}% VWAP:{ind['vwap']} AboveVWAP:{ind['above_vwap']}
Vol:{ind['vol_ratio']}x HighVol:{ind['high_volume']}
Support:{ind['support']} NearSupport:{ind['near_support']}
Resistance:{ind['resistance']} NearResistance:{ind['near_resistance']}
FVGBull:{ind['fvg_bull']} FVGBear:{ind['fvg_bear']}
OBBull:{ind['ob_bull']} OBBear:{ind['ob_bear']}
HH:{ind['higher_highs']} HL:{ind['higher_lows']} LL:{ind['lower_lows']} LH:{ind['lower_highs']}
Small account Rs830. Max leverage 5x. Loss always smaller than profit. Only trade 3+ signals agree.
Return ONLY JSON: {{"direction":"BUY|SELL|NO_TRADE","confidence":8,"stop_loss_pct":1.2,"take_profit_pct":3.5,"leverage":2,"trail_pct":0.5,"timeframe":"5m","entry_reason":"reason","risk_note":"note"}}"""
    try:
        r=requests.post("https://api.anthropic.com/v1/messages",
            headers={"Content-Type":"application/json","x-api-key":CLAUDE_API_KEY,"anthropic-version":"2023-06-01"},
            json={"model":"claude-haiku-4-5-20251001","max_tokens":300,"messages":[{"role":"user","content":prompt}]},timeout=15)
        text=r.json()["content"][0]["text"]
        d=json.loads(text[text.find("{"):text.rfind("}")+1])
        log.info(f"Claude [{pair}]: {d['direction']} conf:{d['confidence']}/10");return d
    except Exception as e:log.warning(f"Claude failed: {e}");return rule_based(ind)

def rule_based(ind):
    bull=bear=0
    if ind["rsi"]<35:bull+=2
    elif ind["rsi"]<45:bull+=1
    if ind["rsi"]>65:bear+=2
    elif ind["rsi"]>55:bear+=1
    if ind["ema_bull"]:bull+=1
    else:bear+=1
    if ind["ema_strong_bull"]:bull+=1
    if ind["macd_bull"]:bull+=1
    else:bear+=1
    if ind["above_vwap"]:bull+=1
    else:bear+=1
    if ind["high_volume"]:
        if bull>bear:bull+=1
        else:bear+=1
    if ind["fvg_bull"]:bull+=1
    if ind["fvg_bear"]:bear+=1
    if ind["ob_bull"]:bull+=1
    if ind["ob_bear"]:bear+=1
    if ind["near_support"]:bull+=1
    if ind["near_resistance"]:bear+=1
    if ind["higher_highs"] and ind["higher_lows"]:bull+=1
    if ind["lower_lows"] and ind["lower_highs"]:bear+=1
    atr=ind["atr_pct"];sl=max(0.4,min(2.5,atr*1.5));tp=sl*3.0
    lev=1 if atr>2 else(3 if atr<0.5 else 2)
    trail=round(sl*0.4,2) if(bull>=5 or bear>=5) else 0
    direction="NO_TRADE";confidence=5
    if bull>=4 and bull>bear:direction="BUY";confidence=min(5+bull,10)
    elif bear>=4 and bear>bull:direction="SELL";confidence=min(5+bear,10)
    return{"direction":direction,"confidence":confidence,"stop_loss_pct":round(sl,2),"take_profit_pct":round(tp,2),
           "leverage":lev,"trail_pct":trail,"timeframe":"5m","entry_reason":f"Bull={bull} Bear={bear} RSI={ind['rsi']} ATR={atr}%","risk_note":"Rule-based"}

def place_order(pair_info,side,size,price,sl_pct,tp_pct,trail_pct,leverage):
    if not DELTA_API_KEY:return None
    sl=price*(1-sl_pct/100) if side=="buy" else price*(1+sl_pct/100)
    tp=price*(1+tp_pct/100) if side=="buy" else price*(1-tp_pct/100)
    body={"product_id":pair_info["product_id"],"size":size,"side":side,"order_type":"market_order","leverage":str(leverage),
          "bracket_stop_loss_price":str(round(sl,2)),"bracket_stop_loss_limit_price":str(round(sl*0.999,2)),
          "bracket_take_profit_price":str(round(tp,2)),"bracket_take_profit_limit_price":str(round(tp*1.001,2)),
          "bracket_trail_amount":str(round(price*trail_pct/100,2)) if trail_pct>0 else "0"}
    ts=str(int(time.time()));msg="POST"+ts+"/v2/orders"+json.dumps(body)
    sig=hmac.new(DELTA_API_SECRET.encode(),msg.encode(),hashlib.sha256).hexdigest()
    try:
        r=requests.post(DELTA_BASE+"/v2/orders",headers={"api-key":DELTA_API_KEY,"timestamp":ts,"signature":sig,"Content-Type":"application/json"},json=body,timeout=10)
        return r.json()
    except Exception as e:log.error(f"Delta error: {e}");return None

def save_trade(pair,decision,price,size,order_id,sl_price,tp_price):
    trade={"id":order_id or f"paper_{int(time.time())}","time":datetime.now().isoformat(),"pair":pair,
           "direction":decision["direction"],"price":price,"size":size,"sl_price":round(sl_price,2),
           "tp_price":round(tp_price,2),"sl_pct":decision["stop_loss_pct"],"tp_pct":decision["take_profit_pct"],
           "leverage":decision["leverage"],"trail_pct":decision["trail_pct"],"confidence":decision["confidence"],
           "timeframe":decision.get("timeframe","5m"),"reason":decision["entry_reason"],
           "risk_note":decision.get("risk_note",""),"pnl":0,"status":"OPEN" if order_id else "PAPER"}
    with state.lock:state.trades.insert(0,trade)
    try:
        existing=json.load(open("trades.json")) if os.path.exists("trades.json") else []
        existing.insert(0,trade);json.dump(existing[:500],open("trades.json","w"),indent=2)
    except:pass

def monitor_trailing():
    while state.running:
        try:
            with state.lock:positions=dict(state.positions)
            for pair,pos in positions.items():
                price=state.last_price.get(pair,0);trail=pos.get("trail_pct",0)
                if price==0 or trail==0:continue
                dist=price*trail/100
                if pos["direction"]=="BUY":
                    new_sl=price-dist
                    if new_sl>pos.get("current_sl",0):
                        with state.lock:
                            if pair in state.positions:state.positions[pair]["current_sl"]=new_sl
                        log.info(f"Trail UP {pair}: SL->$ {new_sl:,.2f}")
                elif pos["direction"]=="SELL":
                    new_sl=price+dist
                    if new_sl<pos.get("current_sl",float("inf")):
                        with state.lock:
                            if pair in state.positions:state.positions[pair]["current_sl"]=new_sl
                        log.info(f"Trail DOWN {pair}: SL->${new_sl:,.2f}")
        except Exception as e:log.error(f"Trail error: {e}")
        time.sleep(5)

def bot_loop():
    log.info("="*50)
    log.info("AdaptiveBot PRO - CoinGecko API (Oregon compatible)")
    log.info(f"Claude: {'Active' if CLAUDE_API_KEY else 'Rule-based'} | Delta: {'Connected' if DELTA_API_KEY else 'Paper only'}")
    log.info("="*50)
    threading.Thread(target=monitor_trailing,daemon=True).start()
    current_tf={p:"5m" for p in PAIRS}
    log.info("Fetching initial prices...")
    prices=fetch_all_prices()
    if prices:
        log.info(f"Prices loaded OK: {list(prices.keys())}")
        with state.lock:state.prices_ext=prices
    else:
        log.warning("Initial price fetch failed - will retry each cycle")
    scan_count=0
    while state.running:
        scan_count+=1
        log.info(f"\n--- Scan #{scan_count} at {datetime.now().strftime('%H:%M:%S')} ---")
        prices=fetch_all_prices()
        if prices:
            with state.lock:state.prices_ext=prices
        for pair,info in PAIRS.items():
            if not state.running:break
            try:
                now=time.time()
                if now-state.last_trade[pair]<TRADE_COOLDOWN:
                    log.info(f"{pair}: Cooldown {int(TRADE_COOLDOWN-(now-state.last_trade[pair]))}s");continue
                if len(state.positions)>=2:continue
                if pair in state.positions:continue
                tf=current_tf[pair]
                log.info(f"Analyzing {pair} {tf}...")
                candles=fetch_candles(pair,interval=tf,limit=100)
                if len(candles)<30:log.warning(f"{pair}: Only {len(candles)} candles");continue
                state.last_price[pair]=candles[-1]["close"]
                ind=calculate_indicators(candles)
                if not ind:continue
                log.info(f"{pair}: ${ind['price']} RSI={ind['rsi']} MACD={ind['macd_hist']:.4f} ATR={ind['atr_pct']}% Trend={ind['trend']}")
                with state.lock:state.last_ind=ind;state.last_pair=pair
                decision=claude_decide(pair,ind,tf)
                new_tf=decision.get("timeframe",tf)
                if new_tf!=current_tf[pair]:log.info(f"{pair}: TF {current_tf[pair]}->{new_tf}");current_tf[pair]=new_tf
                if decision["direction"]=="NO_TRADE":log.info(f"{pair}: NO_TRADE - {decision['entry_reason']}");continue
                if decision["confidence"]<MIN_CONFIDENCE:log.info(f"{pair}: Conf {decision['confidence']}/10 too low");continue
                price=ind["price"]
                sl_price=price*(1-decision["stop_loss_pct"]/100) if decision["direction"]=="BUY" else price*(1+decision["stop_loss_pct"]/100)
                tp_price=price*(1+decision["take_profit_pct"]/100) if decision["direction"]=="BUY" else price*(1-decision["take_profit_pct"]/100)
                size=max(1,int((state.balance*MAX_RISK_PCT)/(price*decision["stop_loss_pct"]/100)))
                log.info(f"SIGNAL: {decision['direction']} {pair} @ ${price:,} SL:{decision['stop_loss_pct']}% TP:{decision['take_profit_pct']}% {decision['leverage']}x")
                order_id=None
                if DELTA_API_KEY:
                    side="buy" if decision["direction"]=="BUY" else "sell"
                    resp=place_order(info,side,size,price,decision["stop_loss_pct"],decision["take_profit_pct"],decision["trail_pct"],decision["leverage"])
                    if resp and resp.get("success"):order_id=resp["result"]["id"];log.info(f"Order placed! ID={order_id}")
                    else:log.error(f"Order failed: {resp}")
                with state.lock:
                    state.positions[pair]={"direction":decision["direction"],"entry_price":price,"sl_price":sl_price,"tp_price":tp_price,
                                           "current_sl":sl_price,"trail_pct":decision["trail_pct"],"leverage":decision["leverage"],
                                           "size":size,"order_id":order_id,"entry_time":datetime.now().isoformat()}
                    state.last_trade[pair]=now
                save_trade(pair,decision,price,size,order_id,sl_price,tp_price)
            except Exception as e:log.error(f"{pair} error: {e}")
        log.info(f"Sleeping {SCAN_INTERVAL}s...")
        time.sleep(SCAN_INTERVAL)

app=Flask(__name__)

@app.route("/")
def dashboard():
    return open("static_dashboard.html").read() if os.path.exists("static_dashboard.html") else DASHBOARD_HTML

DASHBOARD_HTML="""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AdaptiveBot PRO</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Outfit:wght@400;700;800&display=swap');
:root{--bg:#05080e;--bg2:#0b1220;--bg3:#101c2e;--border:rgba(56,189,248,0.1);--sky:#38bdf8;--green:#4ade80;--red:#f87171;--amber:#fbbf24;--teal:#2dd4bf;--muted:#475569;--text:#cbd5e1;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:'Outfit',sans-serif;padding:14px;max-width:520px;margin:0 auto;}
body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(56,189,248,0.02) 1px,transparent 1px),linear-gradient(90deg,rgba(56,189,248,0.02) 1px,transparent 1px);background-size:32px 32px;pointer-events:none;}
.hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid var(--border);}
.logo{font-family:'JetBrains Mono',monospace;font-size:15px;font-weight:700;color:var(--sky);letter-spacing:2px;}
.sub-logo{font-size:9px;color:var(--teal);font-family:'JetBrains Mono',monospace;margin-top:2px;}
.badge{display:flex;align-items:center;gap:6px;padding:5px 12px;border-radius:20px;font-size:10px;font-family:'JetBrains Mono',monospace;letter-spacing:1px;}
.badge.on{background:rgba(74,222,128,0.08);border:1px solid rgba(74,222,128,0.3);color:var(--green);}
.badge.off{background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.3);color:var(--red);}
.dot{width:7px;height:7px;border-radius:50%;}
.on .dot{background:var(--green);animation:p 1.2s infinite;}
.off .dot{background:var(--red);}
@keyframes p{0%,100%{opacity:1;}50%{opacity:.2;}}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:13px;position:relative;overflow:hidden;}
.card::after{content:'';position:absolute;top:0;left:0;right:0;height:2px;}
.c-sky::after{background:var(--sky);}.c-green::after{background:var(--green);}
.c-teal::after{background:var(--teal);}.c-amber::after{background:var(--amber);}
.lbl{font-size:8px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;font-family:'JetBrains Mono',monospace;margin-bottom:4px;}
.val{font-size:20px;font-weight:800;font-family:'JetBrains Mono',monospace;line-height:1.1;}
.sub{font-size:9px;color:var(--muted);font-family:'JetBrains Mono',monospace;margin-top:3px;}
.sec{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:13px;margin-bottom:10px;}
.sec-t{font-size:8px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;font-family:'JetBrains Mono',monospace;margin-bottom:10px;display:flex;justify-content:space-between;}
.sec-t span{color:var(--sky);}
.prow{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid rgba(56,189,248,0.05);}
.prow:last-child{border:none;}
.pname{font-size:13px;font-family:'JetBrains Mono',monospace;font-weight:700;}
.pval{font-size:14px;font-weight:700;font-family:'JetBrains Mono',monospace;}
.chg{font-size:9px;padding:2px 6px;border-radius:3px;font-family:'JetBrains Mono',monospace;}
.up{color:var(--green);}.dn{color:var(--red);}
.chg.up{background:rgba(74,222,128,0.1);}.chg.dn{background:rgba(248,113,113,0.1);}
.chart-box{height:130px;}
.ind-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;}
.ind{background:var(--bg3);border:1px solid var(--border);border-radius:7px;padding:9px;}
.ind-n{font-size:8px;letter-spacing:1.5px;color:var(--muted);text-transform:uppercase;font-family:'JetBrains Mono',monospace;margin-bottom:3px;}
.ind-v{font-size:14px;font-weight:700;font-family:'JetBrains Mono',monospace;}
.ind-s{font-size:9px;font-family:'JetBrains Mono',monospace;margin-top:2px;color:var(--muted);}
.trade{padding:9px 0;border-bottom:1px solid rgba(56,189,248,0.05);}
.trade:last-child{border:none;}
.tr1{display:flex;justify-content:space-between;align-items:center;gap:4px;margin-bottom:3px;}
.tr2{font-size:9px;color:var(--muted);font-family:'JetBrains Mono',monospace;margin-bottom:2px;}
.tr3{font-size:9px;color:var(--muted);font-family:'JetBrains Mono',monospace;}
.chip{padding:2px 7px;border-radius:3px;font-size:9px;font-weight:700;font-family:'JetBrains Mono',monospace;}
.buy{background:rgba(74,222,128,0.12);color:var(--green);}
.sell{background:rgba(248,113,113,0.12);color:var(--red);}
.open{background:rgba(56,189,248,0.12);color:var(--sky);}
.paper{background:rgba(251,191,36,0.12);color:var(--amber);}
.log-box{font-size:9px;font-family:'JetBrains Mono',monospace;color:var(--muted);line-height:1.9;white-space:pre-wrap;max-height:160px;overflow-y:auto;}
.ts{font-size:9px;color:var(--muted);font-family:'JetBrains Mono',monospace;text-align:center;padding:8px 0 20px;}
</style></head><body>
<div class="hdr">
  <div><div class="logo">ADAPTIVEBOT PRO</div><div class="sub-logo">● CoinGecko Live Data</div></div>
  <div class="badge on" id="sb"><div class="dot"></div><span id="st">LIVE</span></div>
</div>
<div class="cards">
  <div class="card c-sky"><div class="lbl">Balance</div><div class="val" id="bal" style="color:var(--sky)">₹830</div><div class="sub">paper money</div></div>
  <div class="card c-green"><div class="lbl">P&L</div><div class="val" id="pnl" style="color:var(--green)">+₹0</div><div class="sub" id="pct">+0.00%</div></div>
  <div class="card c-teal"><div class="lbl">Win Rate</div><div class="val" id="wr" style="color:var(--teal)">—</div><div class="sub" id="tc">0 trades</div></div>
  <div class="card c-amber"><div class="lbl">AI Mode</div><div class="val" id="ai" style="color:var(--amber);font-size:11px;">—</div><div class="sub" id="ss">starting...</div></div>
</div>
<div class="sec"><div class="sec-t">LIVE PRICES <span id="pts">—</span></div><div id="pl"><div style="color:var(--muted);font-size:11px;font-family:JetBrains Mono,monospace">Fetching from CoinGecko...</div></div></div>
<div class="sec"><div class="sec-t">P&L CURVE</div><div class="chart-box"><canvas id="ch"></canvas></div></div>
<div class="sec"><div class="sec-t">INDICATORS <span id="ip">—</span></div>
  <div class="ind-grid">
    <div class="ind"><div class="ind-n">RSI(14)</div><div class="ind-v" id="ir">--</div><div class="ind-s" id="irs">--</div></div>
    <div class="ind"><div class="ind-n">EMA Cross</div><div class="ind-v" id="ie">--</div><div class="ind-s" id="ies">--</div></div>
    <div class="ind"><div class="ind-n">MACD Hist</div><div class="ind-v" id="im">--</div><div class="ind-s" id="ims">--</div></div>
    <div class="ind"><div class="ind-n">ATR Vol%</div><div class="ind-v" id="ia">--</div><div class="ind-s" id="ias">--</div></div>
    <div class="ind"><div class="ind-n">VWAP</div><div class="ind-v" id="iv" style="font-size:10px">--</div><div class="ind-s" id="ivs">--</div></div>
    <div class="ind"><div class="ind-n">Trend</div><div class="ind-v" id="itr">--</div><div class="ind-s" id="itrs">--</div></div>
  </div>
</div>
<div class="sec"><div class="sec-t">TRADE LOG <span id="tlc">0</span></div><div id="tl"><div style="color:var(--muted);font-size:10px;font-family:JetBrains Mono,monospace;padding:10px 0">Waiting for signals...</div></div></div>
<div class="sec"><div class="sec-t">BOT LOG</div><div class="log-box" id="lb">Starting up...</div></div>
<div class="ts" id="ts">—</div>
<script>
const SB=830;let pd=[SB],pl2=['start'];
const ctx=document.getElementById('ch').getContext('2d');
const chart=new Chart(ctx,{type:'line',data:{labels:pl2,datasets:[{data:pd,borderColor:'#38bdf8',backgroundColor:'rgba(56,189,248,0.05)',borderWidth:1.5,pointRadius:0,fill:true,tension:0.4}]},
  options:{responsive:true,maintainAspectRatio:false,animation:{duration:300},plugins:{legend:{display:false}},
    scales:{x:{display:false},y:{grid:{color:'rgba(56,189,248,0.04)'},ticks:{color:'#475569',font:{family:'JetBrains Mono',size:9},callback:v=>'₹'+v.toFixed(0)}}}}});
async function refresh(){
  try{
    const[s,t,l,p]=await Promise.all([fetch('/api/status').then(r=>r.json()).catch(()=>({})),fetch('/api/trades').then(r=>r.json()).catch(()=>[]),fetch('/api/log').then(r=>r.json()).catch(()=>({lines:[]})),fetch('/api/prices').then(r=>r.json()).catch(()=>({}))]);
    document.getElementById('ts').textContent='Updated: '+new Date().toLocaleTimeString('en-IN');
    document.getElementById('ai').textContent=s.claude?'CLAUDE AI':'RULE-BASED';
    document.getElementById('ss').textContent=s.running?'scanning markets':'stopped';
    if(Object.keys(p).length>0){
      document.getElementById('pl').innerHTML=Object.entries(p).map(([pair,d])=>`<div class="prow"><span class="pname">${pair.replace('USDT','/USDT')}</span><span class="pval">$${Number(d.price).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}</span><span class="chg ${d.chg>=0?'up':'dn'}">${d.chg>=0?'+':''}${d.chg}%</span></div>`).join('');
      document.getElementById('pts').textContent=new Date().toLocaleTimeString('en-IN');
    }
    const tpnl=t.reduce((s,x)=>s+(x.pnl||0),0),b=SB+tpnl,pc=(tpnl/SB*100).toFixed(2),w=t.filter(x=>x.pnl>0).length;
    document.getElementById('bal').textContent='₹'+b.toFixed(2);document.getElementById('bal').style.color=b>=SB?'var(--sky)':'var(--red)';
    document.getElementById('pnl').textContent=(tpnl>=0?'+':'')+'₹'+tpnl.toFixed(2);document.getElementById('pnl').style.color=tpnl>=0?'var(--green)':'var(--red)';
    document.getElementById('pct').textContent=(pc>=0?'+':'')+pc+'%';
    document.getElementById('wr').textContent=t.length>0?Math.round(w/t.length*100)+'%':'—';
    document.getElementById('tc').textContent=t.length+' total';document.getElementById('tlc').textContent=t.length+' trades';
    if(t.length>pd.length-1){let rb=SB;pd=[SB];pl2=['start'];[...t].reverse().forEach(x=>{rb+=(x.pnl||0);pd.push(parseFloat(rb.toFixed(2)));pl2.push(new Date(x.time).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'}));});chart.data.labels=pl2;chart.data.datasets[0].data=pd;chart.data.datasets[0].borderColor=b>=SB?'#4ade80':'#f87171';chart.data.datasets[0].backgroundColor=b>=SB?'rgba(74,222,128,0.05)':'rgba(248,113,113,0.05)';chart.update('none');}
    if(s.last_ind){const i=s.last_ind;document.getElementById('ip').textContent=s.last_pair||'';
      const re=document.getElementById('ir');re.textContent=i.rsi||'--';re.style.color=i.rsi<30?'var(--green)':i.rsi>70?'var(--red)':'var(--amber)';
      document.getElementById('irs').textContent=i.rsi<30?'OVERSOLD':i.rsi>70?'OVERBOUGHT':'NEUTRAL';
      document.getElementById('ie').textContent=i.ema_bull?'BULL ↑':'BEAR ↓';document.getElementById('ie').style.color=i.ema_bull?'var(--green)':'var(--red)';
      document.getElementById('ies').textContent='EMA9 '+(i.ema_bull?'>':'<')+' EMA21';
      document.getElementById('im').textContent=Number(i.macd_hist||0).toFixed(4);document.getElementById('im').style.color=i.macd_bull?'var(--green)':'var(--red)';
      document.getElementById('ims').textContent=i.macd_bull?'BULLISH':'BEARISH';
      document.getElementById('ia').textContent=(i.atr_pct||0)+'%';document.getElementById('ias').textContent=i.atr_pct>2?'HIGH VOL':i.atr_pct>0.5?'NORMAL':'LOW VOL';
      document.getElementById('iv').textContent='$'+Number(i.vwap||0).toLocaleString(undefined,{maximumFractionDigits:2});
      document.getElementById('ivs').textContent=i.above_vwap?'ABOVE VWAP':'BELOW VWAP';document.getElementById('ivs').style.color=i.above_vwap?'var(--green)':'var(--red)';
      document.getElementById('itr').textContent=i.trend||'--';document.getElementById('itr').style.color=(i.trend||'').includes('BULL')?'var(--green)':'var(--red)';
      document.getElementById('itrs').textContent=i.higher_highs&&i.higher_lows?'HH+HL structure':i.lower_lows&&i.lower_highs?'LL+LH structure':'Mixed';}
    if(t.length>0)document.getElementById('tl').innerHTML=t.slice(0,15).map(x=>`<div class="trade"><div class="tr1"><span class="chip ${x.direction==='BUY'?'buy':'sell'}">${x.direction}</span><span style="font-family:JetBrains Mono,monospace;font-size:11px">${(x.pair||'').replace('USDT','/USDT')}</span><span style="font-family:JetBrains Mono,monospace;font-size:10px">$${Number(x.price).toLocaleString()}</span><span class="chip ${x.status==='OPEN'?'open':'paper'}">${x.status}</span></div><div class="tr2">SL:${x.sl_pct}% TP:${x.tp_pct}% ${x.leverage}x ${x.timeframe} | ${new Date(x.time).toLocaleTimeString('en-IN')}</div><div class="tr3">🤖 ${x.reason}</div></div>`).join('');
    document.getElementById('lb').textContent=(l.lines||[]).slice(-15).join('\n');
  }catch(e){console.error(e);}
}
refresh();setInterval(refresh,8000);
</script></body></html>"""

@app.route("/api/status")
def api_status():
    return jsonify({"running":state.running,"claude":bool(CLAUDE_API_KEY),"positions":state.positions,"last_ind":state.last_ind,"last_pair":state.last_pair})

@app.route("/api/trades")
def api_trades():
    try:return jsonify(json.load(open("trades.json")) if os.path.exists("trades.json") else [])
    except:return jsonify(state.trades)

@app.route("/api/prices")
def api_prices():
    with state.lock:ext=dict(state.prices_ext)
    result={}
    for pair in PAIRS:
        price=state.last_price.get(pair,0);chg=ext.get(pair,{}).get("chg",0)
        if price>0:result[pair]={"price":price,"chg":chg,"tf":"live"}
    return jsonify(result)

@app.route("/api/log")
def api_log():
    lines=[]
    if os.path.exists("bot.log"):
        with open("bot.log") as f:lines=f.readlines()[-20:]
    return jsonify({"lines":[l.strip() for l in lines]})

if __name__=="__main__":
    state.running=True
    threading.Thread(target=bot_loop,daemon=True).start()
    log.info("Bot started automatically in background thread")
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port,debug=False)
