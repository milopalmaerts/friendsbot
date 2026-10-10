"""Execute signals from any timeframe on 1-minute candles.

Signals (the 0-1-P-2 pattern, EMA, ATR filters) come from the chart timeframe. The fill,
stop, 2R target and the runner are then followed minute by minute, so a 1h or 4h candle
that touched both stop and target is resolved by what happened first inside it. Only a
tie inside a single 1-minute candle still falls back to the `intrabar` assumption.
The runner exits on a close of the *chart* timeframe through its EMA, as on the chart.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest import ExitConfig, Trade, session_of, trade_cost
from .strategy import Setup


def simulate_fine(tf: pd.DataFrame, fine: pd.DataFrame, setups: list[Setup], ex: ExitConfig = ExitConfig(),
                  session_filter=None, max_hold: pd.Timedelta = pd.Timedelta(days=5)) -> list[Trade]:
    """`tf` is the chart timeframe with `ema`; `fine` the 1-minute candles it was built from."""
    step = tf.index[1] - tf.index[0] if len(tf) > 1 else pd.Timedelta(minutes=1)
    step = pd.Series(tf.index).diff().mode().iloc[0] if len(tf) > 2 else step
    ft = fine.index.asi8
    fo, fh, fl, fc = (fine[c].to_numpy() for c in ("open", "high", "low", "close"))
    tf_t = tf.index.asi8
    tf_c, tf_e = tf.close.to_numpy(), tf.ema.to_numpy()
    # chart bar each minute belongs to, and whether it is the last minute of that bar
    bucket = np.searchsorted(tf_t, ft, side="right") - 1
    last_of_bar = np.r_[bucket[1:] != bucket[:-1], True]
    gap_after = np.r_[np.diff(ft) > pd.Timedelta(minutes=30).value, True]
    pessimistic = ex.intrabar == "pessimistic"
    trades, busy_until = [], -1
    for s in setups:
        m = s.entry_bar
        t0 = tf_t[m]
        if t0 <= busy_until or (session_filter is not None and not session_filter(tf.index[m])):
            continue
        sign = 1 if s.direction == "long" else -1
        a = np.searchsorted(ft, t0)
        b = np.searchsorted(ft, t0 + step.value)
        hit = np.nonzero(fh[a:b] >= s.entry)[0] if sign == 1 else np.nonzero(fl[a:b] <= s.entry)[0]
        if len(hit) == 0:
            continue  # the 1-minute feed never traded through the trigger
        k = a + hit[0]
        entry, stop, risk = s.entry, s.stop, s.risk
        target = entry + sign * ex.target_r * risk
        remaining, realized, partial_done, cur_stop = 1.0, 0.0, False, stop
        reason, j = "time", k
        end_time = ft[k] + max_hold.value
        while j < len(ft):
            if j == k and not pessimistic:
                stop_hit = (fc[j] - cur_stop) * sign <= 0
            else:
                stop_hit = fl[j] <= cur_stop if sign == 1 else fh[j] >= cur_stop
            tgt_hit = fh[j] >= target if sign == 1 else fl[j] <= target
            needs_target = ex.mode == "fixed" or (ex.mode == "partial" and not partial_done)
            if stop_hit and (pessimistic or not (needs_target and tgt_hit)):
                realized += remaining * (cur_stop - entry) * sign
                remaining, exit_price = 0.0, cur_stop
                reason = "stop" if cur_stop == stop else "breakeven"
                break
            if needs_target and tgt_hit:
                if ex.mode == "fixed":
                    realized += remaining * (target - entry) * sign
                    remaining, exit_price, reason = 0.0, target, "target"
                    break
                realized += ex.partial_frac * (target - entry) * sign
                remaining -= ex.partial_frac
                partial_done = True
                if ex.breakeven_after_partial:
                    cur_stop = entry
                j += 1
                continue
            runner = ex.mode == "ema" or (ex.mode == "partial" and partial_done)
            bar = bucket[j]
            if runner and last_of_bar[j] and bar > m and (tf_c[bar] - tf_e[bar]) * sign < 0:
                realized += remaining * (fc[j] - entry) * sign
                remaining, exit_price, reason = 0.0, fc[j], "ema"
                break
            if gap_after[j] or ft[j] >= end_time:
                break
            j += 1
        j = min(j, len(ft) - 1)
        if remaining > 0:
            exit_price = fc[j]
            realized += remaining * (fc[j] - entry) * sign
        r_gross = realized / risk
        r_net = r_gross - trade_cost(ex, entry) / risk
        outcome = "win" if r_net > 0.05 else "loss" if r_net < -0.05 else "breakeven"
        entry_time, exit_time = fine.index[k], fine.index[j]
        exit_bar = int(bucket[j])
        trades.append(Trade(s.direction, entry_time, exit_time, entry, stop, float(exit_price),
                            round(float(r_gross), 4), round(float(r_net), 4), outcome, reason,
                            session_of(tf.index[m]), exit_bar - m, m, exit_bar, s.zero, s.one, s.pullback, s.tags))
        busy_until = ft[j]
    return trades
