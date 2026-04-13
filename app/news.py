"""
News & Event Sentiment Analysis
Fetches crypto news from free APIs and scores market sentiment
Key insight: News moves markets BEFORE technicals catch up
Sources: CoinGecko trending, Fear & Greed Index, CryptoPanic
"""
import aiohttp, asyncio, time

_cache = {"sentiment": 0, "label": "neutral", "events": [], "fg_index": 50, "ts": 0}
CACHE_TTL = 300  # refresh every 5 minutes


async def fetch_fear_greed(session):
    """Fear & Greed Index: 0=extreme fear, 100=extreme greed
    Research: Extreme fear = buy signal, Extreme greed = sell signal"""
    try:
        async with session.get("https://api.alternative.me/fng/?limit=1",
                               timeout=aiohttp.ClientTimeout(total=6)) as r:
            d = await r.json()
            val = int(d["data"][0]["value"])
            label = d["data"][0]["value_classification"]
            return val, label
    except:
        return 50, "neutral"


async def fetch_coingecko_trending(session):
    """Trending coins — if BTC/ETH/SOL trending = strong momentum"""
    try:
        async with session.get("https://api.coingecko.com/api/v3/search/trending",
                               timeout=aiohttp.ClientTimeout(total=6)) as r:
            d = await r.json()
            coins = [c["item"]["name"].upper() for c in d.get("coins", [])[:7]]
            return coins
    except:
        return []


async def fetch_cryptopanic(session):
    """Free CryptoPanic news headlines — no API key needed for public feed"""
    try:
        url = "https://cryptopanic.com/api/free/v1/posts/?auth_token=free&public=true&currencies=BTC,ETH,SOL&filter=hot"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            d = await r.json()
            posts = d.get("results", [])[:8]
            events = []
            for p in posts:
                title = p.get("title", "").lower()
                votes = p.get("votes", {})
                bull  = votes.get("positive", 0)
                bear  = votes.get("negative", 0)
                sentiment = "bullish" if bull > bear else ("bearish" if bear > bull else "neutral")
                events.append({
                    "title":     p.get("title", "")[:80],
                    "sentiment": sentiment,
                    "score":     bull - bear,
                    "source":    p.get("source", {}).get("title", ""),
                })
            return events
    except:
        return []


def score_news(fear_greed, trending, events, pair):
    """
    Convert all news data into a single sentiment score (-10 to +10)
    and actionable bias.

    Research insights:
    - Extreme fear (< 20) = institutional buying opportunity (contrarian)
    - Extreme greed (> 80) = distribution phase (sell signal)
    - Trending = momentum confirmation
    - Negative news on a downtrend = accelerates, but on uptrend = buy dip
    """
    score = 0
    signals = []
    pair_coin = pair.replace("USDT", "")

    # Fear & Greed
    if fear_greed < 20:
        score += 3
        signals.append(f"Extreme Fear {fear_greed} → contrarian BUY")
    elif fear_greed < 35:
        score += 1
        signals.append(f"Fear {fear_greed} → cautious buy bias")
    elif fear_greed > 80:
        score -= 3
        signals.append(f"Extreme Greed {fear_greed} → distribution SELL")
    elif fear_greed > 65:
        score -= 1
        signals.append(f"Greed {fear_greed} → caution")
    else:
        signals.append(f"Neutral sentiment {fear_greed}")

    # Trending
    coins_map = {"BTC": "BITCOIN", "ETH": "ETHEREUM", "SOL": "SOLANA"}
    coin_name = coins_map.get(pair_coin, pair_coin)
    if any(coin_name in t or pair_coin in t for t in trending):
        score += 2
        signals.append(f"{pair_coin} trending on CoinGecko")

    # News headlines
    bull_news = sum(1 for e in events if e["sentiment"] == "bullish")
    bear_news = sum(1 for e in events if e["sentiment"] == "bearish")
    net_news  = bull_news - bear_news

    if net_news >= 3:
        score += 2
        signals.append(f"Strong bull news ({bull_news} bullish)")
    elif net_news >= 1:
        score += 1
        signals.append(f"Slight bull news bias")
    elif net_news <= -3:
        score -= 2
        signals.append(f"Strong bear news ({bear_news} bearish)")
    elif net_news <= -1:
        score -= 1
        signals.append(f"Slight bear news bias")

    # Major event keywords (market-moving words)
    all_titles = " ".join(e["title"].lower() for e in events)
    positive_keywords = ["etf", "approval", "adoption", "partnership", "upgrade",
                         "halving", "institutional", "buy", "bullish", "rally",
                         "record", "ath", "accumulation"]
    negative_keywords = ["hack", "ban", "crash", "regulation", "sec", "lawsuit",
                         "scam", "bear", "sell", "dump", "liquidation", "crisis"]

    pos_hits = sum(1 for k in positive_keywords if k in all_titles)
    neg_hits  = sum(1 for k in negative_keywords if k in all_titles)

    if pos_hits >= 2:
        score += 1; signals.append(f"Positive keywords: {pos_hits}")
    if neg_hits >= 2:
        score -= 1; signals.append(f"Risk keywords: {neg_hits}")

    # Clamp
    score = max(-10, min(10, score))

    if score >= 3:    bias = "bullish"
    elif score <= -3: bias = "bearish"
    else:             bias = "neutral"

    return score, bias, signals


async def refresh_news():
    """Refresh news cache every 5 minutes"""
    global _cache
    connector = aiohttp.TCPConnector(limit=5)
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            try:
                fg, fg_label = await fetch_fear_greed(session)
                trending     = await fetch_coingecko_trending(session)
                events       = await fetch_cryptopanic(session)

                _cache = {
                    "fg_index":  fg,
                    "fg_label":  fg_label,
                    "trending":  trending,
                    "events":    events,
                    "ts":        time.time(),
                }
                print(f"[NEWS] F&G:{fg}({fg_label}) Trending:{trending[:3]} Events:{len(events)}")
            except Exception as e:
                print(f"[NEWS] Error: {e}")

            await asyncio.sleep(300)  # refresh every 5 min


def get_news_score(pair):
    """Get cached news sentiment for a pair"""
    fg      = _cache.get("fg_index", 50)
    trending = _cache.get("trending", [])
    events  = _cache.get("events", [])
    score, bias, signals = score_news(fg, trending, events, pair)
    return {
        "score":    score,
        "bias":     bias,
        "signals":  signals[:3],
        "fg_index": fg,
        "fg_label": _cache.get("fg_label", "neutral"),
        "events":   events[:4],
    }
