"""
AdaptiveBot PRO — Complete Professional Crypto Trading Bot
=========================================================
✅ WebSocket real-time feed (sub-second, no polling)
✅ Claude AI brain — analyzes every candle close
✅ Real indicators: RSI, EMA 9/21/50/200, MACD, BB, VWAP, ATR, FVG, OB
✅ Self-adjusting: timeframe, leverage, SL%, TP%, trailing stop
✅ Tick-by-tick trailing stop (follows price every second)
✅ Bracket orders: SL + TP + Trailing in one order
✅ Smart cooldown, position sizing, risk management
✅ Full trade log with timestamps
✅ 100% Free — Binance WS + Delta Testnet + Claude free tier
"""

import os, sys, json, time, hmac, hashlib, threading, logging
import requests, websocket, pandas as pd, numpy as np
from datetime import datetime
from collections import deque
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import BollingerBands, AverageTrueRange

# ─── LOGGING ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("AdaptiveBot")

# ─── CONFIG ────────────────────────────────────────────────
DELTA_API_KEY    = os.getenv("DELTA_API_KEY", "")
DELTA_API_SECRET = os.getenv("DELTA_API_SECRET", "")
CLAUDE_API_KEY   = os.getenv("CLAUDE_API_KEY", "")
DELTA_BASE       = "https://cdn-ind.testnet.deltaex.org"
BINANCE_WS       = "wss://stream.binance.com:9443/stream"
BINANCE_REST     = "https://api.binance.com"

# Trading pairs config
PAIRS = {
    "BTCUSDT": {"delta_symbol": "BTCUSD",  "product_id": 27,  "min_size": 1},
    "ETHUSDT": {"delta_symbol": "ETHUSD",  "product_id": 3,   "min_size": 1},
    "SOLUSDT": {"delta_symbol": "SOLUSD",  "product_id": 185, "min_size": 1},
}

# Risk config
STARTING_BALANCE    = 830      # ₹830 (~$10)
MAX_RISK_PCT        = 0.04     # 4% max risk per trade
MIN_CONFIDENCE      = 7        # Claude confidence threshold (1-10)
MAX_LEVERAGE        = 5        # Never go above 5x (safe for small capital)
TRADE_COOLDOWN      = 300      # 5 min cooldown per pair
MAX_OPEN_POSITIONS  = 2        # Max simultaneous positions
CANDLE_HISTORY      = 200      # Candles to keep in memory

# Timeframes to cycle through based on volatility
TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h"]

# ─── SHARED STATE ─────────────────────────────────────────
class BotState:
    def __init__(self):
        self.candles      = {p: {tf: deque(maxlen=CANDLE_HISTORY) for tf in TIMEFRAMES} for p in PAIRS}
        self.current_tf   = {p: "5m" for p in PAIRS}
        self.last_price   = {p: 0.0 for p in PAIRS}
        self.last_trade   = {p: 0 for p in PAIRS}
        self.positions    = {}       # open positions
        self.trades       = []       # completed trades
        self.balance      = STARTING_BALANCE
        self.ws_connected = False
        self.lock         = threading.Lock()

    def add_candle(self, pair, tf, candle):
        with self.lock:
            self.candles[pair][tf].append(candle)

    def get_df(self, pair, tf):
        with self.lock:
            candles = list(self.candles[pair][tf])
        if len(candles) < 30:
            return None
        return pd.DataFrame(candles)

state = BotState()

# ─── BINANCE REST: FETCH INITIAL CANDLES ──────────────────
def fetch_initial_candles(pair, tf, limit=200):
    """Fetch historical candles to warm up indicators"""
    try:
        url = f"{BINANCE_REST}/api/v3/klines"
        r = requests.get(url, params={"symbol": pair, "interval": tf, "limit": limit}, timeout=10)
        r.raise_for_status()
        for c in r.json():
            state.add_candle(pair, tf, {
                "open":   float(c[1]), "high":  float(c[2]),
                "low":    float(c[3]), "close": float(c[4]),
                "volume": float(c[5]), "time":  int(c[0])
            })
        log.info(f"Loaded {limit} {tf} candles for {pair}")
    except Exception as e:
        log.error(f"Initial candle fetch failed for {pair}/{tf}: {e}")

# ─── BINANCE WEBSOCKET: REAL-TIME CANDLES ─────────────────
class BinanceWebSocket:
    def __init__(self):
        self.ws = None
        self.reconnect_delay = 3

    def build_streams(self):
        """Subscribe to kline streams for all pairs and timeframes we care about"""
        streams = []
        for pair in PAIRS:
            p = pair.lower()
            for tf in ["1m", "5m", "15m", "1h"]:  # core timeframes
                streams.append(f"{p}@kline_{tf}")
            streams.append(f"{p}@trade")  # tick data for trailing stop
        return "/".join(streams)

    def on_open(self, ws):
        state.ws_connected = True
        log.info("✅ WebSocket connected — receiving real-time data")

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            stream = data.get("stream", "")
            payload = data.get("data", {})

            # Trade tick — update last price instantly (for trailing stop)
            if "@trade" in stream:
                pair = stream.split("@")[0].upper()
                if pair in state.last_price:
                    state.last_price[pair] = float(payload["p"])
                    # Check trailing stops on every tick
                    threading.Thread(
                        target=check_trailing_stops,
                        args=(pair,),
                        daemon=True
                    ).start()
                return

            # Kline (candle) update
            if "@kline" in stream:
                k = payload.get("k", {})
                pair = k.get("s", "")
                tf   = k.get("i", "")
                candle = {
                    "open":   float(k["o"]), "high":   float(k["h"]),
                    "low":    float(k["l"]), "close":  float(k["c"]),
                    "volume": float(k["v"]), "time":   int(k["t"])
                }
                state.add_candle(pair, tf, candle)
                state.last_price[pair] = float(k["c"])

                # On candle CLOSE — run full AI analysis
                if k.get("x"):  # x=True means candle is closed
                    log.info(f"Candle closed: {pair} {tf} @ ${float(k['c']):,.2f}")
                    threading.Thread(
                        target=analyze_and_trade,
                        args=(pair, tf),
                        daemon=True
                    ).start()

        except Exception as e:
            log.error(f"WS message error: {e}")

    def on_error(self, ws, error):
        state.ws_connected = False
        log.error(f"WebSocket error: {error}")

    def on_close(self, ws, code, msg):
        state.ws_connected = False
        log.warning(f"WebSocket closed ({code}). Reconnecting in {self.reconnect_delay}s...")
        time.sleep(self.reconnect_delay)
        self.reconnect_delay = min(self.reconnect_delay * 2, 60)
        self.connect()

    def connect(self):
        streams = self.build_streams()
        url = f"{BINANCE_WS}?streams={streams}"
        self.ws = websocket.WebSocketApp(
            url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        self.ws.run_forever(ping_interval=30, ping_timeout=10)

    def start(self):
        t = threading.Thread(target=self.connect, daemon=True)
        t.start()
        log.info("WebSocket thread started")

# ─── INDICATOR ENGINE ─────────────────────────────────────
def calculate_all_indicators(df):
    """Calculate every indicator we need from real candle data"""
    if df is None or len(df) < 30:
        return None

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]
    ind    = {}

    try:
        # Current price
        ind["price"]      = round(float(close.iloc[-1]), 4)
        ind["prev_close"] = round(float(close.iloc[-2]), 4)
        ind["change_pct"] = round((ind["price"] - ind["prev_close"]) / ind["prev_close"] * 100, 3)

        # RSI (14)
        rsi = RSIIndicator(close=close, window=14).rsi()
        ind["rsi"] = round(float(rsi.iloc[-1]), 2)
        ind["rsi_prev"] = round(float(rsi.iloc[-2]), 2)
        ind["rsi_divergence"] = ind["rsi"] > ind["rsi_prev"] and ind["price"] < ind["prev_close"]

        # EMAs
        for p in [9, 21, 50, 200]:
            if len(df) >= p:
                ema = EMAIndicator(close=close, window=p).ema_indicator()
                ind[f"ema{p}"] = round(float(ema.iloc[-1]), 4)

        ind["ema_bull"] = ind.get("ema9", 0) > ind.get("ema21", 0)
        ind["ema_strong_bull"] = (ind.get("ema9", 0) > ind.get("ema21", 0) and
                                   ind.get("ema21", 0) > ind.get("ema50", 0))

        # MACD
        macd_obj = MACD(close=close)
        ind["macd"]      = round(float(macd_obj.macd().iloc[-1]), 6)
        ind["macd_sig"]  = round(float(macd_obj.macd_signal().iloc[-1]), 6)
        ind["macd_hist"] = round(float(macd_obj.macd_diff().iloc[-1]), 6)
        ind["macd_bull"] = ind["macd_hist"] > 0
        ind["macd_cross_up"] = ind["macd_hist"] > 0 and float(macd_obj.macd_diff().iloc[-2]) < 0

        # Bollinger Bands
        bb = BollingerBands(close=close)
        ind["bb_upper"] = round(float(bb.bollinger_hband().iloc[-1]), 4)
        ind["bb_lower"] = round(float(bb.bollinger_lband().iloc[-1]), 4)
        ind["bb_mid"]   = round(float(bb.bollinger_mavg().iloc[-1]), 4)
        ind["bb_pct"]   = round(float(bb.bollinger_pband().iloc[-1]), 4)
        ind["bb_squeeze"] = (ind["bb_upper"] - ind["bb_lower"]) / ind["bb_mid"] < 0.02

        # ATR (volatility) — critical for adaptive SL/TP
        atr = AverageTrueRange(high=high, low=low, close=close)
        ind["atr"]     = round(float(atr.average_true_range().iloc[-1]), 4)
        ind["atr_pct"] = round(ind["atr"] / ind["price"] * 100, 3)

        # VWAP
        typical_price = (high + low + close) / 3
        ind["vwap"]       = round(float((typical_price * volume).sum() / volume.sum()), 4)
        ind["above_vwap"] = ind["price"] > ind["vwap"]

        # Volume analysis
        avg_vol = float(volume.rolling(20).mean().iloc[-1])
        ind["vol_ratio"]   = round(float(volume.iloc[-1]) / avg_vol, 2) if avg_vol > 0 else 1
        ind["high_volume"] = ind["vol_ratio"] > 1.5

        # Support & Resistance
        recent = df.tail(20)
        ind["support"]    = round(float(recent["low"].min()), 4)
        ind["resistance"] = round(float(recent["high"].max()), 4)
        ind["near_support"]    = abs(ind["price"] - ind["support"]) / ind["price"] < 0.005
        ind["near_resistance"] = abs(ind["price"] - ind["resistance"]) / ind["price"] < 0.005

        # Fair Value Gap (FVG)
        if len(df) >= 3:
            c_vals = close.values
            h_vals = high.values
            l_vals = low.values
            ind["fvg_bull"] = float(l_vals[-1]) > float(h_vals[-3])
            ind["fvg_bear"] = float(h_vals[-1]) < float(l_vals[-3])
        else:
            ind["fvg_bull"] = ind["fvg_bear"] = False

        # Order Block detection
        # Bullish OB: last bearish candle before strong bullish move
        if len(df) >= 5:
            last5_close = close.values[-5:]
            last5_open  = df["open"].values[-5:]
            impulse_up   = (last5_close[-1] - last5_open[-1]) / last5_open[-1] * 100 > 0.5
            impulse_down = (last5_open[-1] - last5_close[-1]) / last5_open[-1] * 100 > 0.5
            ind["ob_bull"] = impulse_up and last5_close[-2] < last5_open[-2]
            ind["ob_bear"] = impulse_down and last5_close[-2] > last5_open[-2]
        else:
            ind["ob_bull"] = ind["ob_bear"] = False

        # Market structure
        highs = high.rolling(5).max()
        lows  = low.rolling(5).min()
        ind["higher_highs"] = float(highs.iloc[-1]) > float(highs.iloc[-6]) if len(df) >= 6 else False
        ind["higher_lows"]  = float(lows.iloc[-1]) > float(lows.iloc[-6]) if len(df) >= 6 else False
        ind["lower_lows"]   = float(lows.iloc[-1]) < float(lows.iloc[-6]) if len(df) >= 6 else False
        ind["lower_highs"]  = float(highs.iloc[-1]) < float(highs.iloc[-6]) if len(df) >= 6 else False

        # Overall trend
        if ind["higher_highs"] and ind["higher_lows"]:
            ind["trend"] = "STRONG_BULL"
        elif ind["lower_lows"] and ind["lower_highs"]:
            ind["trend"] = "STRONG_BEAR"
        elif ind.get("ema_bull"):
            ind["trend"] = "BULL"
        else:
            ind["trend"] = "BEAR"

        return ind

    except Exception as e:
        log.error(f"Indicator error: {e}")
        return None

# ─── CLAUDE AI BRAIN ──────────────────────────────────────
def claude_decide(pair, ind, tf):
    """Send real indicators to Claude for smart adaptive decision"""
    if not CLAUDE_API_KEY:
        return rule_based_fallback(ind)

    prompt = f"""You are a professional crypto trading AI. Analyze this REAL live market data and make a trading decision.

PAIR: {pair} | TIMEFRAME: {tf}
TIMESTAMP: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

=== REAL LIVE INDICATORS ===
Price: ${ind['price']:,} | Change: {ind['change_pct']}%
Trend: {ind['trend']}

RSI(14): {ind['rsi']} (prev: {ind['rsi_prev']}) {'⚠ OVERSOLD' if ind['rsi']<30 else '⚠ OVERBOUGHT' if ind['rsi']>70 else ''}
RSI Divergence: {ind['rsi_divergence']}

EMA9: {ind.get('ema9','N/A')} | EMA21: {ind.get('ema21','N/A')} | EMA50: {ind.get('ema50','N/A')} | EMA200: {ind.get('ema200','N/A')}
EMA Bull Cross: {ind['ema_bull']} | Strong Bull (9>21>50): {ind['ema_strong_bull']}

MACD Hist: {ind['macd_hist']} ({'BULL' if ind['macd_bull'] else 'BEAR'}) | Fresh Cross: {ind['macd_cross_up']}
BB%: {ind['bb_pct']} (0=bottom 1=top) | BB Squeeze: {ind['bb_squeeze']}
ATR: {ind['atr_pct']}% | VWAP: {ind['vwap']} | Above VWAP: {ind['above_vwap']}
Volume: {ind['vol_ratio']}x average | High Volume: {ind['high_volume']}

Support: {ind['support']} | Near Support: {ind['near_support']}
Resistance: {ind['resistance']} | Near Resistance: {ind['near_resistance']}

Smart Money:
- Fair Value Gap Bull: {ind['fvg_bull']} | FVG Bear: {ind['fvg_bear']}
- Order Block Bull: {ind['ob_bull']} | OB Bear: {ind['ob_bear']}
- Market Structure: HH={ind['higher_highs']} HL={ind['higher_lows']} LL={ind['lower_lows']} LH={ind['lower_highs']}

=== TRADING RULES ===
- Capital: ₹830 (~$10 USD), small account
- Max leverage: 5x
- Loss MUST always be smaller than profit potential
- Only trade when multiple signals agree
- Confidence must be >= 7 to trade
- In ranging/choppy market → NO_TRADE

=== YOUR TASK ===
Decide and return ONLY this JSON:
{{
  "direction": "BUY | SELL | NO_TRADE",
  "confidence": <1-10>,
  "stop_loss_pct": <0.3-3.0>,
  "take_profit_pct": <1.0-10.0>,
  "leverage": <1-5>,
  "trail_pct": <0-2.0>,
  "timeframe": "<best tf to use next: 1m/3m/5m/15m/30m/1h/2h/4h>",
  "entry_reason": "<what signals agree>",
  "risk_note": "<any warning about current market>"
}}

Rules for your decision:
- take_profit_pct must be at least 2.5x stop_loss_pct
- trail_pct = 0 if no clear trend, 0.3-1.0 in strong trend
- leverage 1x if ATR > 2%, 2-3x if ATR 0.5-2%, 4-5x if ATR < 0.5% AND strong signal
- NO_TRADE if: BB squeeze with no direction, RSI 40-60, conflicting signals"""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=15
        )
        text = r.json()["content"][0]["text"]
        start = text.find("{")
        end   = text.rfind("}") + 1
        decision = json.loads(text[start:end])
        log.info(f"🤖 Claude [{pair}]: {decision['direction']} | Conf:{decision['confidence']}/10 | SL:{decision['stop_loss_pct']}% TP:{decision['take_profit_pct']}% | {decision['entry_reason']}")
        return decision
    except Exception as e:
        log.warning(f"Claude failed ({e}) → rule-based fallback")
        return rule_based_fallback(ind)

# ─── RULE-BASED FALLBACK ──────────────────────────────────
def rule_based_fallback(ind):
    """Smart multi-signal rule engine — runs when Claude unavailable"""
    bull = 0
    bear = 0

    if ind["rsi"] < 35:              bull += 2
    elif ind["rsi"] < 45:            bull += 1
    if ind["rsi"] > 65:              bear += 2
    elif ind["rsi"] > 55:            bear += 1
    if ind["ema_bull"]:              bull += 1
    else:                            bear += 1
    if ind["ema_strong_bull"]:       bull += 1
    if ind["macd_bull"]:             bull += 1
    else:                            bear += 1
    if ind["macd_cross_up"]:         bull += 2
    if ind["above_vwap"]:            bull += 1
    else:                            bear += 1
    if ind["high_volume"]:
        if bull > bear:              bull += 1
        else:                        bear += 1
    if ind["fvg_bull"]:              bull += 1
    if ind["fvg_bear"]:              bear += 1
    if ind["ob_bull"]:               bull += 1
    if ind["ob_bear"]:               bear += 1
    if ind["near_support"]:          bull += 1
    if ind["near_resistance"]:       bear += 1
    if ind["higher_highs"] and ind["higher_lows"]: bull += 1
    if ind["lower_lows"] and ind["lower_highs"]:   bear += 1

    atr = ind["atr_pct"]
    sl  = max(0.4, min(2.5, atr * 1.5))
    tp  = sl * 3.0
    lev = 1 if atr > 2 else (3 if atr < 0.5 else 2)
    trail = round(sl * 0.4, 2) if bull >= 5 or bear >= 5 else 0

    direction  = "NO_TRADE"
    confidence = 5
    if bull >= 5 and bull > bear:
        direction  = "BUY"
        confidence = min(5 + bull, 10)
    elif bear >= 5 and bear > bull:
        direction  = "SELL"
        confidence = min(5 + bear, 10)

    tf = "15m" if atr > 1.5 else ("5m" if atr > 0.5 else "1m")

    return {
        "direction": direction,
        "confidence": confidence,
        "stop_loss_pct": round(sl, 2),
        "take_profit_pct": round(tp, 2),
        "leverage": lev,
        "trail_pct": trail,
        "timeframe": tf,
        "entry_reason": f"Bull={bull} Bear={bear} ATR={atr}% RSI={ind['rsi']}",
        "risk_note": "Rule-based mode"
    }

# ─── TRAILING STOP (TICK BY TICK) ─────────────────────────
def check_trailing_stops(pair):
    """Runs on every price tick — moves trailing stop up in real-time"""
    price = state.last_price.get(pair, 0)
    if price == 0:
        return

    with state.lock:
        pos = state.positions.get(pair)

    if not pos:
        return

    trail_pct = pos.get("trail_pct", 0)
    if trail_pct == 0:
        return

    direction = pos["direction"]
    trail_dist = price * trail_pct / 100

    if direction == "BUY":
        new_sl = price - trail_dist
        if new_sl > pos.get("current_sl", 0):
            with state.lock:
                state.positions[pair]["current_sl"] = new_sl
            log.info(f"🔺 Trail UP {pair}: SL moved to ${new_sl:,.2f} (price=${price:,.2f})")
            # Update on Delta Exchange
            if DELTA_API_KEY and pos.get("order_id"):
                update_trailing_stop(pos["order_id"], pos["product_id"], new_sl, pos["tp_price"])

    elif direction == "SELL":
        new_sl = price + trail_dist
        if new_sl < pos.get("current_sl", float("inf")):
            with state.lock:
                state.positions[pair]["current_sl"] = new_sl
            log.info(f"🔻 Trail DOWN {pair}: SL moved to ${new_sl:,.2f} (price=${price:,.2f})")
            if DELTA_API_KEY and pos.get("order_id"):
                update_trailing_stop(pos["order_id"], pos["product_id"], new_sl, pos["tp_price"])

# ─── ANALYZE AND TRADE ────────────────────────────────────
def analyze_and_trade(pair, candle_tf):
    """Called on every candle close — full AI analysis + trade decision"""
    now = time.time()

    # Only analyze on the pair's current selected timeframe
    if candle_tf != state.current_tf[pair]:
        return

    # Cooldown check
    if now - state.last_trade[pair] < TRADE_COOLDOWN:
        return

    # Max positions check
    if len(state.positions) >= MAX_OPEN_POSITIONS:
        log.info(f"Max positions ({MAX_OPEN_POSITIONS}) reached — skipping {pair}")
        return

    # Already in position for this pair
    if pair in state.positions:
        log.info(f"{pair}: Already in position")
        return

    # Get candle data
    df = state.get_df(pair, candle_tf)
    if df is None:
        return

    # Calculate all indicators
    ind = calculate_all_indicators(df)
    if not ind:
        return

    # AI decision
    decision = claude_decide(pair, ind, candle_tf)

    # Update timeframe if AI suggests better one
    suggested_tf = decision.get("timeframe", candle_tf)
    if suggested_tf != state.current_tf[pair] and suggested_tf in TIMEFRAMES:
        log.info(f"{pair}: Switching TF {state.current_tf[pair]} → {suggested_tf}")
        state.current_tf[pair] = suggested_tf
        # Fetch candles for new TF if needed
        if len(state.candles[pair][suggested_tf]) < 30:
            threading.Thread(
                target=fetch_initial_candles,
                args=(pair, suggested_tf),
                daemon=True
            ).start()

    # Trade gate
    if decision["direction"] == "NO_TRADE":
        log.info(f"{pair}: NO_TRADE — {decision['entry_reason']}")
        return

    if decision["confidence"] < MIN_CONFIDENCE:
        log.info(f"{pair}: Confidence {decision['confidence']}/10 < {MIN_CONFIDENCE} — skip")
        return

    # Position sizing — adaptive
    price      = ind["price"]
    risk_amt   = state.balance * MAX_RISK_PCT
    sl_dollar  = price * decision["stop_loss_pct"] / 100
    size       = max(1, int(risk_amt / sl_dollar * decision["leverage"]))

    log.info(f"{'='*50}")
    log.info(f"📊 SIGNAL: {pair} {decision['direction']}")
    log.info(f"   Price: ${price:,} | Size: {size} | Leverage: {decision['leverage']}x")
    log.info(f"   SL: {decision['stop_loss_pct']}% | TP: {decision['take_profit_pct']}% | Trail: {decision['trail_pct']}%")
    log.info(f"   Reason: {decision['entry_reason']}")
    log.info(f"   Risk note: {decision.get('risk_note', '')}")
    log.info(f"{'='*50}")

    # Place order
    order_id = None
    sl_price  = price * (1 - decision["stop_loss_pct"]/100) if decision["direction"] == "BUY" else price * (1 + decision["stop_loss_pct"]/100)
    tp_price  = price * (1 + decision["take_profit_pct"]/100) if decision["direction"] == "BUY" else price * (1 - decision["take_profit_pct"]/100)

    if DELTA_API_KEY:
        resp = place_bracket_order(
            pair_info  = PAIRS[pair],
            side       = "buy" if decision["direction"] == "BUY" else "sell",
            size       = size,
            price      = price,
            sl_pct     = decision["stop_loss_pct"],
            tp_pct     = decision["take_profit_pct"],
            trail_pct  = decision["trail_pct"],
            leverage   = decision["leverage"]
        )
        if resp and resp.get("success"):
            order_id = resp["result"]["id"]
            log.info(f"✅ ORDER PLACED on Delta Testnet: ID={order_id}")
        else:
            log.error(f"❌ Order failed: {resp}")
    else:
        log.info(f"📝 Paper log only (no Delta API key)")

    # Track position
    with state.lock:
        state.positions[pair] = {
            "direction":  decision["direction"],
            "entry_price": price,
            "sl_price":    sl_price,
            "tp_price":    tp_price,
            "current_sl":  sl_price,
            "trail_pct":   decision["trail_pct"],
            "leverage":    decision["leverage"],
            "size":        size,
            "order_id":    order_id,
            "product_id":  PAIRS[pair]["product_id"],
            "entry_time":  datetime.now().isoformat()
        }
        state.last_trade[pair] = now

    # Save trade
    save_trade(pair, decision, price, size, order_id, sl_price, tp_price)

# ─── DELTA EXCHANGE API ───────────────────────────────────
def delta_sign_request(method, path, body=""):
    ts  = str(int(time.time()))
    msg = method + ts + path + (json.dumps(body) if body else "")
    sig = hmac.new(DELTA_API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {
        "api-key": DELTA_API_KEY,
        "timestamp": ts,
        "signature": sig,
        "Content-Type": "application/json"
    }

def delta_post(path, body):
    headers = delta_sign_request("POST", path, body)
    try:
        r = requests.post(DELTA_BASE + path, headers=headers, json=body, timeout=10)
        return r.json()
    except Exception as e:
        log.error(f"Delta POST {path}: {e}")
        return None

def delta_put(path, body):
    headers = delta_sign_request("PUT", path, body)
    try:
        r = requests.put(DELTA_BASE + path, headers=headers, json=body, timeout=10)
        return r.json()
    except Exception as e:
        log.error(f"Delta PUT {path}: {e}")
        return None

def place_bracket_order(pair_info, side, size, price, sl_pct, tp_pct, trail_pct, leverage):
    """One API call: market order + SL + TP + trailing stop"""
    sl_price = price * (1 - sl_pct/100) if side == "buy" else price * (1 + sl_pct/100)
    tp_price = price * (1 + tp_pct/100) if side == "buy" else price * (1 - tp_pct/100)
    trail_amt = round(price * trail_pct / 100, 2)

    body = {
        "product_id":   pair_info["product_id"],
        "size":         size,
        "side":         side,
        "order_type":   "market_order",
        "leverage":     str(leverage),
        "bracket_stop_loss_price":       str(round(sl_price, 2)),
        "bracket_stop_loss_limit_price": str(round(sl_price * (0.999 if side=="buy" else 1.001), 2)),
        "bracket_take_profit_price":     str(round(tp_price, 2)),
        "bracket_take_profit_limit_price": str(round(tp_price * (1.001 if side=="buy" else 0.999), 2)),
        "bracket_trail_amount":          str(trail_amt) if trail_pct > 0 else "0"
    }
    return delta_post("/v2/orders", body)

def update_trailing_stop(order_id, product_id, new_sl, tp_price):
    """Update bracket order SL as trailing stop moves"""
    body = {
        "id": order_id,
        "product_id": product_id,
        "bracket_stop_loss_price": str(round(new_sl, 2)),
        "bracket_take_profit_price": str(round(tp_price, 2))
    }
    delta_put("/v2/orders/bracket", body)

# ─── TRADE LOGGING ────────────────────────────────────────
def save_trade(pair, decision, price, size, order_id, sl_price, tp_price):
    trade = {
        "id":          order_id or f"paper_{int(time.time())}",
        "time":        datetime.now().isoformat(),
        "pair":        pair,
        "direction":   decision["direction"],
        "price":       price,
        "size":        size,
        "sl_price":    round(sl_price, 2),
        "tp_price":    round(tp_price, 2),
        "sl_pct":      decision["stop_loss_pct"],
        "tp_pct":      decision["take_profit_pct"],
        "leverage":    decision["leverage"],
        "trail_pct":   decision["trail_pct"],
        "confidence":  decision["confidence"],
        "timeframe":   decision["timeframe"],
        "reason":      decision["entry_reason"],
        "risk_note":   decision.get("risk_note", ""),
        "status":      "OPEN" if order_id else "PAPER"
    }
    with state.lock:
        state.trades.insert(0, trade)
        if len(state.trades) > 500:
            state.trades.pop()
    try:
        existing = json.load(open("trades.json")) if os.path.exists("trades.json") else []
        existing.insert(0, trade)
        json.dump(existing[:500], open("trades.json", "w"), indent=2)
    except Exception as e:
        log.error(f"Save trade error: {e}")

# ─── STARTUP ──────────────────────────────────────────────
def startup():
    log.info("=" * 60)
    log.info("  AdaptiveBot PRO — Starting Up")
    log.info(f"  Pairs: {list(PAIRS.keys())}")
    log.info(f"  Balance: ₹{STARTING_BALANCE}")
    log.info(f"  Delta API: {'✅ Connected' if DELTA_API_KEY else '📝 Paper only'}")
    log.info(f"  Claude AI: {'✅ Active' if CLAUDE_API_KEY else '🔧 Rule-based fallback'}")
    log.info("=" * 60)

    # Pre-load candles for all pairs and core timeframes
    log.info("Loading historical candles...")
    for pair in PAIRS:
        for tf in ["5m", "15m", "1h"]:
            fetch_initial_candles(pair, tf, limit=200)
    log.info("Historical candles loaded ✅")

def run():
    startup()

    # Start WebSocket
    bws = BinanceWebSocket()
    bws.start()

    # Wait for WS to connect
    log.info("Waiting for WebSocket connection...")
    for _ in range(30):
        if state.ws_connected:
            break
        time.sleep(1)

    if not state.ws_connected:
        log.error("WebSocket failed to connect! Check internet.")
        sys.exit(1)

    log.info("🚀 Bot is LIVE — receiving real-time data")
    log.info("Trades will execute when AI confidence >= 7/10")

    # Keep main thread alive
    try:
        while True:
            time.sleep(10)
            # Heartbeat log
            prices = {p: f"${state.last_price[p]:,.2f}" for p in PAIRS if state.last_price[p] > 0}
            log.info(f"Heartbeat | Prices: {prices} | Positions: {list(state.positions.keys())} | WS: {'✅' if state.ws_connected else '❌'}")
    except KeyboardInterrupt:
        log.info("Bot stopped by user")

if __name__ == "__main__":
    run()
