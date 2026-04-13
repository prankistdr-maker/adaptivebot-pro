"""
Real indicator calculations from candle data
All computed from scratch - no external library needed
"""

def ema(values, period):
    if len(values) < period:
        return []
    k = 2/(period+1)
    r = [sum(values[:period])/period]
    for v in values[period:]:
        r.append(v*k + r[-1]*(1-k))
    return r

def compute(candles):
    if not candles or len(candles) < 30:
        return {}
    closes  = [c["close"]  for c in candles]
    highs   = [c["high"]   for c in candles]
    lows    = [c["low"]    for c in candles]
    volumes = [c["volume"] for c in candles]
    ind = {}
    ind["price"]      = closes[-1]
    ind["prev_close"] = closes[-2]
    ind["change_pct"] = round((closes[-1]-closes[-2])/closes[-2]*100, 3)

    # RSI
    gains,losses = [],[]
    for i in range(1,len(closes)):
        d = closes[i]-closes[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    period=14
    ag=sum(gains[-period:])/period; al=sum(losses[-period:])/period
    ind["rsi"] = round(100-(100/(1+ag/al)),2) if al>0 else 100
    ind["rsi_signal"] = "oversold" if ind["rsi"]<30 else ("overbought" if ind["rsi"]>70 else "neutral")

    # EMAs
    for p in [9,21,50,200]:
        if len(closes)>=p:
            e=ema(closes,p); ind[f"ema{p}"]=round(e[-1],4) if e else closes[-1]
        else:
            ind[f"ema{p}"]=closes[-1]
    ind["ema_bull"]        = ind["ema9"]>ind["ema21"]
    ind["ema_strong_bull"] = ind["ema9"]>ind["ema21"]>ind["ema50"]
    ind["above_ema200"]    = closes[-1]>ind["ema200"]

    # EMA cross detection
    e9_all=ema(closes,9); e21_all=ema(closes,21)
    if len(e9_all)>=2 and len(e21_all)>=2:
        ind["ema_cross_bull"] = e9_all[-1]>e21_all[-1] and e9_all[-2]<=e21_all[-2]
        ind["ema_cross_bear"] = e9_all[-1]<e21_all[-1] and e9_all[-2]>=e21_all[-2]
    else:
        ind["ema_cross_bull"]=ind["ema_cross_bear"]=False

    # MACD
    e12=ema(closes,12); e26=ema(closes,26)
    if e12 and e26:
        diff=len(e12)-len(e26)
        ml=[e12[i+diff]-e26[i] for i in range(len(e26))]
        sig=ema(ml,9)
        if sig:
            ind["macd"]=round(ml[-1],4); ind["macd_signal"]=round(sig[-1],4)
            ind["macd_hist"]=round(ml[-1]-sig[-1],4); ind["macd_bull"]=ind["macd_hist"]>0
            ind["macd_cross_bull"]=ml[-1]>sig[-1] and (len(ml)<2 or ml[-2]<=sig[-2]) if len(sig)>=2 else False
            ind["macd_cross_bear"]=ml[-1]<sig[-1] and (len(ml)<2 or ml[-2]>=sig[-2]) if len(sig)>=2 else False
        else:
            ind["macd"]=ind["macd_signal"]=ind["macd_hist"]=0
            ind["macd_bull"]=ind["macd_cross_bull"]=ind["macd_cross_bear"]=False
    else:
        ind["macd"]=ind["macd_signal"]=ind["macd_hist"]=0
        ind["macd_bull"]=ind["macd_cross_bull"]=ind["macd_cross_bear"]=False

    # ATR
    trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,len(candles))]
    ind["atr"]     = round(sum(trs[-14:])/14,4) if len(trs)>=14 else 0
    ind["atr_pct"] = round(ind["atr"]/closes[-1]*100,3) if closes[-1] else 0
    ind["volatility"]="high" if ind["atr_pct"]>0.5 else("medium" if ind["atr_pct"]>0.2 else "low")

    # VWAP
    typical=[(highs[i]+lows[i]+closes[i])/3 for i in range(len(candles))]
    vol_sum=sum(volumes)
    ind["vwap"]=round(sum(t*v for t,v in zip(typical,volumes))/vol_sum,4) if vol_sum>0 else closes[-1]
    ind["above_vwap"]=closes[-1]>ind["vwap"]

    # Bollinger Bands
    if len(closes)>=20:
        bb=closes[-20:]; m=sum(bb)/20
        std=(sum((c-m)**2 for c in bb)/20)**0.5
        ind["bb_upper"]=round(m+2*std,4); ind["bb_lower"]=round(m-2*std,4); ind["bb_mid"]=round(m,4)
        rng=ind["bb_upper"]-ind["bb_lower"]
        ind["bb_pct"]=round((closes[-1]-ind["bb_lower"])/rng,3) if rng>0 else 0.5
        ind["bb_squeeze"]=rng/m<0.015
        ind["near_bb_upper"]=closes[-1]>ind["bb_upper"]*0.998
        ind["near_bb_lower"]=closes[-1]<ind["bb_lower"]*1.002
    else:
        ind["bb_upper"]=ind["bb_lower"]=ind["bb_mid"]=closes[-1]
        ind["bb_pct"]=0.5; ind["bb_squeeze"]=False
        ind["near_bb_upper"]=ind["near_bb_lower"]=False

    # Volume
    avg_vol=sum(volumes[-20:])/20
    ind["vol_ratio"]=round(volumes[-1]/avg_vol,2) if avg_vol>0 else 1
    ind["high_volume"]=ind["vol_ratio"]>1.5
    ind["vol_confirmed_bull"]=closes[-1]>closes[-2] and ind["vol_ratio"]>1.3
    ind["vol_confirmed_bear"]=closes[-1]<closes[-2] and ind["vol_ratio"]>1.3
    ind["vol_weak_bull"]    =closes[-1]>closes[-2] and ind["vol_ratio"]<0.6
    ind["vol_weak_bear"]    =closes[-1]<closes[-2] and ind["vol_ratio"]<0.6
    ind["vol_climax_buy"]   =closes[-1]<closes[-2] and ind["vol_ratio"]>2.2
    ind["vol_climax_sell"]  =closes[-1]>closes[-2] and ind["vol_ratio"]>2.2

    # Support/Resistance (swing highs/lows — more accurate SL placement)
    n=20
    ind["swing_low"]  = round(min(lows[-n:]),4)
    ind["swing_high"] = round(max(highs[-n:]),4)
    ind["support"]    = ind["swing_low"]
    ind["resistance"] = ind["swing_high"]
    ind["near_support"]    =abs(closes[-1]-ind["support"])/closes[-1]<0.004
    ind["near_resistance"] =abs(closes[-1]-ind["resistance"])/closes[-1]<0.004

    # FVG
    ind["fvg_bull"]=lows[-1]>highs[-3] if len(candles)>=3 else False
    ind["fvg_bear"]=highs[-1]<lows[-3] if len(candles)>=3 else False

    # Order Block
    if len(candles)>=5:
        op=[c["open"] for c in candles]
        ind["ob_bull"]=(closes[-1]-op[-1])/op[-1]*100>0.5 and closes[-2]<op[-2]
        ind["ob_bear"]=(op[-1]-closes[-1])/op[-1]*100>0.5 and closes[-2]>op[-2]
    else:
        ind["ob_bull"]=ind["ob_bear"]=False

    # Market structure
    if len(closes)>=6:
        ind["hh"]=max(highs[-3:])>max(highs[-6:-3])
        ind["hl"]=min(lows[-3:]) >min(lows[-6:-3])
        ind["ll"]=min(lows[-3:]) <min(lows[-6:-3])
        ind["lh"]=max(highs[-3:])<max(highs[-6:-3])
    else:
        ind["hh"]=ind["hl"]=ind["ll"]=ind["lh"]=False

    if ind["hh"] and ind["hl"] and ind["ema_strong_bull"]: ind["trend"]="STRONG_BULL"
    elif ind["hh"] and ind["hl"]:                          ind["trend"]="BULL"
    elif ind["ll"] and ind["lh"]:                          ind["trend"]="BEAR"
    else:                                                   ind["trend"]="RANGING"

    # Momentum
    if len(closes)>=5:
        ind["momentum"]=round((closes[-1]-closes[-5])/closes[-5]*100,3)
        ind["momentum_bull"]=ind["momentum"]>0.1
    else:
        ind["momentum"]=0; ind["momentum_bull"]=False

    return ind
