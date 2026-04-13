import threading

lock = threading.Lock()

PAIRS = {
    "BTCUSDT": {"kraken": "XBTUSD",  "display": "BTC/USDT"},
    "ETHUSDT": {"kraken": "ETHUSD",  "display": "ETH/USDT"},
    "SOLUSDT": {"kraken": "SOLUSD",  "display": "SOL/USDT"},
}

STARTING_BALANCE = 20.0

state = {
    "balance":         STARTING_BALANCE,
    "initial_balance": STARTING_BALANCE,
    "peak_balance":    STARTING_BALANCE,
    "total_pnl":       0.0,
    "pnl":             0.0,
    "positions":       [],
    "trades":          [],
    "total_trades":    0,
    "winning_trades":  0,
    "losing_trades":   0,
    "max_drawdown":    0.0,
    "daily_trades":    0,
    "daily_wins":      0,
    "daily_loss":      0.0,
    "daily_reset_hour": -1,
    "prices":          {p: 0.0 for p in PAIRS},
    "candles":         {p: []  for p in PAIRS},
    "ind":             {p: {}  for p in PAIRS},
    "smc":             {p: {}  for p in PAIRS},
    "decision":        {p: "HOLD" for p in PAIRS},
    "mode":            {p: "AVOID" for p in PAIRS},
    "conf":            {p: 0   for p in PAIRS},
    "reasons":         {p: []  for p in PAIRS},
    "last_trade_time": {p: 0   for p in PAIRS},
    "feed_status":     "connecting",
    "equity_curve":    [{"t": "start", "v": STARTING_BALANCE}],
}
