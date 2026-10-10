"""Trade simulation on 5-minute candles.

Inside one candle we cannot see whether the stop or the target was touched first, so the
default is pessimistic: the stop wins any tie, including on the entry candle itself.
`intrabar="optimistic"` flips that, so every report can show both bounds.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace

import numpy as np
import pandas as pd

from .strategy import Setup

# Fixed UTC windows (no daylight-saving shift) so every session is comparable.
SESSIONS = {
    "asia": (0, 7),
    "london": (7, 12),
    "new_york": (12, 17),
    "late": (17, 22),
}
# The journal's TIME/CHINA column runs 12:15–14:25 China time (UTC+8): 04:00–06:30 UTC.
FRIEND_WINDOW = (4.0, 6.5)


def session_of(t: pd.Timestamp) -> str:
    h = t.hour + t.minute / 60
    for name, (a, b) in SESSIONS.items():
        if a <= h < b:
            return name
    return "off"


def in_window(t: pd.Timestamp, window: tuple[float, float]) -> bool:
    h = t.hour + t.minute / 60
    return window[0] <= h < window[1]


@dataclass(frozen=True)
class ExitConfig:
    mode: str = "partial"           # fixed | partial | ema | adaptive
    # adaptive (the author's own rule): everything at target_r, unless the setup is tagged
    # "good" (start of trend / key level): then partial_frac at target_r and a runner.
    target_r: float = 2.0           # fixed: full exit; partial: first target
    partial_frac: float = 0.5       # share closed at target_r in partial mode
    breakeven_after_partial: bool = True
    cost: float = 0.30              # round-trip spread + slippage in price units (gold: $ per ounce)
    cost_pct: float = 0.0           # extra round-trip cost as a fraction of price (crypto fees)
    max_hold_bars: int = 96         # 8 hours
    intrabar: str = "pessimistic"   # pessimistic | optimistic

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def label(self) -> str:
        if self.mode == "fixed":
            return f"{self.target_r:g}R"
        if self.mode == "adaptive":
            return f"{self.target_r:g}R, goede setup: {int(self.partial_frac * 100)}% op {self.target_r:g}R + runner"
        if self.mode == "partial":
            be = " + BE" if self.breakeven_after_partial else ""
            return f"{int(self.partial_frac * 100)}% at {self.target_r:g}R, runner to EMA{be}"
        return "exit on EMA close"


@dataclass
class Trade:
    direction: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry: float
    stop: float
    exit_price: float
    r_gross: float
    r: float                # net of costs
    outcome: str            # win | loss | breakeven
    exit_reason: str
    session: str
    bars_held: int
    entry_bar: int
    exit_bar: int
    zero_bar: int
    one_bar: int
    pullback_bar: int
    tags: dict

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_time"] = self.entry_time.isoformat()
        d["exit_time"] = self.exit_time.isoformat()
        return d


def for_setup(ex: ExitConfig, setup) -> ExitConfig:
    """Resolve the adaptive exit into a concrete one for this setup."""
    if ex.mode != "adaptive":
        return ex
    return replace(ex, mode="partial" if setup.tags.get("good") else "fixed")


def trade_cost(ex: ExitConfig, price: float) -> float:
    return ex.cost + ex.cost_pct * abs(price)


def simulate(df: pd.DataFrame, setups: list[Setup], exit_cfg: ExitConfig = ExitConfig(),
             session_filter=None) -> list[Trade]:
    """Simulate setups one position at a time. `df` must contain the `ema` column.
    `session_filter(timestamp) -> bool` decides which entries are allowed."""
    h, l, c, e = df.high.to_numpy(), df.low.to_numpy(), df.close.to_numpy(), df.ema.to_numpy()
    idx = df.index
    n = len(df)
    trades: list[Trade] = []
    busy_until = -1
    pessimistic = exit_cfg.intrabar == "pessimistic"
    for s in setups:
        m = s.entry_bar
        if m <= busy_until or (session_filter is not None and not session_filter(idx[m])):
            continue
        sign = 1 if s.direction == "long" else -1
        ex = for_setup(exit_cfg, s)
        entry, stop, risk = s.entry, s.stop, s.risk
        target = entry + sign * ex.target_r * risk
        remaining, realized, partial_done = 1.0, 0.0, False
        cur_stop = stop
        exit_bar, exit_price, reason = None, None, None
        last = min(n - 1, m + ex.max_hold_bars)
        for j in range(m, last + 1):
            # the entry candle: pessimistic assumes the low after the fill touched the stop
            if j == m and not pessimistic:
                stop_hit = (c[j] - cur_stop) * sign <= 0
            else:
                stop_hit = l[j] <= cur_stop if sign == 1 else h[j] >= cur_stop
            tgt_hit = h[j] >= target if sign == 1 else l[j] <= target
            needs_target = ex.mode == "fixed" or (ex.mode == "partial" and not partial_done)
            if stop_hit and (pessimistic or not (needs_target and tgt_hit)):
                realized += remaining * (cur_stop - entry) * sign
                remaining, exit_bar, exit_price = 0.0, j, cur_stop
                reason = "stop" if cur_stop == stop else "breakeven"
                break
            if needs_target and tgt_hit:
                if ex.mode == "fixed":
                    realized += remaining * (target - entry) * sign
                    remaining, exit_bar, exit_price, reason = 0.0, j, target, "target"
                    break
                realized += ex.partial_frac * (target - entry) * sign
                remaining -= ex.partial_frac
                partial_done = True
                if ex.breakeven_after_partial:
                    cur_stop = entry
                continue  # runner is judged on later candle closes
            runner_active = ex.mode == "ema" or (ex.mode == "partial" and partial_done)
            if runner_active and j > m and (c[j] - e[j]) * sign < 0:
                realized += remaining * (c[j] - entry) * sign
                remaining, exit_bar, exit_price, reason = 0.0, j, c[j], "ema"
                break
            if j + 1 < n and idx[j + 1] - idx[j] > pd.Timedelta(minutes=30):
                break  # never hold through the daily break or the weekend
        if exit_bar is None:
            exit_bar = j
            exit_price = c[j]
            realized += remaining * (c[j] - entry) * sign
            reason = "time"  # closed at the candle close
        r_gross = realized / risk
        r_net = r_gross - trade_cost(ex, entry) / risk
        outcome = "win" if r_net > 0.05 else "loss" if r_net < -0.05 else "breakeven"
        trades.append(Trade(s.direction, idx[m], idx[exit_bar], entry, stop, float(exit_price),
                            round(float(r_gross), 4), round(float(r_net), 4), outcome, reason,
                            session_of(idx[m]), exit_bar - m, m, exit_bar, s.zero, s.one, s.pullback, s.tags))
        busy_until = exit_bar
    return trades


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": None, "total_r": 0.0, "avg_r": None, "profit_factor": None,
                "max_drawdown_r": 0.0, "ci_low": None, "ci_high": None}
    r = np.array([t.r for t in trades])
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    equity = np.cumsum(r)
    dd = float((np.maximum.accumulate(np.concatenate([[0], equity]))[1:] - equity).max())
    rng = np.random.default_rng(7)
    boots = rng.choice(r, size=(2000, len(r))).mean(axis=1)
    return {
        "trades": int(len(r)),
        "win_rate": round(float((r > 0.05).mean()), 4),
        "total_r": round(float(r.sum()), 2),
        "avg_r": round(float(r.mean()), 3),
        "profit_factor": round(float(wins / losses), 2) if losses > 0 else None,
        "max_drawdown_r": round(dd, 2),
        "ci_low": round(float(np.percentile(boots, 2.5)), 3),
        "ci_high": round(float(np.percentile(boots, 97.5)), 3),
    }
