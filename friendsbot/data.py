"""Candle loading, merging and validation.

The bundled history comes from the MIT-licensed 5-minute XAUUSD sample published at
github.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data. That repository
only keeps a rolling six-month window, so `fetch` walks its git history and merges
every published version (newest version wins on overlapping timestamps).
"""
from __future__ import annotations

import subprocess
import tempfile
from io import StringIO
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "data" / "xauusd_5m.csv"
SOURCE_REPO = "https://github.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data"
SOURCE_FILE = "XAUUSD_5m.csv"
COLUMNS = ["open", "high", "low", "close", "volume"]


def load_candles(path: str | Path = DEFAULT_CSV) -> pd.DataFrame:
    """Read an OHLC CSV with a `datetime` column into a UTC-indexed frame."""
    df = pd.read_csv(path)
    time_col = next(c for c in df.columns if c.lower() in ("datetime", "time", "date", "timestamp"))
    df = df.rename(columns={c: c.lower() for c in df.columns})
    idx = pd.to_datetime(df[time_col.lower()], utc=True)
    df = df.set_index(idx).drop(columns=[time_col.lower()])
    df.index.name = "time"
    if "volume" not in df:
        df["volume"] = 0.0
    df = df[COLUMNS].astype(float).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    validate(df)
    return df


def validate(df: pd.DataFrame) -> None:
    bad = (df.high < df[["open", "close"]].max(axis=1) - 1e-9) | (df.low > df[["open", "close"]].min(axis=1) + 1e-9)
    if bad.any():
        raise ValueError(f"{int(bad.sum())} candles have high/low outside open/close, first at {df.index[bad][0]}")
    if not df.index.is_monotonic_increasing:
        raise ValueError("candles are not sorted")


def merge_versions(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Merge oldest→newest; later frames override earlier ones on the same timestamp."""
    merged = pd.concat(frames)
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    validate(merged)
    return merged


def drop_partial_tail(df: pd.DataFrame, max_gap: str = "1h") -> pd.DataFrame:
    """Each published version ends with a bar that may still have been forming; drop it
    when it is isolated (more than `max_gap` after the previous bar)."""
    if len(df) > 1 and df.index[-1] - df.index[-2] > pd.Timedelta(max_gap):
        return df.iloc[:-1]
    return df


def fetch(out: str | Path = DEFAULT_CSV) -> pd.DataFrame:
    """Clone the source repository and merge every historical version of the CSV."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", SOURCE_REPO, tmp], check=True)
        commits = subprocess.run(["git", "-C", tmp, "log", "--reverse", "--format=%H", "--", SOURCE_FILE],
                                 check=True, capture_output=True, text=True).stdout.split()
        frames = []
        for sha in commits:
            text = subprocess.run(["git", "-C", tmp, "show", f"{sha}:{SOURCE_FILE}"],
                                  check=True, capture_output=True, text=True).stdout
            frames.append(drop_partial_tail(load_candles(StringIO(text))))
    merged = merge_versions(frames)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    merged.reset_index().rename(columns={"time": "datetime"}).to_csv(out, index=False, float_format="%.2f")
    return merged


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["open"])
