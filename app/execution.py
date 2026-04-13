"""
Execution engine v4
Key fixes:
- SL placed at actual SWING LOW/HIGH (not just price - sl%)
- Trailing: ATR-based distance (tighter, gives back less)
- Breakeven: after 0.5% profit
- TIME exit: 90 min, only if profitable
- Emergency exit: 3h open + losing 3x SL
- Per-pair position tracking
"""
import time
from datetime import datetime
from app.state import state, lock

FEE_RATE = 0.001   # 0.1% per side
MIN_TRADE = 2      # $2 minimum (small account)
RISK_PCT  = 0.02   # 2% risk per trade
MAX_TIME  = 5400   # 90 minutes


def execute(pair, action, sl_pct, tp_pct, confidence, reasons, mode, ind):
    with lock:
        price   = state["prices"][pair]
        balance = state["balance"]
        positions = state["positions"]

    if price<=0 or balance<2:
        return

    # Max 1 position per pair, max 2 total
    pair_positions = [p for p in positions if p["pair"]==pair]
    if pair_positions or len(positions)>=2:
        return

    # Position sizing: risk 2% of balance
    risk_amount = balance*RISK_PCT
    sl_dollar   = price*sl_pct/100
    units       = risk_amount/sl_dollar if sl_dollar>0 else 0
    trade_value = units*price
    trade_value = min(trade_value, balance*0.3)
    trade_value = max(trade_value, MIN_TRADE)
    if trade_value>balance*0.95:
        return

    # SL PLACED AT ACTUAL SWING POINT (key fix)
    swing_low  = ind.get("swing_low",  price*(1-sl_pct/100))
    swing_high = ind.get("swing_high", price*(1+sl_pct/100))
    atr        = ind.get("atr", price*0.002)

    if action=="BUY":
        # SL = just below swing low (with 0.5 ATR buffer)
        sl_price = max(swing_low - atr*0.5, price*(1-sl_pct/100))
        tp_price = price*(1+tp_pct/100)
    else:
        # SL = just above swing high (with 0.5 ATR buffer)
        sl_price = min(swing_high + atr*0.5, price*(1+sl_pct/100))
        tp_price = price*(1-tp_pct/100)

    # Recalculate actual sl_pct from swing-based SL
    actual_sl_pct = abs(price-sl_price)/price*100
    entry_fee = trade_value*FEE_RATE

    position = {
        "id":              f"{pair[:3]}{int(time.time())}",
        "pair":            pair,
        "action":          action,
        "entry":           price,
        "amount":          trade_value,
        "sl":              round(sl_price,4),
        "tp":              round(tp_price,4),
        "sl_pct":          round(actual_sl_pct,2),
        "tp_pct":          tp_pct,
        "atr":             atr,
        "peak":            price,
        "trail_on":        False,
        "be_set":          False,
        "time":            time.time(),
        "time_str":        datetime.now().strftime("%H:%M:%S"),
        "confidence":      confidence,
        "mode":            mode,
        "reasons":         reasons[:3],
        "pnl":             0.0,
        "fee":             entry_fee,
    }

    with lock:
        state["positions"].append(position)
        state["balance"]         -= (trade_value+entry_fee)
        state["last_trade_time"][pair] = time.time()
        state["daily_trades"]   += 1
        state["total_trades"]   += 1

    print(f"[TRADE] {pair} {action} @${price:,.2f} | SL:${sl_price:,.2f}({actual_sl_pct:.1f}%) TP:${tp_price:,.2f}({tp_pct:.1f}%) | ${trade_value:.2f} | {mode}")


def manage_positions():
    with lock:
        positions = state["positions"][:]

    for pos in positions:
        pair   = pos["pair"]
        with lock:
            price = state["prices"][pair]
        if price<=0:
            continue

        action   = pos["action"]
        entry    = pos["entry"]
        amount   = pos["amount"]
        sl       = pos["sl"]
        tp       = pos["tp"]
        atr      = pos.get("atr", entry*0.002)
        peak     = pos.get("peak", entry)
        t_open   = pos.get("time", time.time())

        raw_pnl  = (price-entry)/entry*amount if action=="BUY" else (entry-price)/entry*amount
        pnl_pct  = raw_pnl/amount*100

        with lock:
            for p in state["positions"]:
                if p["id"]==pos["id"]:
                    p["pnl"]=round(raw_pnl,4)

        be  = pos.get("be_set",False)
        trail_on = pos.get("trail_on",False)

        # BREAKEVEN: after 0.5% profit → move SL to entry + fees
        if not be and pnl_pct>0.5:
            be_price = entry*(1+FEE_RATE*2.5) if action=="BUY" else entry*(1-FEE_RATE*2.5)
            if (action=="BUY" and be_price>sl) or (action=="SELL" and be_price<sl):
                with lock:
                    for p in state["positions"]:
                        if p["id"]==pos["id"]:
                            p["sl"]=round(be_price,4); p["be_set"]=True
                sl=be_price; be=True
                print(f"[BE] {pair} {pos['id']} SL→${be_price:,.2f}")

        # TRAILING: activate after 0.4% profit, use ATR distance (tighter)
        # ATR trailing gives back less than fixed % trailing
        if not trail_on and pnl_pct>0.4:
            with lock:
                for p in state["positions"]:
                    if p["id"]==pos["id"]:
                        p["trail_on"]=True
            trail_on=True

        if trail_on:
            trail_dist = atr*1.2  # 1.2x ATR trailing distance (tight)
            if action=="BUY" and price>peak:
                new_sl=price-trail_dist
                if new_sl>sl:
                    with lock:
                        for p in state["positions"]:
                            if p["id"]==pos["id"]:
                                p["sl"]=round(new_sl,4); p["peak"]=price
                    sl=new_sl; peak=price
            elif action=="SELL" and price<peak:
                new_sl=price+trail_dist
                if new_sl<sl:
                    with lock:
                        for p in state["positions"]:
                            if p["id"]==pos["id"]:
                                p["sl"]=round(new_sl,4); p["peak"]=price
                    sl=new_sl; peak=price

        # EXIT CONDITIONS
        exit_reason=None; exit_pnl=0.0

        if action=="BUY"  and price>=tp:
            exit_reason="TP"; exit_pnl=(tp-entry)/entry*amount-amount*FEE_RATE
        elif action=="SELL" and price<=tp:
            exit_reason="TP"; exit_pnl=(entry-tp)/entry*amount-amount*FEE_RATE
        elif action=="BUY"  and price<=sl:
            exit_reason="SL"; exit_pnl=(sl-entry)/entry*amount-amount*FEE_RATE
        elif action=="SELL" and price>=sl:
            exit_reason="SL"; exit_pnl=(entry-sl)/entry*amount-amount*FEE_RATE
        elif time.time()-t_open>MAX_TIME and raw_pnl>amount*FEE_RATE:
            exit_reason="TIME"; exit_pnl=raw_pnl-amount*FEE_RATE
        elif time.time()-t_open>10800 and pnl_pct<-(pos["sl_pct"]*3):
            exit_reason="EMERGENCY"; exit_pnl=raw_pnl-amount*FEE_RATE

        if exit_reason:
            _close(pos, exit_reason, exit_pnl, price)


def _close(pos, reason, pnl, close_price):
    amount=pos["amount"]
    record={
        "id":pos["id"],"pair":pos["pair"],"action":pos["action"],
        "entry":pos["entry"],"exit":close_price,"amount":round(amount,4),
        "pnl":round(pnl,4),"pnl_pct":round(pnl/amount*100,2),
        "reason":reason,"mode":pos.get("mode",""),
        "confidence":pos.get("confidence",0),"reasons":pos.get("reasons",[]),
        "duration":round((time.time()-pos["time"])/60,1),
        "time":pos.get("time_str",""),"exit_time":datetime.now().strftime("%H:%M:%S"),
        "won":pnl>0,
    }
    emoji="✅" if pnl>0 else "❌"
    with lock:
        state["balance"]+=amount+pnl
        if state["balance"]>state["peak_balance"]:
            state["peak_balance"]=state["balance"]
        dd=(state["peak_balance"]-state["balance"])/state["peak_balance"]*100
        if dd>state["max_drawdown"]: state["max_drawdown"]=round(dd,2)
        if pnl>0:
            state["winning_trades"]+=1; state["daily_wins"]+=1
        else:
            state["losing_trades"]+=1; state["daily_loss"]+=abs(pnl)
        state["total_pnl"]=state["balance"]-state["initial_balance"]
        state["trades"].insert(0,record)
        if len(state["trades"])>200: state["trades"].pop()
        state["positions"]=[p for p in state["positions"] if p["id"]!=pos["id"]]
        # Update equity curve
        state["equity_curve"].append({
            "t": datetime.now().strftime("%H:%M"),
            "v": round(state["balance"],4)
        })
        if len(state["equity_curve"])>200:
            state["equity_curve"].pop(1)
    print(f"{emoji} [{reason}] {pos['pair']} {pos['action']} @${close_price:,.2f} PnL:${pnl:+.4f}({record['pnl_pct']:+.1f}%) {record['duration']}min")


def daily_reset():
    import datetime as dt
    h=dt.datetime.utcnow().hour
    with lock:
        if h==0 and state.get("daily_reset_hour")!=0:
            state["daily_trades"]=0; state["daily_loss"]=0.0
            state["daily_wins"]=0;   state["daily_reset_hour"]=0
        elif h!=0:
            state["daily_reset_hour"]=h
