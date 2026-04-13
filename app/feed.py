"""
Multi-pair price feed using Kraken REST API
Fetches BTC, ETH, SOL simultaneously
Works from Render Oregon - confirmed working
"""
import asyncio, aiohttp, time
from app.state import state, lock, PAIRS

async def fetch_price(session, pair_key):
    kraken_pair = PAIRS[pair_key]["kraken"]
    try:
        url = f"https://api.kraken.com/0/public/Ticker?pair={kraken_pair}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
            data = await r.json()
            result = data.get("result", {})
            if result:
                key = list(result.keys())[0]
                return float(result[key]["c"][0])
    except:
        pass
    return 0.0

async def fetch_candles(session, pair_key, interval=5, limit=200):
    kraken_pair = PAIRS[pair_key]["kraken"]
    try:
        url = f"https://api.kraken.com/0/public/OHLC?pair={kraken_pair}&interval={interval}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json()
            result = data.get("result", {})
            if not result:
                return []
            key = [k for k in result.keys() if k != "last"][0]
            raw = result[key][-limit:]
            return [{"time":int(c[0]),"open":float(c[1]),"high":float(c[2]),
                     "low":float(c[3]),"close":float(c[4]),"volume":float(c[6])} for c in raw]
    except:
        return []

async def stream():
    candle_timer = 0
    connector = aiohttp.TCPConnector(limit=20)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Initial candle load for all pairs
        for pair_key in PAIRS:
            candles = await fetch_candles(session, pair_key)
            if candles:
                with lock:
                    state["candles"][pair_key] = candles
        with lock:
            state["feed_status"] = "connected"

        while True:
            try:
                # Fetch all prices concurrently
                tasks = [fetch_price(session, p) for p in PAIRS]
                prices = await asyncio.gather(*tasks)

                with lock:
                    for i, pair_key in enumerate(PAIRS):
                        if prices[i] > 0:
                            state["prices"][pair_key] = prices[i]
                            # Update last candle close with live price
                            if state["candles"][pair_key]:
                                state["candles"][pair_key][-1]["close"] = prices[i]
                                state["candles"][pair_key][-1]["high"] = max(
                                    state["candles"][pair_key][-1]["high"], prices[i])
                                state["candles"][pair_key][-1]["low"] = min(
                                    state["candles"][pair_key][-1]["low"], prices[i])
                    state["feed_status"] = "connected"

                # Refresh candles every 90 seconds
                candle_timer += 3
                if candle_timer >= 90:
                    for pair_key in PAIRS:
                        c = await fetch_candles(session, pair_key)
                        if c:
                            with lock:
                                state["candles"][pair_key] = c
                    candle_timer = 0

            except Exception as e:
                with lock:
                    state["feed_status"] = f"error"

            await asyncio.sleep(3)
