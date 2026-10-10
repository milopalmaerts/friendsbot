import numpy as np
import pandas as pd
import pytest

from friendsbot.backtest import ExitConfig, simulate, session_of, summarize
from friendsbot.strategy import StrategyConfig, find_setups, with_indicators
from friendsbot.levels import KeyLevels

CFG = StrategyConfig(zero_lookback=3, trend="none", min_risk_atr=0, max_risk_atr=100)


def frame(rows, start="2026-03-02 04:00"):
    """rows: (high, low[, close]); close defaults to mid-range, open = previous close."""
    idx = pd.date_range(start, periods=len(rows), freq="5min", tz="UTC")
    hi = np.array([r[0] for r in rows], float)
    lo = np.array([r[1] for r in rows], float)
    close = np.array([r[2] if len(r) > 2 else (r[0] + r[1]) / 2 for r in rows], float)
    op = np.clip(np.concatenate([[close[0]], close[:-1]]), lo, hi)
    df = pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 1.0}, index=idx)
    return with_indicators(df, CFG)


# Uptrend, 0 at bar 3, dip, 1 at bar 6, P at bar 7, 2 at bar 8.
LONG = [(100, 99), (101, 100), (102, 101), (105, 103),   # 3 = zero
        (104, 102), (103, 101.5),                          # pullback
        (103.5, 102),                                      # 6 = one (high > prev high, < zero)
        (103, 101),                                        # 7 = P (lower low)
        (104, 102.5),                                      # 8 = two: breaks 103 → entry 103.01
        (106, 103.5), (110, 105), (112, 108)]


def test_long_second_entry_detected_with_wick_break_entry_and_pullback_stop():
    s = find_setups(frame(LONG), CFG)
    assert len(s) == 1
    s = s[0]
    assert (s.direction, s.zero, s.one, s.pullback, s.entry_bar) == ("long", 3, 6, 7, 8)
    assert s.entry == pytest.approx(103.01)
    assert s.stop == pytest.approx(101 - CFG.stop_buffer)


def test_short_is_the_mirror_of_long():
    rows = [(-lo, -hi) for hi, lo in LONG]
    rows = [(200 + a, 200 + b) for a, b in rows]
    s = [x for x in find_setups(frame(rows), CFG) if x.direction == "short"]
    assert len(s) == 1 and s[0].entry_bar == 8
    assert s[0].entry == pytest.approx(200 - 103 - 0.01)
    assert s[0].stop == pytest.approx(200 - 101 + CFG.stop_buffer)


def test_new_high_above_zero_restarts_the_count():
    rows = LONG[:7] + [(106, 104)] + LONG[8:]   # bar 7 breaks above 0 instead of pulling back
    assert all(s.zero != 3 for s in find_setups(frame(rows), CFG))


def test_no_entry_before_pullback():
    rows = LONG[:7] + [(104, 103)] * 3          # never a lower low after 1
    assert find_setups(frame(rows), CFG) == []


def test_fixed_target_win_and_cost_in_r():
    df = frame(LONG)
    s = find_setups(df, CFG)
    t = simulate(df, s, ExitConfig(mode="fixed", target_r=1.0, cost=0.0))[0]
    assert t.outcome == "win" and t.r == pytest.approx(1.0) and t.exit_reason == "target"
    t2 = simulate(df, s, ExitConfig(mode="fixed", target_r=1.0, cost=0.211))[0]
    assert t2.r == pytest.approx(1.0 - 0.211 / s[0].risk, abs=1e-3)


def test_stop_wins_ties_when_pessimistic_and_target_when_optimistic():
    rows = LONG[:9] + [(110, 100)] + LONG[10:]    # one candle hits both stop and 2R target
    df = frame(rows)
    s = find_setups(df, CFG)
    pes = simulate(df, s, ExitConfig(mode="fixed", target_r=2.0, cost=0))[0]
    opt = simulate(df, s, ExitConfig(mode="fixed", target_r=2.0, cost=0, intrabar="optimistic"))[0]
    assert pes.exit_reason == "stop" and pes.r == pytest.approx(-1.0)
    assert opt.exit_reason == "target" and opt.r == pytest.approx(2.0)


def test_partial_then_runner_exits_on_close_below_ema():
    rows = LONG + [(113, 111), (113, 101.5, 101.6), (102, 101, 101.5)]  # closes far below EMA
    df = frame(rows)
    s = find_setups(df, CFG)
    t = simulate(df, s, ExitConfig(mode="partial", target_r=2.0, cost=0, breakeven_after_partial=False))[0]
    assert t.exit_reason == "ema"
    assert t.exit_price == pytest.approx(101.6)          # first close below the EMA
    runner = (101.6 - s[0].entry) / s[0].risk
    assert t.r == pytest.approx(0.5 * 2.0 + 0.5 * runner, abs=1e-4)


def test_trend_filter_blocks_long_below_ema():
    df = frame(LONG)
    df.loc[:, "ema"] = 1000.0
    assert find_setups(df, StrategyConfig(zero_lookback=3, trend="side", min_risk_atr=0, max_risk_atr=100)) == []


def test_sessions_use_utc_hours():
    assert session_of(pd.Timestamp("2026-03-02 04:30", tz="UTC")) == "asia"
    assert session_of(pd.Timestamp("2026-03-02 08:00", tz="UTC")) == "london"
    assert session_of(pd.Timestamp("2026-03-02 13:00", tz="UTC")) == "new_york"


def test_summary_statistics():
    df = frame(LONG)
    trades = simulate(df, find_setups(df, CFG), ExitConfig(mode="fixed", target_r=1.0, cost=0))
    s = summarize(trades)
    assert s["trades"] == 1 and s["win_rate"] == 1.0 and s["total_r"] == pytest.approx(1.0)


def test_key_levels_have_no_lookahead():
    idx = pd.date_range("2026-03-02", periods=288 * 3, freq="5min", tz="UTC")
    price = 100 + np.sin(np.arange(len(idx)) / 20)
    df = pd.DataFrame({"open": price, "high": price + 0.2, "low": price - 0.2, "close": price, "volume": 1.0}, index=idx)
    lv = KeyLevels(df)
    t = pd.Timestamp("2026-03-03 03:00", tz="UTC")
    kinds = {k for _, k in lv.active(t)}
    assert "prev_day_high" in kinds and "asia_high" not in kinds    # today's Asia range isn't finished
    day2 = df.loc["2026-03-03"]
    assert all(p != day2.high.max() for p, k in lv.active(t) if k == "prev_day_high")
