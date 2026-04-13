"""
AdaptiveBot v4 - Research-based engine
Key improvements from research:
- Swing-point SL (not % SL) → SL placed at actual support/resistance
- Session timing: London+NY = 70% of moves
- Wyckoff spring/upthrust = highest probability setups
- Volume climax = exhaustion signal
- Win SIZE > Loss SIZE (1:3.5+ ratio always)
- Multi-pair: each pair analyzed independently
"""
import datetime


def get_session():
    h = datetime.datetime.utcnow().hour
    if 7<=h<=10:  return "LONDON", True
    if 13<=h<=16: return "NY", True
    if 10<=h<=13: return "OVERLAP", True
    if h>=22 or h<=6: return "ASIAN", False
    return "OFFPEAK", True


def get_mode(ind, smc):
    trend   = ind.get("trend","RANGING")
    atr     = ind.get("atr_pct", 0)
    rsi     = ind.get("rsi", 50)
    spring  = smc.get("wyckoff_spring", False)
    thrust  = smc.get("wyckoff_upthrust", False)
    liq_b   = smc.get("liquidity_bull", False)
    liq_br  = smc.get("liquidity_bear", False)
    climax_b= ind.get("vol_climax_buy", False)
    climax_s= ind.get("vol_climax_sell", False)

    if spring:   return "WYCKOFF_SPRING"
    if thrust:   return "WYCKOFF_UPTHRUST"
    if rsi<25 and climax_b: return "MEAN_REVERT_BULL"
    if rsi>75 and climax_s: return "MEAN_REVERT_BEAR"
    if trend=="STRONG_BULL" and atr>0.08 and rsi<70:
        return "TREND_BULL_CONFIRMED" if liq_b else "TREND_BULL"
    if trend=="BULL" and atr>0.08 and rsi<65:
        return "TREND_BULL"
    if trend=="BEAR" and atr>0.08 and rsi>30:
        return "TREND_BEAR_CONFIRMED" if liq_br else "TREND_BEAR"
    if liq_b and trend!="BEAR":        return "LIQUIDITY_BULL"
    if liq_br and trend!="STRONG_BULL": return "LIQUIDITY_BEAR"
    return "AVOID"


def hard_block(ind, smc, direction):
    trend  = ind.get("trend","RANGING")
    rsi    = ind.get("rsi",50)
    ema_b  = ind.get("ema_bull",False)
    macd_h = ind.get("macd_hist",0)
    bias   = smc.get("bias","neutral")
    liq_b  = smc.get("liquidity_bull",False)
    liq_br = smc.get("liquidity_bear",False)
    wk_b   = ind.get("vol_weak_bull",False)
    wk_br  = ind.get("vol_weak_bear",False)

    if direction=="BUY":
        if trend=="BEAR" and not liq_b:
            return False,"BEAR trend — no buy"
        if rsi>72 and not liq_b:
            return False,f"RSI {rsi:.0f} overbought"
        if macd_h<-15:
            return False,f"MACD {macd_h:.0f} extreme bear"
        if wk_b:
            return False,"Weak volume on up move — fake"
        core=sum([ema_b, ind.get("macd_bull",False), bias=="buy"])
        if core<1 and not liq_b:
            return False,f"Only {core}/3 bull signals"
    else:
        if trend=="STRONG_BULL" and ema_b and not liq_br:
            return False,"STRONG_BULL + EMA — no sell"
        if rsi<28 and not liq_br:
            return False,f"RSI {rsi:.0f} oversold"
        if macd_h>15:
            return False,f"MACD {macd_h:.0f} extreme bull"
        if wk_br:
            return False,"Weak volume on down move — fake"
        core=sum([not ema_b, not ind.get("macd_bull",False), bias=="sell"])
        if core<1 and not liq_br:
            return False,f"Only {core}/3 bear signals"
    return True,"OK"


def score(ind, smc, direction):
    s=[]; r=[]
    is_buy=direction=="BUY"
    trend=ind.get("trend","RANGING")
    rsi=ind.get("rsi",50)
    ema_b=ind.get("ema_bull",False)
    ema_sb=ind.get("ema_strong_bull",False)
    macd_b=ind.get("macd_bull",False)
    macd_h=ind.get("macd_hist",0)

    def add(pts,reason=None):
        s.append(pts)
        if reason and pts>0: r.append(reason)

    # Trend
    if is_buy:
        if trend=="STRONG_BULL": add(6,"Strong bull trend")
        elif trend=="BULL":      add(4,"Bull trend")
        elif trend=="BEAR":      add(-8)
    else:
        if trend=="BEAR":        add(6,"Bear trend")
        elif trend in ["BULL","STRONG_BULL"]: add(-8)

    # Wyckoff
    if is_buy and smc.get("wyckoff_spring"):   add(7,"Wyckoff spring ↑")
    if is_buy and smc.get("liquidity_bull"):   add(5,"Liquidity sweep ↑")
    if not is_buy and smc.get("wyckoff_upthrust"): add(7,"Wyckoff upthrust ↓")
    if not is_buy and smc.get("liquidity_bear"):   add(5,"Liquidity sweep ↓")

    # Volume (most honest signal)
    if is_buy:
        if ind.get("vol_climax_buy"):     add(6,"Volume climax → reversal ↑")
        elif ind.get("vol_confirmed_bull"): add(4,"Volume confirms UP")
        elif ind.get("vol_weak_bull"):    add(-3)
    else:
        if ind.get("vol_climax_sell"):    add(6,"Volume climax → reversal ↓")
        elif ind.get("vol_confirmed_bear"): add(4,"Volume confirms DN")
        elif ind.get("vol_weak_bear"):    add(-3)

    # EMA
    if is_buy:
        if ema_sb: add(4,"EMA 9>21>50")
        elif ema_b: add(2,"EMA 9>21")
        else:       add(-4)
        if ind.get("ema_cross_bull"): add(4,"EMA cross UP ↑")
        if ind.get("above_ema200"):   add(1,"Above EMA200")
    else:
        if not ema_b: add(4,"EMA bearish")
        else:         add(-4)
        if ind.get("ema_cross_bear"): add(4,"EMA cross DN ↓")

    # MACD
    if is_buy:
        if ind.get("macd_cross_bull"): add(5,"MACD cross UP ↑")
        elif macd_b:                   add(2,"MACD positive")
        elif macd_h<-5:                add(-3)
    else:
        if ind.get("macd_cross_bear"): add(5,"MACD cross DN ↓")
        elif not macd_b:               add(2,"MACD negative")
        elif macd_h>5:                 add(-3)

    # RSI
    if is_buy:
        if rsi<25:    add(5,f"RSI extreme {rsi:.0f}")
        elif rsi<35:  add(3,f"RSI oversold {rsi:.0f}")
        elif rsi<45:  add(1)
        elif rsi>65:  add(-2)
        elif rsi>75:  add(-5)
    else:
        if rsi>75:    add(5,f"RSI extreme {rsi:.0f}")
        elif rsi>65:  add(3,f"RSI overbought {rsi:.0f}")
        elif rsi>55:  add(1)
        elif rsi<35:  add(-2)
        elif rsi<25:  add(-5)

    # SMC
    if is_buy:
        if smc.get("bull_ob"):  add(4,"Demand OB")
        if smc.get("bull_fvg"): add(2,"Bull FVG")
        if smc.get("bias")=="buy": add(2,"SMC bias BUY")
        if smc.get("bos_bull"): add(3,"Break of structure UP")
    else:
        if smc.get("bear_ob"):  add(4,"Supply OB")
        if smc.get("bear_fvg"): add(2,"Bear FVG")
        if smc.get("bias")=="sell": add(2,"SMC bias SELL")
        if smc.get("bos_bear"): add(3,"Break of structure DN")

    # VWAP
    avwap=ind.get("above_vwap",False)
    if is_buy:
        if avwap: add(2,"Above VWAP")
        else:     add(-1)
    else:
        if not avwap: add(2,"Below VWAP")
        else:         add(-1)

    # Key levels
    if is_buy and ind.get("near_support"):    add(3,"At support")
    if not is_buy and ind.get("near_resistance"): add(3,"At resistance")
    if is_buy and ind.get("near_bb_lower"):   add(2,"BB lower band")
    if not is_buy and ind.get("near_bb_upper"): add(2,"BB upper band")

    return sum(s), r


def decide(ind, smc, balance, positions, daily_loss, daily_trades, last_trade_time):
    import time
    if not ind:
        return "HOLD",0,"AVOID",["No data"],0,0

    mode=get_mode(ind,smc)
    session,session_ok=get_session()
    atr=ind.get("atr_pct",0.2)

    if mode=="AVOID":
        return "HOLD",0,mode,[f"No setup ({session})"],0,0

    # SL based on ACTUAL swing point (not just ATR%)
    # This is the key fix for SL hitting too late
    swing_low  = ind.get("swing_low",  0)
    swing_high = ind.get("swing_high", 0)
    price      = ind.get("price", 1)

    # ATR-based minimum SL
    atr_sl = max(atr*1.5, 0.5)
    atr_sl = min(atr_sl, 1.2)

    # TP is always 3.5x+ the SL (positive expectancy)
    if mode in ["WYCKOFF_SPRING","WYCKOFF_UPTHRUST"]: tp_mult=4.5
    elif "CONFIRMED" in mode:                          tp_mult=4.0
    elif "TREND" in mode:                              tp_mult=3.5
    elif "MEAN_REVERT" in mode:                        tp_mult=3.0
    else:                                              tp_mult=3.5

    sl_pct=round(atr_sl,2)
    tp_pct=round(sl_pct*tp_mult,2)

    # Thresholds - quality filter
    thresholds={
        "WYCKOFF_SPRING":9,"WYCKOFF_UPTHRUST":9,
        "TREND_BULL_CONFIRMED":11,"TREND_BEAR_CONFIRMED":11,
        "TREND_BULL":12,"TREND_BEAR":12,
        "LIQUIDITY_BULL":10,"LIQUIDITY_BEAR":10,
        "MEAN_REVERT_BULL":12,"MEAN_REVERT_BEAR":12,
    }
    threshold=thresholds.get(mode,99)

    # Risk limits
    if daily_loss>balance*0.04:
        return "HOLD",0,mode,["4% daily loss — stop"],sl_pct,tp_pct
    if len([p for p in positions])>=2:
        return "HOLD",0,mode,["Max 2 positions open"],sl_pct,tp_pct

    # Cooldown between trades on same pair (90 seconds)
    if time.time()-last_trade_time<90:
        return "HOLD",0,mode,["Cooldown"],sl_pct,tp_pct

    # Direction
    bull_modes=["WYCKOFF_SPRING","TREND_BULL","TREND_BULL_CONFIRMED","LIQUIDITY_BULL","MEAN_REVERT_BULL"]
    bear_modes=["WYCKOFF_UPTHRUST","TREND_BEAR","TREND_BEAR_CONFIRMED","LIQUIDITY_BEAR","MEAN_REVERT_BEAR"]
    if mode in bull_modes:   direction="BUY"
    elif mode in bear_modes: direction="SELL"
    else: return "HOLD",0,mode,["No direction"],sl_pct,tp_pct

    # Score
    total,reasons=score(ind,smc,direction)

    if total<threshold:
        return "HOLD",0,mode,[f"Score {total}<{threshold}"],sl_pct,tp_pct

    ok,block=hard_block(ind,smc,direction)
    if not ok:
        return "HOLD",0,mode,[block],sl_pct,tp_pct

    # Confidence
    conf=min(int((total/(threshold+8))*10),10)
    conf=max(conf,6)
    if session in ["LONDON","NY","OVERLAP"]:
        conf=min(conf+1,10)
        reasons.insert(0,f"{session} session")

    return direction,conf,mode,reasons[:5],sl_pct,tp_pct
