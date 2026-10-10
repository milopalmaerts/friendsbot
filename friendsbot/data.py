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


CACHE = ROOT / "data" / "cache"
GETDATA = "https://github.com/getdata-finance/"
# name: (repository, file inside it, all versions in git history?)
DATASETS = {
    "xauusd_5m": (SOURCE_REPO, SOURCE_FILE, True),
    "xauusd_1m": (GETDATA + "xauusd-1m-ohlcv-metals-historical-data", "XAUUSD_1m.csv", True),
    "nas100_1m": (GETDATA + "nas100-1m-ohlcv-index-historical-data", "NAS100_1m.csv", True),
    # Bitstamp BTC/USD, used as the BTCUSDT proxy (same price to within a few dollars)
    "btcusd_1m": ("https://github.com/ff137/bitstamp-btcusd-minute-data",
                  "data/updates/btcusd_bitstamp_1min_latest.csv", False),
}


def _git_versions(repo: str, path: str, all_versions: bool) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", repo, tmp], check=True)
        commits = subprocess.run(["git", "-C", tmp, "log", "--reverse", "--format=%H", "--", path],
                                 check=True, capture_output=True, text=True).stdout.split()
        if not all_versions:
            commits = commits[-1:]
        return [subprocess.run(["git", "-C", tmp, "show", f"{sha}:{path}"], check=True,
                               capture_output=True, text=True).stdout for sha in commits]


def _read_any(text: str) -> pd.DataFrame:
    head = text[:200].split("\n", 1)[0].lower()
    if head.startswith("timestamp"):
        df = pd.read_csv(StringIO(text))
        df["datetime"] = pd.to_datetime(df.pop("timestamp"), unit="s", utc=True)
        return load_candles(StringIO(df.to_csv(index=False)))
    return load_candles(StringIO(text))


def fetch(name: str = "xauusd_5m", out: str | Path | None = None) -> pd.DataFrame:
    """Clone the source repository and merge every published version of the CSV
    (newest wins). The 5-minute gold file is kept in git; the rest go to data/cache/."""
    repo, path, all_versions = DATASETS[name]
    frames = [drop_partial_tail(_read_any(t)) for t in _git_versions(repo, path, all_versions)]
    merged = merge_versions(frames)
    if out is None:
        out = DEFAULT_CSV if name == "xauusd_5m" else CACHE / f"{name}.csv.gz"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    merged.reset_index().rename(columns={"time": "datetime"}).to_csv(out, index=False, float_format="%.2f")
    return merged


def load_dataset(name: str) -> pd.DataFrame:
    """Load a dataset, fetching it on first use."""
    path = DEFAULT_CSV if name == "xauusd_5m" else CACHE / f"{name}.csv.gz"
    if not Path(path).exists():
        return fetch(name)
    return load_candles(path)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["open"])
