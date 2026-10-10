"""Session research: which session suits the second-entry strategy best?

Two kinds of evidence, kept apart on purpose:

1. `FRIEND_RULES` is fixed before looking at any results: EMA 21 trend (price above a rising
   EMA for longs), pullback stop, half off at 2R, runner until a close through the EMA,
   stop to breakeven after the partial. Its numbers are not tuned on the data.
2. A grid of variants is ranked on the development period only (Feb–Jun 2026); the winner
   per session is then scored once on the untouched test period (Jul–Sep 2026).
"""
from __future__ import annotations

import itertools
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import FRIEND_WINDOW, SESSIONS, ExitConfig, simulate, summarize
from .data import DEFAULT_CSV, load_candles
from .levels import KeyLevels
from .strategy import StrategyConfig, find_setups, with_indicators

DEV_END = pd.Timestamp("2026-07-01", tz="UTC")
WINDOWS = {**{k: v for k, v in SESSIONS.items()}, "friend_window": FRIEND_WINDOW}
WINDOW_LABELS = {
    "asia": "Asia 00:00–07:00 UTC",
    "london": "London 07:00–12:00 UTC",
    "new_york": "New York 12:00–17:00 UTC",
    "late": "Late US 17:00–22:00 UTC",
    "friend_window": "Journal window 04:00–06:30 UTC (12:00–14:30 China)",
}
FRIEND_RULES = (StrategyConfig(trend="side+slope"), ExitConfig())


def window_filter(name: str):
    a, b = WINDOWS[name]
    return lambda t: a <= t.hour + t.minute / 60 < b


def split(trades):
    return [t for t in trades if t.entry_time < DEV_END], [t for t in trades if t.entry_time >= DEV_END]


def grid():
    strategies = [StrategyConfig(trend=tr, ema_touch_atr=et, level_atr=la)
                  for tr, et, la in itertools.product(["none", "side", "side+slope"], [None, 0.5], [None, 0.5])]
    exits = [ExitConfig(mode="fixed", target_r=1.0), ExitConfig(mode="fixed", target_r=2.0),
             ExitConfig(), ExitConfig(breakeven_after_partial=False)]
    return strategies, exits


def describe(cfg: StrategyConfig, ex: ExitConfig) -> str:
    trend = {"none": "no trend filter", "side": "price on trend side of EMA",
             "side+slope": "price on trend side of a sloping EMA"}[cfg.trend]
    parts = [trend]
    if cfg.ema_touch_atr is not None:
        parts.append(f"pullback within {cfg.ema_touch_atr:g} ATR of EMA")
    if cfg.level_atr is not None:
        parts.append(f"pullback within {cfg.level_atr:g} ATR of a key level")
    return ", ".join(parts) + " · exit " + ex.label


def run(csv=DEFAULT_CSV, out_dir="reports") -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    df = with_indicators(load_candles(csv), StrategyConfig())
    levels = KeyLevels(df)

    # 1. pre-registered rules, both intrabar assumptions
    cfg, ex = FRIEND_RULES
    setups = find_setups(df, cfg, levels)
    friend = {}
    friend_trades = {}
    for w in WINDOWS:
        row = {}
        for mode in ("pessimistic", "optimistic"):
            trades = simulate(df, setups, replace(ex, intrabar=mode), window_filter(w))
            dev, test = split(trades)
            row[mode] = {"all": summarize(trades), "dev": summarize(dev), "test": summarize(test)}
            if mode == "pessimistic":
                friend_trades[w] = trades
        friend[w] = row

    # hour-of-day profile under the same rules
    all_trades = simulate(df, setups, ex)
    by_hour = []
    for hour in range(24):
        r = [t.r for t in all_trades if t.entry_time.hour == hour]
        by_hour.append({"hour": hour, "trades": len(r), "avg_r": round(float(np.mean(r)), 3) if r else None,
                        "total_r": round(float(np.sum(r)), 2)})

    # cost sensitivity in the journal window
    costs = []
    for c in (0.0, 0.15, 0.30, 0.50, 0.80):
        trades = simulate(df, setups, replace(ex, cost=c), window_filter("friend_window"))
        costs.append({"cost": c, **summarize(trades)})

    # 2. grid, selected on development data only
    strategies, exits = grid()
    variants = []
    for s_cfg in strategies:
        s_setups = find_setups(df, s_cfg, levels)
        for e_cfg in exits:
            for w in WINDOWS:
                trades = simulate(df, s_setups, e_cfg, window_filter(w))
                dev, test = split(trades)
                variants.append({"window": w, "rules": describe(s_cfg, e_cfg),
                                 "dev": summarize(dev), "test": summarize(test)})
    selected = {}
    for w in WINDOWS:
        pool = [v for v in variants if v["window"] == w and v["dev"]["trades"] >= 30]
        best = max(pool, key=lambda v: v["dev"]["total_r"])
        selected[w] = best

    months = sorted({t.entry_time.strftime("%Y-%m") for t in friend_trades["friend_window"]})
    monthly = []
    for mth in months:
        for w in WINDOWS:
            r = [t.r for t in friend_trades[w] if t.entry_time.strftime("%Y-%m") == mth]
            monthly.append({"month": mth, "window": w, "trades": len(r), "total_r": round(float(sum(r)), 2)})

    result = {
        "data": {"first": df.index[0].isoformat(), "last": df.index[-1].isoformat(), "candles": len(df),
                 "dev_end": DEV_END.isoformat()},
        "windows": WINDOW_LABELS,
        "friend_rules": {"strategy": cfg.to_dict(), "exit": ex.to_dict(), "description": describe(cfg, ex)},
        "friend": friend,
        "by_hour": by_hour,
        "cost_sensitivity": costs,
        "monthly": monthly,
        "selected": selected,
        "variants_tested": len(strategies) * len(exits),
    }
    (out / "results.json").write_text(json.dumps(result, indent=1, default=str))
    (out / "trades_friend_rules.json").write_text(json.dumps(
        {w: [t.to_dict() for t in ts] for w, ts in friend_trades.items()}, default=str))
    return result
