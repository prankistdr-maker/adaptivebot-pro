"""
AdaptiveBot PRO v4 - Main Application
Multi-pair: BTC, ETH, SOL
News-aware strategy
$20 starting balance
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
import asyncio, os, time, json
from datetime import datetime

from app.state import state, lock, PAIRS, STARTING_BALANCE
from app.feed import stream
from app.indicators import compute
from app.smc import analyze_smc
from app.engine import decide
from app.execution import execute, manage_positions, daily_reset
from app.news import refresh_news, get_news_score

app = FastAPI()
BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def serve_ui():
    path = os.path.join(FRONTEND_DIR, "index.html")
    return FileResponse(path) if os.path.exists(path) else HTMLResponse("<h1>AdaptiveBot v4 Running</h1>")

@app.head("/")
def head_root(): return {}
@app.head("/data")
def head_data(): return {}


@app.on_event("startup")
async def start():
    asyncio.create_task(stream())
    asyncio.create_task(refresh_news())
    asyncio.create_task(main_loop())
    print("AdaptiveBot PRO v4 started — BTC/ETH/SOL + News")


async def main_loop():
    # Wait for prices
    for _ in range(30):
        with lock:
            prices = state["prices"]
        if any(v > 0 for v in prices.values()):
            break
        await asyncio.sleep(1)

    print(f"Prices loaded: {dict(state['prices'])}")

    while True:
        try:
            daily_reset()
            manage_positions()

            with lock:
                balance    = state["balance"]
                daily_loss = state["daily_loss"]
                positions  = state["positions"][:]

            live_pnl = 0
            for pos in positions:
                pair  = pos["pair"]
                with lock:
                    price = state["prices"][pair]
                if price > 0:
                    p = (price-pos["entry"])/pos["entry"]*pos["amount"] if pos["action"]=="BUY" \
                        else (pos["entry"]-price)/pos["entry"]*pos["amount"]
                    live_pnl += p

            with lock:
                state["pnl"] = round(live_pnl, 4)

            # Analyze each pair
            for pair in PAIRS:
                with lock:
                    price   = state["prices"][pair]
                    candles = state["candles"][pair][:]
                    last_tt = state["last_trade_time"][pair]

                if price <= 0 or len(candles) < 30:
                    continue

                ind = compute(candles)
                smc = analyze_smc(candles)

                # News sentiment adjustment
                news = get_news_score(pair)
                # News modifies score in engine via smc bias override
                if news["bias"] == "bullish" and smc.get("bias") == "neutral":
                    smc["bias"] = "buy"
                elif news["bias"] == "bearish" and smc.get("bias") == "neutral":
                    smc["bias"] = "sell"
                # Extreme fear = strong contrarian buy signal
                if news["fg_index"] < 20:
                    smc["liquidity_bull"] = True
                # Extreme greed = strong contrarian sell signal
                if news["fg_index"] > 82:
                    smc["liquidity_bear"] = True

                direction, confidence, mode, reasons, sl_pct, tp_pct = decide(
                    ind, smc, balance, positions, daily_loss,
                    state["daily_trades"], last_tt
                )

                # Add news context to reasons
                if news["signals"]:
                    reasons.append(f"News: {news['signals'][0]}")

                with lock:
                    state["ind"][pair]      = ind
                    state["smc"][pair]      = smc
                    state["decision"][pair] = direction
                    state["mode"][pair]     = mode
                    state["conf"][pair]     = confidence
                    state["reasons"][pair]  = reasons

                if direction in ["BUY","SELL"] and confidence >= 6:
                    pair_pos = [p for p in positions if p["pair"]==pair]
                    if not pair_pos:
                        execute(pair, direction, sl_pct, tp_pct,
                                confidence, reasons, mode, ind)

        except Exception as e:
            print(f"[MAIN] Error: {e}")
            import traceback; traceback.print_exc()

        await asyncio.sleep(3)


@app.get("/data")
def get_data():
    with lock:
        price_data = {}
        for pair in PAIRS:
            p = state["prices"][pair]
            ind = state["ind"].get(pair, {})
            price_data[pair] = {
                "price":    round(p, 4),
                "rsi":      ind.get("rsi", 0),
                "trend":    ind.get("trend", "RANGING"),
                "macd_hist":ind.get("macd_hist", 0),
                "atr_pct":  ind.get("atr_pct", 0),
                "mode":     state["mode"][pair],
                "decision": state["decision"][pair],
                "conf":     state["conf"][pair],
                "reasons":  state["reasons"][pair],
                "ema_bull": ind.get("ema_bull", False),
                "above_vwap": ind.get("above_vwap", False),
                "volatility": ind.get("volatility", "low"),
            }

        positions = []
        for pos in state["positions"]:
            pair  = pos["pair"]
            price = state["prices"].get(pair, 0)
            positions.append({
                "id": pos["id"], "pair": pair,
                "action": pos["action"], "entry": pos["entry"],
                "current": price, "pnl": round(pos.get("pnl",0), 4),
                "sl": pos["sl"], "tp": pos["tp"],
                "sl_pct": pos["sl_pct"], "tp_pct": pos["tp_pct"],
                "mode": pos.get("mode",""), "confidence": pos.get("confidence",0),
                "time": pos.get("time_str",""), "reasons": pos.get("reasons",[]),
                "be_set": pos.get("be_set", False),
                "trail_on": pos.get("trail_on", False),
            })

        # News for dashboard
        from app.news import _cache as news_cache
        news_display = {
            "fg_index":  news_cache.get("fg_index", 50),
            "fg_label":  news_cache.get("fg_label", "neutral"),
            "events":    news_cache.get("events", [])[:4],
            "trending":  news_cache.get("trending", [])[:5],
        }

        total = state["total_trades"]
        wins  = state["winning_trades"]

        return {
            "balance":       round(state["balance"], 4),
            "initial":       STARTING_BALANCE,
            "total_pnl":     round(state["total_pnl"], 4),
            "pnl":           round(state["pnl"], 4),
            "win_rate":      round(wins/total*100,1) if total>0 else 0,
            "total_trades":  total,
            "winning":       wins,
            "losing":        state["losing_trades"],
            "max_drawdown":  state["max_drawdown"],
            "daily_trades":  state["daily_trades"],
            "daily_loss":    round(state["daily_loss"],4),
            "feed_status":   state["feed_status"],
            "pairs":         price_data,
            "positions":     positions,
            "trades":        state["trades"][:30],
            "equity_curve":  state["equity_curve"],
            "news":          news_display,
        }
