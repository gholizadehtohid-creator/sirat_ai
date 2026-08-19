import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import backtest_config
from backtesting import engine
from backtesting.pit import PointInTimeData
cfg = backtest_config.load()
data = PointInTimeData(cfg, verbose=False)
cal = data.calendar()
r = engine.run(backtest_config.DEFAULT_START, cal[-1], cfg=cfg, data=data,
               verbose=False, engine="v2", use_cache=False)
d = r.diagnostics
keep = ["sessions","rebalances","signals_total","empty_rebalances","ranked_out",
        "blocked_by","sized_to_zero","wanted_positions","opened_positions",
        "buy_orders_raised","buy_orders_expired","orders_raised","orders_expired",
        "stopped_out","target_hit","rotated_out","queue_blocked_exit",
        "entry_rejections","cash_analysis","filled","partial","refused",
        "buy_queue_blocked","sell_queue_blocked","participation_capped"]
out = {k: d.get(k) for k in keep if k in d}
print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
