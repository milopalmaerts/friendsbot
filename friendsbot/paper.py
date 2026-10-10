"""Paper-trading bot over gold, US100 and BTCUSDT.

The rules are frozen on FORWARD_START. Every month the bot picks, per market and session, the
timeframe that did best over the previous LOOKBACK_DAYS (same rule as portfolio.py) and trades
only those combinations that month. Running `update` again after new candles arrive extends the
journal; trades before FORWARD_START are a walk-forward *simulation*, trades from FORWARD_START on
are *paper* trades: taken by rules that were fixed before those candles existed.

Exit: half at 2R, stop to breakeven, runner until a close through the EMA 21 — a lower win rate
(~35–40%) in exchange for a higher reward per winner.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import summarize
from .data import ROOT, load_dataset, resample
from .portfolio import EXITS, MIN_DEV_AVG_R, MIN_DEV_TRADES, SESSIONS, TIMEFRAMES, cell_trades
from .research import FRIEND_RULES, window_filter
from .strategy import pending, with_indicators
from .timeframes import ASSETS, START

FORWARD_START = pd.Timestamp("2026-10-10", tz="UTC")   # rules frozen on this day
FIRST_MONTH = pd.Timestamp("2026-06-01", tz="UTC")     # first month with 4 months of history
LOOKBACK_DAYS = 120
EXIT = "2R_runner"
JOURNAL = ROOT / "reports" / "paper" / "journal.json"
DEFAULT_ACCOUNT = 10_000
DEFAULT_RISK_PCT = 0.5


def month_starts(first: pd.Timestamp, last: pd.Timestamp) -> list[pd.Timestamp]:
    out, m = [], first
    while m <= last:
        out.append(m)
        m = (m + pd.offsets.MonthBegin(1)).normalize()
    return out


def select(cells: dict, at: pd.Timestamp) -> list[dict]:
    """Best timeframe per (market, session) over the LOOKBACK_DAYS before `at`."""
    lo = at - pd.Timedelta(days=LOOKBACK_DAYS)
    best = {}
    for (asset, tf, ex, session), trades in cells.items():
        if ex != EXIT:
            continue
        s = summarize([t for t in trades if lo <= t.entry_time < at])
        if s["trades"] < MIN_DEV_TRADES or s["avg_r"] < MIN_DEV_AVG_R or s["total_r"] <= 0:
            continue
        key = (asset, session)
        if key not in best or s["avg_r"] > best[key]["avg_r"]:
            best[key] = {"asset": asset, "session": session, "tf": tf, "avg_r": s["avg_r"],
                         "trades": s["trades"], "total_r": s["total_r"]}
    return sorted(best.values(), key=lambda r: (r["asset"], SESSIONS.index(r["session"])))


def _trade_row(t, last_time) -> dict:
    status = "open" if t.exit_reason == "time" and t.exit_time >= last_time else "closed"
    return {
        "id": f"{t.tags['asset']}-{t.tags['tf']}-{t.direction}-{t.entry_time.strftime('%Y%m%d%H%M')}",
        "asset": t.tags["asset"], "tf": t.tags["tf"], "session": t.tags["session"], "direction": t.direction,
        "entry_time": t.entry_time.isoformat(), "exit_time": t.exit_time.isoformat(),
        "entry": round(t.entry, 2), "stop": round(t.stop, 2), "exit": round(t.exit_price, 2),
        "r": t.r, "r_gross": t.r_gross, "exit_reason": t.exit_reason, "status": status,
        "source": "papier" if t.entry_time >= FORWARD_START else "simulatie",
    }


def update(path: Path = JOURNAL) -> dict:
    data_end = {}
    for asset, spec in ASSETS.items():
        data_end[asset] = load_dataset(spec["dataset"]).index[-1]
    last = max(data_end.values())
    cells = cell_trades(exits=[EXIT], start=START, end=last + pd.Timedelta(minutes=1))

    selections, rows = [], []
    months = month_starts(FIRST_MONTH, last)
    for i, m in enumerate(months):
        nxt = months[i + 1] if i + 1 < len(months) else last + pd.Timedelta(days=1)
        chosen = select(cells, m)
        selections.append({"month": m.strftime("%Y-%m"), "cells": chosen})
        for c in chosen:
            for t in cells[(c["asset"], c["tf"], EXIT, c["session"])]:
                if m <= t.entry_time < nxt:
                    rows.append(_trade_row(t, data_end[c["asset"]]))
    rows.sort(key=lambda r: r["entry_time"])

    # paper trades are append-only: a closed paper trade already in the journal is never rewritten
    if path.exists():
        old = {r["id"]: r for r in json.loads(path.read_text())["trades"] if r["source"] == "papier"}
        new_ids = {r["id"] for r in rows}
        for r in rows:
            if r["id"] in old and old[r["id"]]["status"] == "closed":
                r.update(old[r["id"]])
        rows += [r for i, r in old.items() if i not in new_ids and r["status"] == "closed"]
        rows.sort(key=lambda r: r["entry_time"])

    # orders the bot would have working right now, for this month's selection
    cfg = FRIEND_RULES[0]
    orders = []
    for c in selections[-1]["cells"] if selections else []:
        fine = load_dataset(ASSETS[c["asset"]]["dataset"])
        tf = with_indicators(resample(fine.iloc[-60 * 24 * 10:], TIMEFRAMES[c["tf"]]), cfg)
        if not window_filter(c["session"])(tf.index[-1] + (tf.index[-1] - tf.index[-2])):
            continue
        for p in pending(tf, cfg):
            if p.get("entry_stop") and p.get("trend_ok"):
                orders.append({**c, **p})

    journal = {
        "generated": pd.Timestamp.now(tz="UTC").isoformat(),
        "forward_start": FORWARD_START.isoformat(),
        "data_end": {k: v.isoformat() for k, v in data_end.items()},
        "assets": {k: v["label"] for k, v in ASSETS.items()},
        "rules": {"exit": "50% op 2R, stop naar breakeven, runner tot slot door EMA 21",
                  "selection": f"per markt en sessie de beste timeframe over de vorige {LOOKBACK_DAYS} dagen "
                               f"(min. {MIN_DEV_TRADES} trades, gemiddeld ≥ +{MIN_DEV_AVG_R}R), elke maand opnieuw",
                  "timeframes": list(TIMEFRAMES), "sessions": SESSIONS,
                  "costs": {k: v["cost_note"] for k, v in ASSETS.items()}},
        "defaults": {"account": DEFAULT_ACCOUNT, "risk_pct": DEFAULT_RISK_PCT},
        "selections": selections,
        "orders": orders,
        "trades": rows,
        "summary": {"simulatie": summarize_rows([r for r in rows if r["source"] == "simulatie"]),
                    "papier": summarize_rows([r for r in rows if r["source"] == "papier"])},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(journal, indent=1, default=str))
    return journal


def summarize_rows(rows: list[dict]) -> dict:
    closed = [r for r in rows if r["status"] == "closed"]
    r = np.array([x["r"] for x in closed])
    if not len(r):
        return {"trades": 0, "open": len(rows)}
    return {"trades": int(len(r)), "open": len(rows) - len(closed), "win_rate": round(float((r > 0.05).mean()), 3),
            "total_r": round(float(r.sum()), 2), "avg_r": round(float(r.mean()), 3),
            "avg_win_r": round(float(r[r > 0.05].mean()), 2) if (r > 0.05).any() else None,
            "avg_loss_r": round(float(r[r < -0.05].mean()), 2) if (r < -0.05).any() else None}
