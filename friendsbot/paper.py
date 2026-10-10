"""Paper-trading bot over gold, US100 and BTCUSDT.

The rules are frozen on FORWARD_START. Trades before that date are a walk-forward *simulation*;
trades from FORWARD_START on are *paper* trades, taken by rules fixed before those candles existed.

Every month (on the 1st) the bot ranks, per market and session slot, the timeframes and windows
by their average R over the previous LOOKBACK_DAYS and keeps the best one per slot. The Asia slot
can be filled by the whole Asia session or by the author's own window (04:00–06:30 UTC).

Risk, decided trade by trade using only what is already known at that moment:
* the HOT_N best-ranked combinations risk HOT_RISK % per trade, the rest BASE_RISK %;
* a hot combination that loses HOT_DEMOTE_AFTER trades in a row drops to BASE_RISK for the rest
  of the month and the next-best combination takes its place;
* after ASSET_DAY_LOSSES losing trades on one market in a day, that market is done for the day;
* at most MAX_OPEN positions at the same time.

Exit: half at 2R, stop to breakeven, runner until a close through the EMA 21.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import summarize
from .data import ROOT, load_dataset, resample
from .portfolio import MIN_DEV_AVG_R, MIN_DEV_TRADES, SESSIONS, TIMEFRAMES, cell_trades
from .research import FRIEND_RULES, window_filter
from .strategy import pending, with_indicators
from .timeframes import ASSETS, START

FORWARD_START = pd.Timestamp("2026-10-10", tz="UTC")   # rules frozen on this day
FIRST_MONTH = pd.Timestamp("2026-06-01", tz="UTC")     # first month with 4 months of history
LOOKBACK_DAYS = 120
EXIT = "2R_runner"
CANDIDATE_SESSIONS = [*SESSIONS, "friend_window"]
SLOT = {"friend_window": "asia"}                         # the author's window competes for the Asia slot
BASE_RISK, HOT_RISK, HOT_N = 0.25, 0.5, 2
HOT_DEMOTE_AFTER = 3
ASSET_DAY_LOSSES = 2
MAX_OPEN = 3
LOSS_R = -0.05
JOURNAL = ROOT / "reports" / "paper" / "journal.json"
DEFAULT_ACCOUNT = 10_000


def month_starts(first: pd.Timestamp, last: pd.Timestamp) -> list[pd.Timestamp]:
    out, m = [], first
    while m <= last:
        out.append(m)
        m = (m + pd.offsets.MonthBegin(1)).normalize()
    return out


def select(cells: dict, at: pd.Timestamp) -> list[dict]:
    """Best timeframe/window per (market, slot) over the LOOKBACK_DAYS before `at`,
    ranked best first by average R."""
    lo = at - pd.Timedelta(days=LOOKBACK_DAYS)
    best = {}
    for (asset, tf, ex, session), trades in cells.items():
        if ex != EXIT:
            continue
        s = summarize([t for t in trades if lo <= t.entry_time < at])
        if s["trades"] < MIN_DEV_TRADES or s["avg_r"] < MIN_DEV_AVG_R or s["total_r"] <= 0:
            continue
        key = (asset, SLOT.get(session, session))
        if key not in best or s["avg_r"] > best[key]["avg_r"]:
            best[key] = {"asset": asset, "session": session, "tf": tf, "avg_r": s["avg_r"],
                         "trades": s["trades"], "total_r": s["total_r"]}
    return sorted(best.values(), key=lambda r: -r["avg_r"])


def cell_id(c) -> str:
    return f"{c['asset']}|{c['tf']}|{c['session']}"


def run_book(candidates: list[tuple], ranks: dict, *, hot_n=HOT_N, demote_after=HOT_DEMOTE_AFTER,
             asset_day_losses=ASSET_DAY_LOSSES, max_open=MAX_OPEN, base=BASE_RISK, hot=HOT_RISK):
    """Walk the candidate trades in time order and decide, with only past information, which are
    taken and with what risk. `candidates` = [(trade, cell_id, month)], `ranks` = {month: [cell_id…]}.
    Returns (taken: [(trade, cell_id, risk_pct, is_hot)], skipped: {reason: count})."""
    candidates = sorted(candidates, key=lambda x: x[0].entry_time)
    taken, skipped = [], defaultdict(int)
    pending_exits = []                         # accepted trades whose outcome is not known yet
    streak = defaultdict(int)                  # consecutive losses per cell
    demoted = defaultdict(set)                 # month → demoted cells
    day_losses = defaultdict(int)              # (asset, day) → losses
    for trade, cid, month in candidates:
        now = trade.entry_time
        # learn the outcome of every trade that has closed by now
        done = [p for p in pending_exits if p[0].exit_time <= now]
        pending_exits = [p for p in pending_exits if p[0].exit_time > now]
        for t, c, m in sorted(done, key=lambda p: p[0].exit_time):
            if t.r < LOSS_R:
                streak[c] += 1
                day_losses[(t.tags["asset"], t.exit_time.strftime("%Y-%m-%d"))] += 1
            elif t.r > -LOSS_R:
                streak[c] = 0
            if streak[c] >= demote_after:
                demoted[m].add(c)
        if day_losses[(trade.tags["asset"], now.strftime("%Y-%m-%d"))] >= asset_day_losses:
            skipped["dagstop markt"] += 1
            continue
        if max_open and len(pending_exits) >= max_open:
            skipped["max. posities"] += 1
            continue
        hot_cells = [c for c in ranks[month] if c not in demoted[month]][:hot_n]
        is_hot = cid in hot_cells
        taken.append((trade, cid, hot if is_hot else base, is_hot))
        pending_exits.append((trade, cid, month))
    return taken, dict(skipped)


def walk_forward(cells: dict, last: pd.Timestamp, **book):
    selections, candidates, ranks = [], [], {}
    months = month_starts(FIRST_MONTH, last)
    for i, m in enumerate(months):
        nxt = months[i + 1] if i + 1 < len(months) else last + pd.Timedelta(days=1)
        chosen = select(cells, m)
        key = m.strftime("%Y-%m")
        ranks[key] = [cell_id(c) for c in chosen]
        for rank, c in enumerate(chosen):
            c["rank"] = rank + 1
            c["hot"] = rank < book.get("hot_n", HOT_N)
        selections.append({"month": key, "cells": chosen})
        for c in chosen:
            for t in cells[(c["asset"], c["tf"], EXIT, c["session"])]:
                if m <= t.entry_time < nxt:
                    candidates.append((t, cell_id(c), key))
    # the same trade can come from two Asia-slot windows on one market only if both were chosen,
    # which `select` prevents; still guard against duplicates
    seen, unique = set(), []
    for t, c, m in candidates:
        k = (t.tags["asset"], t.direction, t.entry_time)
        if k not in seen:
            seen.add(k)
            unique.append((t, c, m))
    taken, skipped = run_book(unique, ranks, **book)
    return selections, taken, skipped


def _trade_row(t, risk_pct, is_hot, last_time) -> dict:
    status = "open" if t.exit_reason == "time" and t.exit_time >= last_time else "closed"
    return {
        "id": f"{t.tags['asset']}-{t.tags['tf']}-{t.direction}-{t.entry_time.strftime('%Y%m%d%H%M')}",
        "asset": t.tags["asset"], "tf": t.tags["tf"], "session": t.tags["session"], "direction": t.direction,
        "entry_time": t.entry_time.isoformat(), "exit_time": t.exit_time.isoformat(),
        "entry": round(t.entry, 2), "stop": round(t.stop, 2), "exit": round(t.exit_price, 2),
        "r": t.r, "r_gross": t.r_gross, "exit_reason": t.exit_reason, "status": status,
        "risk_pct": risk_pct, "hot": is_hot,
        "source": "papier" if t.entry_time >= FORWARD_START else "simulatie",
    }


def equity_stats(rows: list[dict]) -> dict:
    closed = sorted([r for r in rows if r["status"] == "closed"], key=lambda r: r["exit_time"])
    eq = peak = 1.0
    dd = 0.0
    for r in closed:
        eq *= 1 + r["risk_pct"] / 100 * r["r"]
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    return {"return_pct": round((eq - 1) * 100, 2), "max_dd_pct": round(dd * 100, 2)}


def load_cells(last: pd.Timestamp):
    return cell_trades(exits=[EXIT], start=START, end=last + pd.Timedelta(minutes=1), sessions=CANDIDATE_SESSIONS)


def update(path: Path = JOURNAL, cells=None) -> dict:
    data_end = {asset: load_dataset(spec["dataset"]).index[-1] for asset, spec in ASSETS.items()}
    last = max(data_end.values())
    cells = cells or load_cells(last)
    selections, taken, skipped = walk_forward(cells, last)
    rows = sorted((_trade_row(t, risk, hot, data_end[t.tags["asset"]]) for t, _, risk, hot in taken),
                  key=lambda r: r["entry_time"])

    # paper trades are append-only: a closed paper trade already in the journal is never rewritten
    if path.exists():
        old = {r["id"]: r for r in json.loads(path.read_text())["trades"] if r["source"] == "papier"}
        new_ids = {r["id"] for r in rows}
        for r in rows:
            if r["id"] in old and old[r["id"]]["status"] == "closed":
                r.update(old[r["id"]])
        rows += [r for i, r in old.items() if i not in new_ids and r["status"] == "closed"]
        rows.sort(key=lambda r: r["entry_time"])

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
        "rules": {
            "exit": "50% op 2R, stop naar breakeven, runner tot slot door EMA 21",
            "selection": f"elke 1e van de maand per markt en sessie de beste timeframe over de vorige {LOOKBACK_DAYS} "
                         f"dagen (min. {MIN_DEV_TRADES} trades, gemiddeld ≥ +{MIN_DEV_AVG_R}R); het venster van "
                         f"04:00–06:30 UTC concurreert met de hele Asia-sessie",
            "risk": {"base": BASE_RISK, "hot": HOT_RISK, "hot_n": HOT_N, "demote_after": HOT_DEMOTE_AFTER,
                     "asset_day_losses": ASSET_DAY_LOSSES, "max_open": MAX_OPEN},
            "timeframes": list(TIMEFRAMES), "sessions": CANDIDATE_SESSIONS,
            "costs": {k: v["cost_note"] for k, v in ASSETS.items()},
        },
        "defaults": {"account": DEFAULT_ACCOUNT, "base_risk": BASE_RISK},
        "selections": selections,
        "orders": orders,
        "skipped": skipped,
        "trades": rows,
        "summary": {src: {**summarize_rows([r for r in rows if r["source"] == src]),
                          **equity_stats([r for r in rows if r["source"] == src])}
                    for src in ("simulatie", "papier")},
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
