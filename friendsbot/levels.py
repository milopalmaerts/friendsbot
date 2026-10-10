"""Key levels (support/resistance) known at a given moment, without look-ahead.

The author draws levels by hand. As a reproducible stand-in we use the levels traders
usually draw on gold intraday charts:

* previous UTC day's high and low,
* the current day's Asia range high/low once Asia is over (00:00–07:00 UTC),
* confirmed swing highs/lows on the 1-hour chart from the last `pivot_days` days
  (a pivot is confirmed only `pivot_strength` hours after it formed).
"""
from __future__ import annotations

import bisect

import numpy as np
import pandas as pd

from .data import resample


class KeyLevels:
    def __init__(self, df: pd.DataFrame, pivot_strength: int = 3, pivot_days: int = 5):
        self.pivot_days = pd.Timedelta(days=pivot_days)
        events: list[tuple[pd.Timestamp, pd.Timestamp, float, str]] = []  # (known_from, expires, price, kind)

        daily = resample(df, "1D")
        for day, row in daily.iterrows():
            start, end = day + pd.Timedelta(days=1), day + pd.Timedelta(days=4)
            events += [(start, end, row.high, "prev_day_high"), (start, end, row.low, "prev_day_low")]

        asia = df[df.index.hour < 7]
        for day, g in asia.groupby(asia.index.normalize()):
            start, end = day + pd.Timedelta(hours=7), day + pd.Timedelta(days=1)
            events += [(start, end, g.high.max(), "asia_high"), (start, end, g.low.min(), "asia_low")]

        hourly = resample(df, "1h")
        hi, lo, k = hourly.high.to_numpy(), hourly.low.to_numpy(), pivot_strength
        for i in range(k, len(hourly) - k):
            known = hourly.index[i + k] + pd.Timedelta(hours=1)
            if hi[i] == hi[i - k:i + k + 1].max():
                events.append((known, known + self.pivot_days, hi[i], "swing_high"))
            if lo[i] == lo[i - k:i + k + 1].min():
                events.append((known, known + self.pivot_days, lo[i], "swing_low"))

        events.sort(key=lambda e: e[0])
        self._events = events
        self._starts = [e[0] for e in events]

    def active(self, t: pd.Timestamp) -> list[tuple[float, str]]:
        hi = bisect.bisect_right(self._starts, t)
        lo = bisect.bisect_left(self._starts, t - pd.Timedelta(days=5))
        return [(p, kind) for start, end, p, kind in self._events[lo:hi] if end > t]

    def nearest(self, t: pd.Timestamp, price: float) -> float | None:
        levels = self.active(t)
        if not levels:
            return None
        prices = np.array([p for p, _ in levels])
        return float(prices[np.argmin(np.abs(prices - price))])
