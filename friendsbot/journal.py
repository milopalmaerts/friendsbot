"""Read the author's trade journal (data_final.xlsx).

Columns: PAIR, POSITION (2EL / 2ES / F2EL / F2ES), TIME/CHINA, TP/SL, RUNNER, PIPS.
"2EL"/"2ES" read as second-entry long/short. The meaning of the "F" prefix is unknown;
the author did not know either, so we only report it as a separate label.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from pathlib import Path

import openpyxl

from .data import ROOT

DEFAULT_XLSX = ROOT / "data" / "journal" / "data_final.xlsx"
CHINA_UTC_OFFSET = 8


def load(path: str | Path = DEFAULT_XLSX) -> list[dict]:
    ws = openpyxl.load_workbook(path, data_only=True).active
    rows = []
    for r in ws.iter_rows(min_row=2, max_col=6, values_only=True):
        if not r[0]:
            continue
        pair, position, t, result, runner, pips = r
        position = str(position).strip().upper()
        utc = None
        if isinstance(t, dt.time):
            minutes = (t.hour - CHINA_UTC_OFFSET) * 60 + t.minute
            utc = f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"
        rows.append({
            "pair": str(pair).strip().upper(),
            "position": position,
            "direction": "long" if position.endswith("L") else "short",
            "prefix_f": position.startswith("F"),
            "time_china": t.strftime("%H:%M") if isinstance(t, dt.time) else None,
            "time_utc": utc,
            "result": str(result).strip().upper(),
            "runner": {"YES": True, "NO": False}.get(str(runner).strip().upper()),
            "pips": pips,
        })
    return rows


def summary(rows: list[dict]) -> dict:
    n = len(rows)
    wins = sum(r["result"] == "TP" for r in rows)
    times = sorted(r["time_utc"] for r in rows if r["time_utc"])
    tp_pips = [r["pips"] for r in rows if r["result"] == "TP"]
    hours = Counter(t[:2] for t in times)
    return {
        "trades": n,
        "tp": wins,
        "sl": n - wins,
        "win_rate": round(wins / n, 4) if n else None,
        "positions": dict(Counter(r["position"] for r in rows)),
        "directions": dict(Counter(r["direction"] for r in rows)),
        "runner": {"yes": sum(r["runner"] is True for r in rows), "no": sum(r["runner"] is False for r in rows),
                   "unknown": sum(r["runner"] is None for r in rows)},
        "time_utc_first": times[0] if times else None,
        "time_utc_last": times[-1] if times else None,
        "entries_by_utc_hour": dict(sorted(hours.items())),
        "median_tp_pips": sorted(tp_pips)[len(tp_pips) // 2] if tp_pips else None,
        "sl_pips": [r["pips"] for r in rows if r["result"] == "SL"],
        "missing_time": sum(r["time_utc"] is None for r in rows),
    }
