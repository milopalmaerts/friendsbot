"""One bot, several markets: trade each market only in the sessions/timeframes that suit it.

Picking the best cells after seeing all results makes any combination look great, so the
selection here only looks at the development period (Feb–Jun 2026). The chosen portfolio is
then scored once on the test period (Jul–Sep 2026). A portfolio picked by hand from the full
results is reported next to it, clearly marked as not out-of-sample.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from .backtest import ExitConfig, summarize
from .data import load_dataset, resample
from .execution import simulate_fine
from .levels import KeyLevels
from .research import DEV_END, FRIEND_RULES, window_filter
from .strategy import find_setups, with_indicators
from .timeframes import ASSETS, END, START

TIMEFRAMES = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h"}
SESSIONS = ["asia", "london", "new_york", "late"]          # non-overlapping, together the whole day
EXITS = {
    "author": ExitConfig(mode="adaptive", target_r=1.0),    # 1R; good setup: half at 1R + runner
    "1R": ExitConfig(mode="fixed", target_r=1.0),
    "2R_runner": ExitConfig(),                              # half at 2R + runner (earlier assumption)
}
# Selection rule, fixed in advance: enough development trades, positive total and average.
MIN_DEV_TRADES, MIN_DEV_AVG_R = 20, 0.05
# The combination proposed by hand after looking at the full timeframe table.
HAND_PICKED = [("nas100", "15m", "new_york"), ("xauusd", "15m", "asia"), ("xauusd", "1h", "asia"),
               ("nas100", "1h", "asia"), ("btcusdt", "15m", "london")]


def cell_trades(assets=None, exits=None, start=START, end=END, timeframes=None, sessions=None):
    """{(asset, tf, exit, session): [Trade]} with Trade.tags['asset'] set."""
    cfg = FRIEND_RULES[0]
    out = {}
    exits = exits if isinstance(exits, dict) else {k: EXITS[k] for k in (exits or EXITS)}
    for asset in assets or ASSETS:
        spec = ASSETS[asset]
        fine = load_dataset(spec["dataset"])
        fine = fine[(fine.index >= start) & (fine.index < end)]
        levels = KeyLevels(fine)
        for tf_name, rule in (timeframes or TIMEFRAMES).items():
            tf = with_indicators(resample(fine, rule), cfg)
            setups = find_setups(tf, cfg, levels)
            for ex_name, ex in exits.items():
                ex = replace(ex, cost=spec["cost"], cost_pct=spec["cost_pct"])
                for session in sessions or SESSIONS:
                    trades = simulate_fine(tf, fine, setups, ex, window_filter(session), session=session)
                    for t in trades:
                        t.tags = {**t.tags, "asset": asset, "tf": tf_name, "session": session}
                    out[(asset, tf_name, ex_name, session)] = trades
            print(f"{asset} {tf_name} done", flush=True)
    return out


def _split(trades):
    return [t for t in trades if t.entry_time < DEV_END], [t for t in trades if t.entry_time >= DEV_END]


def combine(trades) -> dict:
    trades = sorted(trades, key=lambda t: t.entry_time)
    dev, test = _split(trades)
    r = np.array([t.r for t in trades])
    equity = np.cumsum(r) if len(r) else np.array([0.0])
    # most positions open at the same time
    events = sorted([(t.entry_time, 1) for t in trades] + [(t.exit_time, -1) for t in trades])
    open_now = peak = 0
    for _, d in events:
        open_now += d
        peak = max(peak, open_now)
    monthly = {}
    for t in trades:
        m = t.entry_time.strftime("%Y-%m")
        monthly[m] = round(monthly.get(m, 0) + t.r, 2)
    return {"all": summarize(trades), "dev": summarize(dev), "test": summarize(test),
            "max_open": peak, "monthly": monthly,
            "equity": [[t.exit_time.isoformat(), round(float(e), 2)] for t, e in zip(trades, equity)]}


def run(out_dir="reports") -> dict:
    cells = cell_trades()
    table = []
    for (asset, tf, ex, session), trades in cells.items():
        dev, test = _split(trades)
        sd, st = summarize(dev), summarize(test)
        table.append({"asset": asset, "tf": tf, "exit": ex, "session": session,
                      "dev": sd, "test": st, "trades": len(trades),
                      "good_share": round(float(np.mean([t.tags.get("good", False) for t in trades])), 2) if trades else None})

    result = {"period": {"start": START.isoformat(), "end": END.isoformat(), "dev_end": DEV_END.isoformat()},
              "rule": {"min_dev_trades": MIN_DEV_TRADES, "min_dev_avg_r": MIN_DEV_AVG_R},
              "assets": {k: v["label"] for k, v in ASSETS.items()}, "cells": table, "portfolios": {}}

    for ex in EXITS:
        # out-of-sample: choose on development only; per market+session keep the best timeframe
        best = {}
        for row in table:
            if row["exit"] != ex:
                continue
            d = row["dev"]
            if d["trades"] < MIN_DEV_TRADES or d["avg_r"] is None or d["avg_r"] < MIN_DEV_AVG_R or d["total_r"] <= 0:
                continue
            key = (row["asset"], row["session"])
            if key not in best or d["avg_r"] > best[key]["dev"]["avg_r"]:
                best[key] = row
        chosen = [(r["asset"], r["tf"], r["session"]) for r in best.values()]
        trades = [t for a, tf, s in chosen for t in cells[(a, tf, ex, s)]]
        result["portfolios"][f"selected_{ex}"] = {"exit": ex, "out_of_sample": True,
                                                  "cells": chosen, **combine(trades)}
        hand = [t for a, tf, s in HAND_PICKED for t in cells[(a, tf, ex, s)]]
        result["portfolios"][f"hand_{ex}"] = {"exit": ex, "out_of_sample": False,
                                              "cells": HAND_PICKED, **combine(hand)}
        everything = [t for (a, tf, e, s), ts in cells.items() if e == ex and tf == "15m" for t in ts]
        result["portfolios"][f"all15m_{ex}"] = {"exit": ex, "out_of_sample": True,
                                                "cells": "alle markten en sessies op 15m", **combine(everything)}

    # how much the author's exit gains from its "good setup" split
    good_vs_plain = {}
    for ex in ("author",):
        trades = [t for (a, tf, e, s), ts in cells.items() if e == ex for t in ts]
        for flag in (True, False):
            sub = [t for t in trades if bool(t.tags.get("good")) == flag]
            good_vs_plain["good" if flag else "plain"] = summarize(sub)
    result["good_vs_plain"] = good_vs_plain

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "portfolio.json").write_text(json.dumps(result, default=str))
    return result
