import numpy as np
import pandas as pd
import pytest

from friendsbot.backtest import ExitConfig, simulate
from friendsbot.data import resample
from friendsbot.execution import simulate_fine
from friendsbot.strategy import StrategyConfig, find_setups, with_indicators

from test_engine import LONG, CFG


def minutes(rows, high_first, start="2026-03-02 04:00"):
    """Expand (high, low) 5-minute candles into 1-minute paths. high_first[i] picks the order."""
    t, o, h, l, c = [], [], [], [], []
    base = pd.Timestamp(start, tz="UTC")
    prev = (rows[0][0] + rows[0][1]) / 2
    for i, (hi, lo) in enumerate(rows):
        close = (hi + lo) / 2
        path = [prev, hi, lo, close] if high_first[i] else [prev, lo, hi, close]
        pts = [min(max(p, lo), hi) for p in path]
        legs = [(pts[0], pts[1]), (pts[1], pts[1]), (pts[1], pts[2]), (pts[2], pts[3]), (pts[3], pts[3])]
        for k, (a, b) in enumerate(legs):
            t.append(base + pd.Timedelta(minutes=5 * i + k))
            o.append(a); c.append(b); h.append(max(a, b)); l.append(min(a, b))
        prev = close
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1.0}, index=pd.DatetimeIndex(t))


ROWS = LONG[:9] + [(110, 100)] + LONG[10:]   # candle 9 touches both the stop and the 2R target


@pytest.mark.parametrize("high_first,expected", [(True, "target"), (False, "stop")])
def test_minute_path_decides_tie_inside_the_chart_candle(high_first, expected):
    order = [True] * len(ROWS)
    order[9] = high_first
    fine = minutes(ROWS, order)
    tf = with_indicators(resample(fine, "5min"), CFG)
    setups = find_setups(tf, CFG)
    assert [s.entry_bar for s in setups if s.direction == "long"] == [8]
    t = simulate_fine(tf, fine, [s for s in setups if s.direction == "long"],
                      ExitConfig(mode="fixed", target_r=2.0, cost=0))[0]
    assert t.exit_reason == expected
    assert t.r == pytest.approx(2.0 if expected == "target" else -1.0)
    coarse = simulate(tf, [s for s in setups if s.direction == "long"], ExitConfig(mode="fixed", target_r=2.0, cost=0))[0]
    assert coarse.exit_reason == "stop"   # without minute data the stop always wins the tie


def test_percentage_cost_is_charged_in_r():
    fine = minutes(LONG, [True] * len(LONG))
    tf = with_indicators(resample(fine, "5min"), CFG)
    s = [x for x in find_setups(tf, CFG) if x.direction == "long"]
    a = simulate_fine(tf, fine, s, ExitConfig(mode="fixed", target_r=1.0, cost=0))[0]
    b = simulate_fine(tf, fine, s, ExitConfig(mode="fixed", target_r=1.0, cost=0, cost_pct=0.001))[0]
    assert a.r - b.r == pytest.approx(0.001 * s[0].entry / s[0].risk, abs=1e-3)
