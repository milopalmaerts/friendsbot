"""Build the static website: website/index.html (self-contained, data inlined).

The journal is private: its summary is only included when data/journal/data_final.xlsx
exists locally, and the output that contains it goes to dist/ (git-ignored).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from friendsbot.backtest import ExitConfig, simulate  # noqa: E402
from friendsbot.data import load_candles  # noqa: E402
from friendsbot.research import FRIEND_RULES, window_filter  # noqa: E402
from friendsbot.strategy import find_setups, with_indicators  # noqa: E402

HERE = Path(__file__).resolve().parent

# Read off the author's TradingView screenshots (chart in UTC+1, converted to UTC). ±5 minutes.
SCREENSHOT_TRADES = {
    "2026-07-31": [("06:50", "short"), ("08:55", "short"), ("09:45", "short"), ("11:05", "short"),
                   ("11:50", "short"), ("15:15", "long"), ("17:50", "long")],
    "2026-08-04": [("11:05", "long")],
}


def candles(df, a, b):
    part = df.iloc[a:b + 1]
    return {
        "t": [int(t.timestamp()) for t in part.index],
        "o": part.open.round(2).tolist(), "h": part.high.round(2).tolist(),
        "l": part.low.round(2).tolist(), "c": part.close.round(2).tolist(),
        "e": part.ema.round(2).tolist(),
    }


def trade_view(df, t, a):
    sign = 1 if t.direction == "long" else -1
    risk = abs(t.entry - t.stop)
    return {
        "dir": t.direction, "entry_time": t.entry_time.isoformat(), "exit_time": t.exit_time.isoformat(),
        "entry": round(t.entry, 2), "stop": round(t.stop, 2), "target": round(t.entry + sign * 2 * risk, 2),
        "exit": round(t.exit_price, 2), "r": t.r, "reason": t.exit_reason, "session": t.session,
        "marks": {"0": t.zero_bar - a, "1": t.one_bar - a, "P": t.pullback_bar - a, "2": t.entry_bar - a,
                  "exit": t.exit_bar - a},
    }


def build(include_journal: bool | None = None) -> str:
    results = json.loads((ROOT / "reports" / "results.json").read_text())
    cfg, ex = FRIEND_RULES
    df = with_indicators(load_candles(), cfg)
    setups = find_setups(df, cfg)
    trades = simulate(df, setups, ex)

    examples = []
    for t in simulate(df, setups, ex, window_filter("friend_window")):
        a = max(0, t.zero_bar - 18)
        b = min(len(df) - 1, max(t.exit_bar + 6, t.entry_bar + 24))
        if b - a > 140:
            b = a + 140
        examples.append({**trade_view(df, t, a), "candles": candles(df, a, b)})

    days = []
    for day, marks in SCREENSHOT_TRADES.items():
        part = df.loc[day]
        a = df.index.get_loc(part.index[0])
        b = df.index.get_loc(part.index[-1])
        bot = [trade_view(df, t, a) for t in trades if str(t.entry_time.date()) == day]
        days.append({"day": day, "candles": candles(df, a, b), "bot": bot,
                     "friend": [{"time": tm, "dir": d} for tm, d in marks]})

    journal = None
    jpath = ROOT / "data" / "journal" / "data_final.xlsx"
    if include_journal is None:
        include_journal = jpath.exists()
    if include_journal and jpath.exists():
        from friendsbot import journal as jr
        journal = jr.summary(jr.load(jpath))

    tf_path = ROOT / "reports" / "timeframes.json"
    timeframes = json.loads(tf_path.read_text()) if tf_path.exists() else None
    pf_path = ROOT / "reports" / "portfolio.json"
    portfolio = json.loads(pf_path.read_text()) if pf_path.exists() else None
    payload = {"results": results, "examples": examples, "days": days, "journal": journal,
               "timeframes": timeframes, "portfolio": portfolio}
    body = (HERE / "template.html").read_text()
    body = body.replace("/*__DATA__*/null", json.dumps(payload, separators=(",", ":")))
    page = ("<!doctype html>\n<html lang=\"nl\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            f"</head>\n<body>\n{body}\n</body>\n</html>\n")
    if journal is None:
        target = HERE / "index.html"           # public build, committed
        target.write_text(page)
    else:
        target = ROOT / "dist" / "index.html"  # private build with journal stats
        target.parent.mkdir(exist_ok=True)
        target.write_text(page)
        (ROOT / "dist" / "artifact.html").write_text(body)
    return str(target)


if __name__ == "__main__":
    print(build(include_journal=False))
    print(build())
