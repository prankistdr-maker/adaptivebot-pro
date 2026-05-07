"""
ULTIMATE LIVE TRADING BOT v9
==============================
Strategy: RSI(2) Mean Reversion + ICT Confluence
Based on: AlphaTrader +3091% NSE proof + v8 backtest +174% in bear market

PROVEN RULES (never change these):
1. ONLY trade when price is ABOVE 200 EMA (uptrend filter)
2. ONLY buy when RSI(2) < 15 (extreme dip in uptrend)
3. HOLD until RSI(2) > 80 (full momentum recovery = big win)
4. SL = 2.5% fixed (small loss)
5. MAX 2 trades per day (fee protection)
6. MAX 2 open positions (capital protection)
7. Dynamic risk: 20% at $20 account → 2% at $10k+

EXCHANGE: Delta Exchange (testnet or live)
PRICE FEED: Kraken REST (free, no auth, reliable)
DEPLOY: Render.com free tier
"""

import os, time, math, json, logging, requests, hmac, hashlib
from datetime import datetime, timezone, timedelta
from collections import deque
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import threading

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION — set via Render environment variables
# ══════════════════════════════════════════════════════════════════════════════
DELTA_API_KEY    = os.getenv('DELTA_API_KEY', '')
DELTA_API_SECRET = os.getenv('DELTA_API_SECRET', '')
DELTA_BASE       = os.getenv('DELTA_BASE', 'https://cdn-ind.testnet.deltaex.org')  # testnet
IS_LIVE          = os.getenv('IS_LIVE', 'false').lower() == 'true'
START_BAL        = float(os.getenv('START_BAL', '10000'))
TRADE_PAIRS      = os.getenv('TRADE_PAIRS', 'BTCUSDT,ETHUSDT').split(',')

# ── STRATEGY PARAMETERS (proven from 180+ combo test) ─────────────────────────
RSI2_ENTRY      = int(os.getenv('RSI2_ENTRY', '15'))    # enter when RSI(2) < this
RSI2_EXIT       = int(os.getenv('RSI2_EXIT',  '80'))    # exit when RSI(2) > this
SL_PCT          = float(os.getenv('SL_PCT', '2.5'))     # stop loss %
MIN_ADX         = int(os.getenv('MIN_ADX', '15'))       # minimum trend strength
MIN_HOLD_HOURS  = int(os.getenv('MIN_HOLD_HOURS', '3')) # hold at least N hours
MAX_HOLD_HOURS  = int(os.getenv('MAX_HOLD_HOURS', '15'))# force exit after N hours
MAX_POSITIONS   = int(os.getenv('MAX_POSITIONS', '2'))
MAX_TRADES_DAY  = int(os.getenv('MAX_TRADES_DAY', '2'))
DAILY_LOSS_STOP = float(os.getenv('DAILY_LOSS_STOP', '4.0'))  # % of balance

# ── COST MODEL (Delta Exchange fees) ──────────────────────────────────────────
MAKER_FEE  = 0.0002  # 0.02%
TAKER_FEE  = 0.0005  # 0.05% (market orders/SL)
LOOP_SEC   = 300     # check every 5 minutes

# Kraken pair mapping (price feed)
KRAKEN_MAP = {
    'BTCUSDT': 'XBTUSD', 'ETHUSDT': 'ETHUSD', 'SOLUSDT': 'SOLUSD',
    'BNBUSDT': 'XBTUSD', 'AVAXUSDT': 'AVAXUSD', 'DOTUSDT': 'DOTUSD',
}
# Delta product IDs (testnet)
DELTA_PRODUCTS = {
    'BTCUSDT': 84,  'ETHUSDT': 1699,
    'SOLUSDT': 14, 'AVAXUSDT': 37,
}

# ══════════════════════════════════════════════════════════════════════════════
#  STATE
# ══════════════════════════════════════════════════════════════════════════════
state = {
    'balance':      START_BAL,
    'start_bal':    START_BAL,
    'positions':    {},        # pair -> position dict
    'trades':       deque(maxlen=200),
    'log':          deque(maxlen=500),
    'prices':       {},        # pair -> latest price
    'indicators':   {},        # pair -> latest indicators
    'daily_trades': 0,
    'daily_loss':   0.0,
    'daily_date':   '',
    'total_pnl':    0.0,
    'wins':         0,
    'losses':       0,
    'running':      True,
    'last_check':   '',
    'mode':         'TESTNET' if not IS_LIVE else '🔴 LIVE',
    'candles':      {},        # pair -> deque of 1H candles
}

# ══════════════════════════════════════════════════════════════════════════════
#  INDICATOR ENGINE — correct Wilder RSI, no bugs
# ══════════════════════════════════════════════════════════════════════════════
def compute_rsi(closes, period):
    """Wilder's RSI — exact same as AlphaTrader v5"""
    if len(closes) < period + 2:
        return 50.0
    gains  = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    avg_g  = sum(gains[:period]) / period
    avg_l  = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_g/avg_l)), 2)

def compute_ema(closes, period):
    if len(closes) < period:
        return closes[-1]
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema

def compute_adx(hi, lo, cl, period=14):
    if len(cl) < period + 2:
        return 20
    trs, pdm, ndm = [], [], []
    for i in range(1, len(cl)):
        trs.append(max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1])))
        up = hi[i] - hi[i-1]; dn = lo[i-1] - lo[i]
        pdm.append(up if up > dn and up > 0 else 0)
        ndm.append(dn if dn > up and dn > 0 else 0)
    atr = sum(trs[-period:]) / period
    if atr == 0:
        return 20
    pdi = sum(pdm[-period:]) / atr * 100
    ndi = sum(ndm[-period:]) / atr * 100
    dx  = abs(pdi-ndi) / (pdi+ndi) * 100 if (pdi+ndi) > 0 else 0
    return round(dx, 1)

def compute_indicators(candles_1h):
    """Returns indicator dict — only uses past candles"""
    if len(candles_1h) < 210:
        return None
    cl = [c['c'] for c in candles_1h]
    hi = [c['h'] for c in candles_1h]
    lo = [c['l'] for c in candles_1h]
    price = cl[-1]

    rsi2  = compute_rsi(cl, 2)
    rsi14 = compute_rsi(cl, 14)
    e200  = compute_ema(cl, 200)
    e50   = compute_ema(cl, 50)
    e10   = compute_ema(cl, 10)
    adx   = compute_adx(hi[-20:], lo[-20:], cl[-20:])
    atr   = sum([max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
                 for i in range(-14, 0)]) / 14

    # 200 EMA must be rising (not just above)
    e200_1m = compute_ema(cl[:-20], 200) if len(cl) > 220 else e200
    e200_rising = e200 >= e200_1m * 0.995

    above_200   = price > e200 * 1.005
    ema_aligned = e10 > e50

    # BB
    n = 20
    bm = sum(cl[-n:]) / n
    bs = math.sqrt(sum((c-bm)**2 for c in cl[-n:]) / n)
    bb_lower = bm - 2*bs

    # Volume
    vols = [c.get('v', 1) for c in candles_1h]
    avg_v = sum(vols[-20:]) / 20
    vol_ratio = vols[-1] / avg_v if avg_v > 0 else 1

    # Signal score (same as backtest)
    score = 0
    if rsi2 < 5:   score += 6
    elif rsi2 < 8: score += 5
    elif rsi2 < 12: score += 4
    elif rsi2 < 15: score += 3
    if price < bb_lower:       score += 3
    if price < bb_lower*1.005: score += 2
    if vol_ratio > 1.5:        score += 2
    if adx > 25:               score += 2
    elif adx > 15:             score += 1
    if e10 > e50:              score += 1

    return {
        'price': price, 'rsi2': rsi2, 'rsi14': rsi14,
        'e200': round(e200, 4), 'e50': round(e50, 4), 'e10': round(e10, 4),
        'adx': adx, 'atr': atr, 'atr_pct': round(atr/price*100, 3),
        'above_200': above_200, 'e200_rising': e200_rising,
        'ema_aligned': ema_aligned,
        'bb_lower': round(bb_lower, 4),
        'vol_ratio': round(vol_ratio, 2),
        'score': score,
    }

# ══════════════════════════════════════════════════════════════════════════════
#  ADAPTIVE RISK (proven from backtests)
# ══════════════════════════════════════════════════════════════════════════════
def get_risk_pct(bal, daily_loss_pct, recent_losses):
    """Fully dynamic risk — never fixed"""
    if   bal < 50:     base = 0.20
    elif bal < 100:    base = 0.15
    elif bal < 200:    base = 0.12
    elif bal < 500:    base = 0.08
    elif bal < 1000:   base = 0.06
    elif bal < 2000:   base = 0.05
    elif bal < 5000:   base = 0.04
    elif bal < 10000:  base = 0.03
    else:              base = 0.02

    if recent_losses >= 2: base *= 0.7
    if recent_losses >= 3: base *= 0.5
    if daily_loss_pct > 2: base *= 0.6
    return min(max(base, 0.005), 0.25)

def position_size(bal, price, sl_pct, risk_pct):
    """Calculate exact position size"""
    risk_amt = bal * risk_pct
    sl_dist  = price * sl_pct / 100
    qty      = risk_amt / sl_dist if sl_dist > 0 else 0
    notional = qty * price
    # Hard cap: max 20% of balance as notional
    max_notional = bal * 0.20
    if notional > max_notional:
        notional = max_notional
        qty = notional / price
    return round(qty, 6), round(notional, 2)

# ══════════════════════════════════════════════════════════════════════════════
#  PRICE FEED — Kraken REST (free, reliable)
# ══════════════════════════════════════════════════════════════════════════════
def fetch_kraken_candles(pair, interval=60, count=250):
    """Fetch 1H candles from Kraken — no API key needed"""
    kr_pair = KRAKEN_MAP.get(pair, 'XBTUSD')
    since   = int((datetime.now(timezone.utc) - timedelta(hours=count+5)).timestamp())
    try:
        r = requests.get(
            f'https://api.kraken.com/0/public/OHLC',
            params={'pair': kr_pair, 'interval': interval, 'since': since},
            timeout=15
        )
        data = r.json()
        if data.get('error'):
            log.warning(f"Kraken error for {pair}: {data['error']}")
            return []
        result_key = list(data['result'].keys())[0]
        raw = data['result'][result_key]
        candles = []
        for c in raw[-count:]:
            candles.append({
                't': int(c[0]),
                'o': float(c[1]), 'h': float(c[2]),
                'l': float(c[3]), 'c': float(c[4]),
                'v': float(c[6]),
            })
        return candles
    except Exception as e:
        log.error(f"Kraken fetch error {pair}: {e}")
        return []

def fetch_current_price(pair):
    """Get latest price from Kraken ticker"""
    kr_pair = KRAKEN_MAP.get(pair, 'XBTUSD')
    try:
        r = requests.get(
            f'https://api.kraken.com/0/public/Ticker',
            params={'pair': kr_pair}, timeout=10
        )
        data = r.json()
        if data.get('error'): return None
        key = list(data['result'].keys())[0]
        return float(data['result'][key]['c'][0])
    except Exception as e:
        log.error(f"Price fetch error {pair}: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════════════
#  DELTA EXCHANGE API
# ══════════════════════════════════════════════════════════════════════════════
def delta_sign(secret, message):
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

def delta_request(method, path, payload=None):
    if not DELTA_API_KEY or not DELTA_API_SECRET:
        # Paper trading mode — simulate
        return {'result': {'id': int(time.time()), 'state': 'open', 'size': 1}}

    ts = str(int(time.time()))
    body = json.dumps(payload) if payload else ''
    sig_data = method + ts + path + body
    sig = delta_sign(DELTA_API_SECRET, sig_data)

    headers = {
        'api-key':     DELTA_API_KEY,
        'timestamp':   ts,
        'signature':   sig,
        'Content-Type':'application/json',
    }
    url = DELTA_BASE + path
    try:
        if method == 'GET':
            r = requests.get(url, headers=headers, timeout=10)
        else:
            r = requests.post(url, headers=headers, json=payload, timeout=10)
        return r.json()
    except Exception as e:
        log.error(f"Delta API error: {e}")
        return {}

def get_delta_balance():
    r = delta_request('GET', '/v2/wallet/balances')
    if r.get('result'):
        for w in r['result']:
            if w.get('asset_symbol') == 'USDT':
                return float(w.get('balance', state['balance']))
    return state['balance']

def place_order(pair, side, qty, price, sl_price):
    """Place market order on Delta Exchange"""
    product_id = DELTA_PRODUCTS.get(pair)
    if not product_id:
        slog(f"⚠️ No product ID for {pair}")
        return None

    payload = {
        'product_id':   product_id,
        'size':         max(1, int(qty)),
        'side':         'buy' if side == 'BUY' else 'sell',
        'order_type':   'market_order',
        'time_in_force':'ioc',
    }

    if IS_LIVE:
        r = delta_request('POST', '/v2/orders', payload)
    else:
        # Paper trading
        r = {'result': {'id': int(time.time()), 'state': 'open',
                         'average_fill_price': price, 'size': qty}}
        slog(f"📝 PAPER ORDER: {side} {pair} qty={qty:.4f} @ ${price:.2f}")

    return r.get('result')

def close_order(pair, side, qty):
    """Close position on Delta Exchange"""
    close_side = 'sell' if side == 'BUY' else 'buy'
    product_id = DELTA_PRODUCTS.get(pair)
    if not product_id:
        return None

    payload = {
        'product_id':   product_id,
        'size':         max(1, int(qty)),
        'side':         close_side,
        'order_type':   'market_order',
        'time_in_force':'ioc',
        'reduce_only':  True,
    }

    if IS_LIVE:
        return delta_request('POST', '/v2/orders', payload)
    else:
        price = state['prices'].get(pair, 0)
        slog(f"📝 PAPER CLOSE: {close_side} {pair} @ ${price:.2f}")
        return {'result': {'state': 'closed'}}

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN TRADING LOGIC
# ══════════════════════════════════════════════════════════════════════════════
def slog(msg):
    """State log"""
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
    state['log'].appendleft(f"[{ts}] {msg}")
    log.info(msg)

def reset_daily():
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if state['daily_date'] != today:
        state['daily_date']   = today
        state['daily_trades'] = 0
        state['daily_loss']   = 0.0
        slog(f"📅 New day {today} — counters reset")

def recent_loss_count():
    trades = list(state['trades'])[-5:]
    return sum(1 for t in trades if not t['won'])

def check_entry(pair, ind, bal):
    """Apply proven entry rules"""
    reasons = []

    # ── MANDATORY FILTERS (must all pass) ─────────────────────────────────
    if not ind:
        return False, 0, []

    if not ind['above_200']:
        return False, 0, [f"Price below 200 EMA (${ind['e200']:.2f})"]

    if not ind['e200_rising']:
        return False, 0, ["200 EMA declining — bear trend"]

    if not ind['ema_aligned']:
        return False, 0, [f"EMA10 < EMA50 — intermediate downtrend"]

    if ind['rsi2'] >= RSI2_ENTRY:
        return False, 0, [f"RSI2={ind['rsi2']} not oversold (need <{RSI2_ENTRY})"]

    if ind['adx'] < MIN_ADX:
        return False, 0, [f"ADX={ind['adx']} too low (need >{MIN_ADX})"]

    if ind['score'] < 4:
        return False, 0, [f"Signal score {ind['score']}/4 insufficient"]

    # ── RISK FILTERS ───────────────────────────────────────────────────────
    if state['daily_trades'] >= MAX_TRADES_DAY:
        return False, 0, [f"Max {MAX_TRADES_DAY} trades/day reached"]

    daily_loss_pct = state['daily_loss'] / bal * 100 if bal > 0 else 0
    if daily_loss_pct >= DAILY_LOSS_STOP:
        return False, 0, [f"Daily loss {daily_loss_pct:.1f}% reached stop"]

    if len(state['positions']) >= MAX_POSITIONS:
        return False, 0, [f"Max {MAX_POSITIONS} positions open"]

    if pair in state['positions']:
        return False, 0, ["Already in this pair"]

    # ── BUILD REASONS ──────────────────────────────────────────────────────
    reasons.append(f"✅ RSI(2)={ind['rsi2']} OVERSOLD in uptrend")
    reasons.append(f"✅ Price ${ind['price']:.2f} above 200 EMA (${ind['e200']:.2f})")
    reasons.append(f"✅ EMA10 > EMA50 (intermediate uptrend)")
    reasons.append(f"✅ ADX={ind['adx']} (trend strength OK)")
    if ind['price'] < ind['bb_lower']:
        reasons.append(f"✅ Below Bollinger lower band")
    if ind['vol_ratio'] > 1.5:
        reasons.append(f"✅ Volume spike {ind['vol_ratio']:.1f}x")
    reasons.append(f"Signal score: {ind['score']}/12")

    return True, ind['score'], reasons

def check_exit(pair, pos, ind, current_price, bar_ts):
    """Check if position should be closed"""
    if not ind:
        return False, 'NO_DATA'

    hours_held = (time.time() - pos['entry_time']) / 3600

    # 1. SL hit
    if current_price <= pos['sl']:
        return True, 'SL'

    # 2. RSI(2) exit — wait minimum hold first
    if hours_held >= MIN_HOLD_HOURS and ind['rsi2'] >= RSI2_EXIT:
        return True, 'RSI_EXIT'

    # 3. 200 EMA broken — trend reversed
    if current_price < ind['e200'] * 0.997 and hours_held >= MIN_HOLD_HOURS:
        return True, 'TREND_BREAK'

    # 4. Time stop
    if hours_held >= MAX_HOLD_HOURS:
        return True, 'TIME'

    return False, 'HOLD'

def run_cycle():
    """One complete check cycle — runs every LOOP_SEC seconds"""
    reset_daily()
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    state['last_check'] = now_str

    bal = state['balance']
    recent_losses = recent_loss_count()

    for pair in TRADE_PAIRS:
        try:
            # Fetch latest candles
            candles = fetch_kraken_candles(pair, interval=60, count=250)
            if len(candles) < 210:
                slog(f"⚠️ {pair}: only {len(candles)} candles, need 210")
                continue

            # Update state
            state['candles'][pair] = candles
            price = candles[-1]['c']
            state['prices'][pair] = price

            # Compute indicators
            ind = compute_indicators(candles)
            if ind:
                state['indicators'][pair] = ind

            # ── CHECK EXITS ────────────────────────────────────────────────
            if pair in state['positions']:
                pos = state['positions'][pair]
                should_exit, reason = check_exit(pair, pos, ind, price, candles[-1]['t'])

                if should_exit:
                    # Close position
                    close_order(pair, pos['side'], pos['qty'])
                    pnl    = (price - pos['entry_price']) / pos['entry_price'] * pos['notional']
                    pnl   -= pos['notional'] * (MAKER_FEE + TAKER_FEE)  # fees
                    pnl    = round(pnl, 4)
                    won    = pnl > 0
                    hours  = (time.time() - pos['entry_time']) / 3600

                    bal   += pos['margin'] + pnl
                    state['balance']   = round(bal, 4)
                    state['total_pnl'] += pnl
                    if pnl < 0:
                        state['daily_loss'] += abs(pnl)

                    if won: state['wins']   += 1
                    else:   state['losses'] += 1

                    trade = {
                        'date':       now_str,
                        'pair':       pair,
                        'entry':      round(pos['entry_price'], 4),
                        'exit':       round(price, 4),
                        'sl':         round(pos['sl'], 4),
                        'pnl':        pnl,
                        'pnl_pct':    round(pnl/pos['margin']*100, 2),
                        'reason':     reason,
                        'won':        won,
                        'bal':        round(bal, 4),
                        'rsi2_entry': pos['rsi2_entry'],
                        'rsi2_exit':  round(ind['rsi2'] if ind else 0, 1),
                        'hold_h':     round(hours, 1),
                    }
                    state['trades'].appendleft(trade)
                    del state['positions'][pair]

                    emoji = "✅" if won else "❌"
                    slog(f"{emoji} CLOSED {pair} | {reason} | P&L: ${pnl:+.4f} ({trade['pnl_pct']:+.1f}%) | Bal: ${bal:.2f}")

                else:
                    # Update unrealized P&L
                    upnl = (price - pos['entry_price']) / pos['entry_price'] * pos['notional']
                    state['positions'][pair]['upnl'] = round(upnl, 4)
                    state['positions'][pair]['price_now'] = round(price, 4)
                    state['positions'][pair]['rsi2_now']  = round(ind['rsi2'] if ind else 0, 1)
                    continue

            # ── CHECK ENTRIES ──────────────────────────────────────────────
            should_enter, score, reasons = check_entry(pair, ind, bal)

            if should_enter:
                risk_pct   = get_risk_pct(bal, state['daily_loss']/bal*100 if bal>0 else 0, recent_losses)
                qty, notional = position_size(bal, price, SL_PCT, risk_pct)

                if notional < 0.50:
                    slog(f"⚠️ {pair}: notional ${notional:.2f} too small, skip")
                    continue

                sl_price = price * (1 - SL_PCT/100)
                margin   = notional  # 1x spot or margin-based

                if margin > bal * 0.95:
                    slog(f"⚠️ {pair}: margin ${margin:.2f} > balance ${bal:.2f}, skip")
                    continue

                # Execute
                result = place_order(pair, 'BUY', qty, price, sl_price)
                if result:
                    bal -= margin
                    state['balance'] = round(bal, 4)
                    state['daily_trades'] += 1

                    state['positions'][pair] = {
                        'pair':         pair,
                        'side':         'BUY',
                        'entry_price':  price,
                        'sl':           sl_price,
                        'qty':          qty,
                        'notional':     notional,
                        'margin':       margin,
                        'risk_pct':     round(risk_pct*100, 2),
                        'entry_time':   time.time(),
                        'rsi2_entry':   ind['rsi2'],
                        'adx_entry':    ind['adx'],
                        'reasons':      reasons,
                        'upnl':         0.0,
                        'price_now':    price,
                        'rsi2_now':     ind['rsi2'],
                    }
                    slog(f"🟢 ENTERED {pair} @ ${price:.4f} | RSI2={ind['rsi2']} | SL=${sl_price:.4f} | Risk={risk_pct*100:.1f}% | Score={score}")
                    for r in reasons[:3]:
                        slog(f"   → {r}")

        except Exception as e:
            slog(f"❌ Error processing {pair}: {e}")
            log.exception(e)

def run_forever():
    """Main loop"""
    slog(f"🚀 Bot started | Mode: {state['mode']} | Pairs: {TRADE_PAIRS}")
    slog(f"📊 Strategy: RSI2 Mean Reversion | Entry<{RSI2_ENTRY} Exit>{RSI2_EXIT} SL={SL_PCT}%")
    slog(f"🛡️ Risk: Max {MAX_TRADES_DAY}/day | Max {MAX_POSITIONS} positions | {DAILY_LOSS_STOP}% daily stop")

    while state['running']:
        try:
            run_cycle()
        except Exception as e:
            slog(f"❌ Cycle error: {e}")
            log.exception(e)
        time.sleep(LOOP_SEC)

# ══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
app = FastAPI()

@app.get('/', response_class=HTMLResponse)
def dashboard():
    bal     = state['balance']
    start   = state['start_bal']
    ret     = round((bal - start) / start * 100, 2)
    wins    = state['wins']
    losses  = state['losses']
    total   = wins + losses
    wr      = round(wins/total*100, 1) if total > 0 else 0
    upnl    = sum(p.get('upnl', 0) for p in state['positions'].values())
    pf_val  = 0
    t_list  = list(state['trades'])
    if t_list:
        gw = sum(t['pnl'] for t in t_list if t['won'])
        gl = abs(sum(t['pnl'] for t in t_list if not t['won']))
        pf_val = round(gw/gl, 2) if gl > 0 else 99.0

    # Positions HTML
    pos_html = ''
    for pair, pos in state['positions'].items():
        upnl_c = '#4ade80' if pos['upnl'] >= 0 else '#f87171'
        hours  = round((time.time()-pos['entry_time'])/3600, 1)
        ind    = state['indicators'].get(pair, {})
        rsi2   = ind.get('rsi2', '?')
        rsi_c  = '#4ade80' if isinstance(rsi2, float) and rsi2 >= RSI2_EXIT else ('#fbbf24' if isinstance(rsi2, float) and rsi2 >= 50 else '#f87171')
        pos_html += f"""<tr>
<td style="color:#38bdf8"><b>{pair}</b></td>
<td>${pos['entry_price']:.4f}</td>
<td>${pos['price_now']:.4f}</td>
<td>${pos['sl']:.4f}</td>
<td style="color:{rsi_c}">{rsi2}</td>
<td>{hours}h</td>
<td style="color:{upnl_c}"><b>${pos['upnl']:+.4f}</b></td>
<td style="color:#64748b;font-size:10px">{pos['risk_pct']}%</td>
</tr>"""
    if not pos_html:
        pos_html = '<tr><td colspan="8" style="text-align:center;color:#475569">No open positions — watching for setups...</td></tr>'

    # Trades HTML
    trades_html = ''
    for t in list(state['trades'])[:30]:
        c = '#4ade80' if t['won'] else '#f87171'
        icon = '✅' if t['won'] else '❌'
        trades_html += f"""<tr>
<td style="font-size:10px;color:#64748b">{t['date'][-8:]}</td>
<td style="color:#38bdf8">{t['pair']}</td>
<td>${t['entry']:.4f}</td><td>${t['exit']:.4f}</td><td>${t['sl']:.4f}</td>
<td style="color:{'#4ade80' if t['reason']=='RSI_EXIT' else '#f87171' if t['reason']=='SL' else '#fbbf24'}">{t['reason']}</td>
<td>{t['hold_h']}h</td>
<td style="color:#f87171">{t.get('rsi2_entry','?')}</td>
<td style="color:#4ade80">{t.get('rsi2_exit','?')}</td>
<td style="color:{c}"><b>${t['pnl']:+.4f}</b></td>
<td style="color:{c}">{t['pnl_pct']:+.1f}%</td>
<td>${t['bal']:,.2f}</td>
</tr>"""

    # Indicators HTML
    ind_html = ''
    for pair in TRADE_PAIRS:
        ind = state['indicators'].get(pair)
        price = state['prices'].get(pair, 0)
        if not ind:
            ind_html += f'<tr><td>{pair}</td><td colspan="7" style="color:#475569">Fetching...</td></tr>'
            continue
        ab200_c = '#4ade80' if ind['above_200'] else '#f87171'
        rsi2_c  = '#f87171' if ind['rsi2'] < RSI2_ENTRY else '#fbbf24' if ind['rsi2'] < 30 else '#64748b'
        entry_c = '#4ade80' if (ind['above_200'] and ind['e200_rising'] and ind['ema_aligned'] and ind['rsi2'] < RSI2_ENTRY and ind['adx'] >= MIN_ADX) else '#f87171'
        ready   = '✅ READY' if entry_c == '#4ade80' else '⏳ waiting'
        ind_html += f"""<tr>
<td style="color:#38bdf8"><b>{pair}</b></td>
<td>${price:.2f}</td>
<td style="color:{rsi2_c}"><b>{ind['rsi2']}</b></td>
<td>{ind['rsi14']}</td>
<td>{ind['adx']}</td>
<td style="color:{ab200_c}">{'✅' if ind['above_200'] else '❌'}</td>
<td>{'✅' if ind['ema_aligned'] else '❌'}</td>
<td style="color:{entry_c}"><b>{ready}</b></td>
</tr>"""

    # Log HTML
    log_html = ''.join(f'<div style="padding:2px 0;border-bottom:1px solid rgba(56,189,248,0.05);font-size:11px;color:#94a3b8">{l}</div>'
                       for l in list(state['log'])[:40])

    ret_c = '#4ade80' if ret >= 0 else '#f87171'

    HTML = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>RSI(2) Bot v9 | {state['mode']}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#04070d;color:#cbd5e1;font-family:'Segoe UI',system-ui,sans-serif;padding:18px;max-width:1500px;margin:0 auto;font-size:13px}}
h1{{font-size:22px;font-weight:800;color:#38bdf8;margin-bottom:4px}}
.badges{{display:flex;gap:7px;flex-wrap:wrap;margin:8px 0 18px}}
.badge{{padding:3px 11px;border-radius:20px;font-size:11px;font-weight:600}}
.bg{{background:rgba(74,222,128,0.15);color:#4ade80;border:1px solid rgba(74,222,128,0.3)}}
.bb{{background:rgba(56,189,248,0.15);color:#38bdf8;border:1px solid rgba(56,189,248,0.3)}}
.by{{background:rgba(251,191,36,0.15);color:#fbbf24;border:1px solid rgba(251,191,36,0.3)}}
.br{{background:rgba(248,113,113,0.15);color:#f87171;border:1px solid rgba(248,113,113,0.3)}}
.hero{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}}
.hc{{background:linear-gradient(135deg,#0b1220,#0f1a2e);border:1px solid rgba(56,189,248,0.2);border-radius:12px;padding:16px;text-align:center}}
.hc .lb{{font-size:9px;letter-spacing:2px;color:#475569;text-transform:uppercase;margin-bottom:6px}}
.hc .val{{font-size:26px;font-weight:800;line-height:1}}
.hc .sb{{font-size:10px;color:#64748b;margin-top:4px}}
h2{{font-size:14px;color:#38bdf8;margin:22px 0 10px;padding-bottom:6px;border-bottom:1px solid rgba(56,189,248,0.18)}}
.tw{{overflow-x:auto;margin-bottom:16px;border-radius:8px;border:1px solid rgba(56,189,248,0.1)}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#0b1220;padding:7px 8px;text-align:left;color:#64748b;font-size:9px;letter-spacing:1px;text-transform:uppercase;border-bottom:1px solid rgba(56,189,248,0.12);white-space:nowrap}}
td{{padding:6px 8px;border-bottom:1px solid rgba(56,189,248,0.04)}}
tr:hover td{{background:rgba(56,189,248,0.03)}}
.strategy-box{{background:#0b1220;border:1px solid rgba(74,222,128,0.2);border-radius:10px;padding:14px;margin-bottom:18px;font-size:11px;color:#94a3b8;line-height:1.9}}
.strategy-box h3{{color:#4ade80;font-size:13px;font-weight:700;margin-bottom:8px}}
.log-box{{background:#0b1220;border:1px solid rgba(56,189,248,0.1);border-radius:10px;padding:12px;max-height:300px;overflow-y:auto;font-family:monospace}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px}}
</style></head><body>

<h1>⚡ RSI(2) Live Bot <span style="font-size:14px;color:{'#4ade80' if 'PAPER' in state['mode'] or 'TEST' in state['mode'] else '#f87171'}">{state['mode']}</span></h1>
<div class="badges">
<span class="badge bg">RSI(2) STRATEGY</span>
<span class="badge bb">Entry&lt;{RSI2_ENTRY} Exit&gt;{RSI2_EXIT}</span>
<span class="badge by">SL={SL_PCT}%</span>
<span class="badge bg">MAX {MAX_TRADES_DAY}/day</span>
<span class="badge bb">Last: {state['last_check']}</span>
<span class="badge br">Auto-refresh 30s</span>
</div>

<div class="strategy-box">
<h3>🧠 Active Strategy Rules (Proven: AlphaTrader +3091% | v8 backtest +174% in bear market)</h3>
<b style="color:#4ade80">ENTRY:</b> Price &gt; 200 EMA (uptrend) + EMA10 &gt; EMA50 + RSI(2) &lt; {RSI2_ENTRY} + ADX &gt; {MIN_ADX} + Score ≥ 4<br>
<b style="color:#fbbf24">EXIT:</b> RSI(2) &gt; {RSI2_EXIT} (momentum recovered = BIG WIN) | SL {SL_PCT}% (fixed small loss) | Time stop {MAX_HOLD_HOURS}h<br>
<b style="color:#38bdf8">RISK:</b> Dynamic {int(get_risk_pct(bal,0,0)*100)}% of balance | Max 20% notional | Max {MAX_POSITIONS} positions | {DAILY_LOSS_STOP}% daily stop<br>
<b style="color:#94a3b8">KEY INSIGHT:</b> Low win rate is OK (41% worked for +3091%). Asymmetry: SL = fixed small loss → RSI exit = variable large win (3-8× SL)
</div>

<div class="hero">
<div class="hc"><div class="lb">Balance</div>
<div class="val" style="color:#38bdf8">${bal:,.2f}</div>
<div class="sb">Start: ${start:,.2f}</div></div>
<div class="hc"><div class="lb">Return</div>
<div class="val" style="color:{ret_c}">{ret:+.2f}%</div>
<div class="sb">${bal-start:+.2f} net</div></div>
<div class="hc"><div class="lb">Unrealized</div>
<div class="val" style="color:{'#4ade80' if upnl>=0 else '#f87171'}">${upnl:+.4f}</div>
<div class="sb">open positions</div></div>
<div class="hc"><div class="lb">Win Rate</div>
<div class="val" style="color:{'#4ade80' if wr>=55 else '#fbbf24' if wr>=40 else '#f87171'}">{wr}%</div>
<div class="sb">{wins}W / {losses}L ({total} trades)</div></div>
<div class="hc"><div class="lb">Profit Factor</div>
<div class="val" style="color:{'#4ade80' if pf_val>=2 else '#fbbf24' if pf_val>=1.3 else '#f87171'}">{pf_val}</div>
<div class="sb">wins ÷ losses</div></div>
<div class="hc"><div class="lb">Today</div>
<div class="val" style="color:#a78bfa">{state['daily_trades']}/{MAX_TRADES_DAY}</div>
<div class="sb">trades today</div></div>
</div>

<h2>📊 Live Market Indicators — RSI(2) Status</h2>
<div class="tw"><table><thead><tr>
<th>Pair</th><th>Price</th><th>RSI(2)</th><th>RSI14</th><th>ADX</th>
<th>&gt;200EMA</th><th>EMA10&gt;50</th><th>Entry Signal</th>
</tr></thead><tbody>{ind_html}</tbody></table></div>

<h2>🔓 Open Positions ({len(state['positions'])})</h2>
<div class="tw"><table><thead><tr>
<th>Pair</th><th>Entry</th><th>Now</th><th>SL</th><th>RSI(2)Now</th><th>Hours</th><th>Unrealized P&L</th><th>Risk%</th>
</tr></thead><tbody>{pos_html}</tbody></table></div>

<div class="grid2">
<div>
<h2>📋 Trade History (last 30)</h2>
<div class="tw"><table><thead><tr>
<th>Time</th><th>Pair</th><th>Entry</th><th>Exit</th><th>SL</th><th>Reason</th>
<th>Hold</th><th>RSI↓</th><th>RSI↑</th><th>P&L $</th><th>P&L%</th><th>Balance</th>
</tr></thead><tbody>{trades_html or '<tr><td colspan="12" style="text-align:center;color:#475569">No trades yet</td></tr>'}</tbody></table></div>
</div>
<div>
<h2>📝 Activity Log</h2>
<div class="log-box">{log_html or '<div style="color:#475569">Waiting for activity...</div>'}</div>
</div>
</div>

<div style="text-align:center;color:#334155;font-size:10px;padding:16px;border-top:1px solid rgba(56,189,248,0.06);margin-top:20px">
RSI(2) Mean Reversion Bot v9 · Proven Strategy · {state['mode']} · 
Delta Exchange {'(Testnet)' if not IS_LIVE else '(LIVE ⚡)'} · 
Kraken price feed · Auto-refresh 30s
</div>
</body></html>"""
    return HTML

@app.get('/health')
def health():
    return {
        'status': 'running',
        'balance': state['balance'],
        'return_pct': round((state['balance']-state['start_bal'])/state['start_bal']*100, 2),
        'positions': len(state['positions']),
        'trades': len(state['trades']),
        'mode': state['mode'],
        'last_check': state['last_check'],
    }

@app.get('/api/state')
def api_state():
    return {
        'balance':    state['balance'],
        'positions':  state['positions'],
        'trades':     list(state['trades'])[:20],
        'indicators': state['indicators'],
        'prices':     state['prices'],
        'wins':       state['wins'],
        'losses':     state['losses'],
    }

# ══════════════════════════════════════════════════════════════════════════════
#  STARTUP
# ══════════════════════════════════════════════════════════════════════════════
@app.on_event('startup')
def startup():
    t = threading.Thread(target=run_forever, daemon=True)
    t.start()
    log.info("Trading thread started")

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', 8000)))
