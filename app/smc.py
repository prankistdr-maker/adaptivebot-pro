"""SMC Analysis - Order Blocks, FVG, Liquidity, Market Structure"""

def analyze_smc(candles):
    if not candles or len(candles)<20:
        return {}
    closes=[c["close"] for c in candles]
    highs=[c["high"] for c in candles]
    lows=[c["low"] for c in candles]
    vols=[c["volume"] for c in candles]
    price=closes[-1]
    avg_vol=sum(vols[-20:])/20; high_vol=vols[-1]>avg_vol*1.3

    # Range
    rh=max(highs[-30:]); rl=min(lows[-30:])

    # Wyckoff Spring: dip below range low, recover
    spring=(lows[-1]<rl*0.999 and closes[-1]>rl and closes[-1]>closes[-2] and high_vol)
    # Wyckoff Upthrust: break above range high, fail back
    upthrust=(highs[-1]>rh*1.001 and closes[-1]<rh and closes[-1]<closes[-2] and high_vol)

    # Liquidity sweep
    prev_h=max(highs[-25:-5]); prev_l=min(lows[-25:-5])
    swept_low=(min(lows[-5:])<prev_l and closes[-1]>prev_l)
    swept_high=(max(highs[-5:])>prev_h and closes[-1]<prev_h)

    # FVG
    bull_fvg=lows[-1]>highs[-3] if len(candles)>=3 else False
    bear_fvg=highs[-1]<lows[-3] if len(candles)>=3 else False

    # Order Blocks
    bull_ob=bear_ob=False
    for i in range(len(candles)-4,len(candles)-1):
        if i<1: continue
        if closes[i]<closes[i-1] and highs[i+1]>highs[i]:
            if lows[i]<=price<=highs[i]*1.002: bull_ob=True; break
        if closes[i]>closes[i-1] and lows[i+1]<lows[i]:
            if lows[i]*0.998<=price<=highs[i]: bear_ob=True; break

    # Structure
    hh=max(highs[-3:])>max(highs[-6:-3]) if len(highs)>=6 else False
    hl=min(lows[-3:]) >min(lows[-6:-3])  if len(lows)>=6  else False
    ll=min(lows[-3:]) <min(lows[-6:-3])  if len(lows)>=6  else False
    lhh=max(highs[-3:])<max(highs[-6:-3]) if len(highs)>=6 else False
    bos_bull=closes[-1]>max(highs[-20:-5]) if len(candles)>=20 else False
    bos_bear=closes[-1]<min(lows[-20:-5])  if len(candles)>=20 else False

    if hh and hl: bias="buy"
    elif ll and lhh: bias="sell"
    else: bias="neutral"

    return {
        "wyckoff_spring":   spring,
        "wyckoff_upthrust": upthrust,
        "liquidity_bull":   swept_low,
        "liquidity_bear":   swept_high,
        "bull_fvg":         bull_fvg,
        "bear_fvg":         bear_fvg,
        "bull_ob":          bull_ob,
        "bear_ob":          bear_ob,
        "bias":             bias,
        "bos_bull":         bos_bull,
        "bos_bear":         bos_bear,
        "range_high":       rh,
        "range_low":        rl,
    }
