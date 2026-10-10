# Third-party data

## `data/xauusd_5m.csv`
- Source: https://github.com/getdata-finance/xauusd-5m-ohlcv-metals-historical-data (MIT licence)
- Content: 5-minute XAUUSD OHLCV, timestamps in UTC, 2026-02-02 → 2026-09-25 (46,419 candles).
- The source repository keeps a rolling six-month sample. `python -m friendsbot fetch` walks its git
  history and merges every published version; the newest version wins on overlapping timestamps.
- Checked against an independent feed (github.com/Sai310421/xauusd-data, M5, Feb–May 2026):
  5-minute returns correlate 0.986 at zero time offset, confirming the UTC timestamps.
- This is a data-vendor feed, not your friend's broker (Capital.com / FXCM on the screenshots).
  Individual wicks can differ by a few tenths of a dollar between feeds.

## `data/journal/data_final.xlsx`
Your friend's trade journal. It is git-ignored and stays local only; put the file at this path to
rerun the journal analysis.
