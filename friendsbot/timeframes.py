"""Which chart timeframe suits the second-entry rules best, on gold, Nasdaq 100 and bitcoin?

Every timeframe is built from the same 1-minute candles and every trade is executed on those
1-minute candles (see execution.py), so timeframes are compared on equal terms. The rules are
the pre-registered FRIEND_RULES; only the chart timeframe and the market change.
Period: 2026-02-02 → 2026-09-25 for all three markets; development until 2026-07-01, test after.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import ExitConfig, summarize
from .data import load_dataset, resample
from .execution import simulate_fine
from .research import DEV_END, FRIEND_RULES, WINDOWS, split, window_filter
from .strategy import find_setups, with_indicators

START, END = pd.Timestamp("2026-02-02", tz="UTC"), pd.Timestamp("2026-09-26", tz="UTC")
TIMEFRAMES = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h"}
ASSETS = {
    # cost: spread + slippage per round trip in price units; cost_pct: exchange fees
    "xauusd": {"label": "Goud (XAUUSD)", "dataset": "xauusd_1m", "cost": 0.30, "cost_pct": 0.0,
               "cost_note": "$0,30 per trade (spread + slippage)"},
    "nas100": {"label": "US100 (Nasdaq 100)", "dataset": "nas100_1m", "cost": 1.5, "cost_pct": 0.0,
               "cost_note": "1,5 punt per trade (spread + slippage)"},
    "btcusdt": {"label": "BTCUSDT", "dataset": "btcusd_1m", "cost": 0.0, "cost_pct": 0.0008,
                "cost_note": "0,08% per trade (futures-fees taker/maker + slippage)"},
}
EXITS = {
    "friend": ExitConfig(),                                   # half at 2R, runner to EMA, BE
    "1R": ExitConfig(mode="fixed", target_r=1.0),             # "I'm out with 1:1"
    "2R": ExitConfig(mode="fixed", target_r=2.0),
}
WINDOW_SET = ["all", *WINDOWS]


def _filter(name):
    return None if name == "all" else window_filter(name)


def run(out_dir="reports", assets=None, timeframes=None) -> dict:
    cfg = FRIEND_RULES[0]
    results = {"period": {"start": START.isoformat(), "end": END.isoformat(), "dev_end": DEV_END.isoformat()},
               "assets": {}, "timeframes": list(TIMEFRAMES), "windows": WINDOW_SET}
    for key in assets or ASSETS:
        spec = ASSETS[key]
        fine = load_dataset(spec["dataset"])
        fine = fine[(fine.index >= START) & (fine.index < END)]
        days = (fine.index[-1] - fine.index[0]).days
        rows = {}
        for tf_name in timeframes or TIMEFRAMES:
            rule = TIMEFRAMES[tf_name]
            tf = fine if rule == "1min" else resample(fine, rule)
            tf = with_indicators(tf, cfg)
            setups = find_setups(tf, cfg)
            per_exit = {}
            for ex_name, ex in EXITS.items():
                ex = replace(ex, cost=spec["cost"], cost_pct=spec["cost_pct"])
                per_window = {}
                for w in WINDOW_SET if ex_name == "friend" else ["all"]:
                    trades = simulate_fine(tf, fine, setups, ex, _filter(w))
                    dev, test = split(trades)
                    risk = [abs(t.entry - t.stop) for t in trades]
                    rs = sorted(t.r for t in trades)
                    cost_r = [t.r_gross - t.r for t in trades]
                    per_window[w] = {
                        "all": summarize(trades), "dev": summarize(dev), "test": summarize(test),
                        "per_week": round(len(trades) / days * 7, 1),
                        "gross_total_r": round(float(sum(t.r_gross for t in trades)), 2),
                        "median_cost_r": round(float(np.median(cost_r)), 3) if trades else None,
                        "without_top5_r": round(float(sum(rs[:-5])), 2) if len(rs) > 5 else None,
                        "monthly": {m: round(float(sum(t.r for t in trades if t.entry_time.strftime("%Y-%m") == m)), 2)
                                    for m in sorted({t.entry_time.strftime("%Y-%m") for t in trades})},
                        "median_risk": round(float(np.median(risk)), 2) if risk else None,
                        "median_hold_min": round(float(np.median([(t.exit_time - t.entry_time).total_seconds() / 60
                                                                  for t in trades])), 0) if trades else None,
                    }
                per_exit[ex_name] = per_window
            rows[tf_name] = per_exit
            fr = per_exit["friend"]["all"]
            print(f"{key:8s} {tf_name:4s} trades {fr['all']['trades']:5d}  dev {fr['dev']['total_r']:+8.1f}R  "
                  f"test {fr['test']['total_r']:+8.1f}R  avg {fr['all']['avg_r']}", flush=True)
        results["assets"][key] = {k: spec[k] for k in ("label", "cost_note")} | {
            "first": fine.index[0].isoformat(), "last": fine.index[-1].isoformat(), "rows": rows}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "timeframes.json").write_text(json.dumps(results, indent=1, default=str))
    return results
