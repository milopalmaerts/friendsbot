"""Second-entry pattern detection on 5-minute candles.

Long setup, as described by the strategy's author ("reverse for a short"):

    0  a candle with no other candle above it (the swing high the pullback starts from)
    1  first candle whose high breaks the previous candle's high, while staying below 0
    P  pullback: a candle that goes below the one before it (lower low by default)
    2  entry: the next candle whose high breaks the previous candle's high.
       We buy on a stop order one tick above that previous high.

The stop goes under the pullback low (lowest low between 1 and the entry candle).
Every number below is a parameter, so filters can be switched on and off and tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

TICK = 0.01


@dataclass(frozen=True)
class StrategyConfig:
    ema_period: int = 21
    atr_period: int = 14
    zero_lookback: int = 12          # 0 must be the highest high of this many candles
    max_setup_bars: int = 18         # 0 → 2 must complete within this many candles
    pullback_rule: str = "low"       # "low": P makes a lower low; "high": P makes a lower high
    stop_ref: str = "pullback"       # "pullback": low since 1; "swing": low since 0
    stop_buffer: float = 0.10        # dollars beyond the stop reference
    min_risk_atr: float = 0.3        # skip setups with a stop tighter than this × ATR
    max_risk_atr: float = 3.0        # skip setups with a stop wider than this × ATR
    trend: str = "side"              # none | side | slope | side+slope   (EMA 21 trend filter)
    slope_bars: int = 3
    ema_touch_atr: float | None = None   # pullback must come within this × ATR of the EMA
    level_atr: float | None = None       # pullback must come within this × ATR of a key level
    directions: tuple[str, ...] = ("long", "short")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Setup:
    direction: str
    zero: int
    one: int
    pullback: int
    entry_bar: int
    entry: float
    stop: float
    extreme: int            # bar of the pullback extreme the stop sits beyond
    tags: dict = field(default_factory=dict)

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - prev).abs(), (df.low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def with_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    out = df.copy()
    out["ema"] = ema(out.close, cfg.ema_period)
    out["atr"] = atr(out, cfg.atr_period)
    return out


def _mirror(df: pd.DataFrame) -> pd.DataFrame:
    """Flip prices so a short setup becomes a long setup on the mirrored series."""
    m = pd.DataFrame(index=df.index)
    m["open"], m["close"] = -df.open, -df.close
    m["high"], m["low"] = -df.low, -df.high
    m["ema"] = -df.ema
    m["atr"] = df.atr
    return m


def _scan_long(h, l, o, cfg: StrategyConfig):
    """Yield (zero, one, pullback, entry_bar, entry_price, extreme_bar) for long setups."""
    n = len(h)
    lb = cfg.zero_lookback
    zero = one = pb = -1
    for i in range(lb, n):
        if zero >= 0 and i - zero > cfg.max_setup_bars:
            zero = one = pb = -1
        if pb >= 0 and h[i] > h[i - 1]:
            # 2: the high of the previous candle breaks → stop-order entry
            trigger = h[i - 1] + TICK
            fill = max(o[i], trigger)
            start = one + 1 if cfg.stop_ref == "pullback" else zero + 1
            extreme = start + int(np.argmin(l[start:i]))
            yield zero, one, pb, i, fill, extreme
            zero = one = pb = -1
            continue
        if (zero < 0 and h[i] >= h[i - lb:i].max()) or (zero >= 0 and h[i] > h[zero]):
            # 0: no candle above it; a higher high always restarts the count
            zero, one, pb = i, -1, -1
        elif zero < 0:
            continue
        elif one < 0:
            if h[i] > h[i - 1]:
                one = i
        elif pb < 0:
            went_down = l[i] < l[i - 1] if cfg.pullback_rule == "low" else h[i] < h[i - 1]
            if went_down:
                pb = i


def find_setups(df: pd.DataFrame, cfg: StrategyConfig = StrategyConfig(), levels=None) -> list[Setup]:
    """Return setups in time order. `df` must come from `with_indicators`.
    `levels` is an optional KeyLevels used by the key-level filter."""
    setups: list[Setup] = []
    for direction in cfg.directions:
        d = df if direction == "long" else _mirror(df)
        h, l, o = d.high.to_numpy(), d.low.to_numpy(), d.open.to_numpy()
        e, a, c = d.ema.to_numpy(), d.atr.to_numpy(), d.close.to_numpy()
        sign = 1 if direction == "long" else -1
        for zero, one, pb, m, fill, ext in _scan_long(h, l, o, cfg):
            stop = l[ext] - cfg.stop_buffer
            risk = fill - stop
            k = m - 1  # last completed candle before the entry candle: no look-ahead
            if not (cfg.min_risk_atr * a[k] <= risk <= cfg.max_risk_atr * a[k]):
                continue
            if "side" in cfg.trend and not c[k] > e[k]:
                continue
            if "slope" in cfg.trend and not e[k] > e[k - cfg.slope_bars]:
                continue
            ema_dist = (l[ext] - e[ext]) / a[ext]
            if cfg.ema_touch_atr is not None and ema_dist > cfg.ema_touch_atr:
                continue
            tags = {"ema_dist_atr": round(float(ema_dist), 2)}
            if levels is not None:
                price = sign * l[ext]
                near = levels.nearest(df.index[k], price)
                tags["level_dist_atr"] = None if near is None else round(abs(near - price) / a[k], 2)
                tags["level"] = near
                if cfg.level_atr is not None and (near is None or abs(near - price) > cfg.level_atr * a[k]):
                    continue
            elif cfg.level_atr is not None:
                raise ValueError("level_atr filter needs key levels")
            setups.append(Setup(direction, zero, one, pb, m, sign * fill, sign * stop, ext, tags))
    setups.sort(key=lambda s: (s.entry_bar, s.direction))
    return setups
