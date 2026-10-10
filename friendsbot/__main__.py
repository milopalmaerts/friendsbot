"""Command line: python -m friendsbot <command>"""
from __future__ import annotations

import argparse
import json

from . import data


def main(argv=None):
    p = argparse.ArgumentParser(prog="friendsbot", description="Second-entry gold strategy toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download and merge the public candle histories")
    f.add_argument("--all", action="store_true", help="also the 1-minute gold, US100 and BTC data (data/cache/)")
    sub.add_parser("timeframes", help="compare 1m…4h on gold, US100 and BTCUSDT")
    sub.add_parser("portfolio", help="one bot over several markets, sessions chosen on Feb–Jun only")
    sub.add_parser("paper", help="update the paper-trading journal with the latest candles")
    r = sub.add_parser("research", help="session comparison, dev/test split, parameter grid")
    r.add_argument("--csv", default=str(data.DEFAULT_CSV))
    s = sub.add_parser("scan", help="paper signals: setups forming on the latest candle")
    s.add_argument("--csv", default=str(data.DEFAULT_CSV))
    sub.add_parser("journal", help="summarise data/journal/data_final.xlsx")
    sub.add_parser("site", help="build the website from the reports")
    a = p.parse_args(argv)

    if a.cmd == "fetch":
        for name in (data.DATASETS if a.all else ["xauusd_5m"]):
            df = data.fetch(name)
            print(f"{name}: {len(df)} candles {df.index[0]} → {df.index[-1]}")
    elif a.cmd == "research":
        from .research import run
        res = run(a.csv)
        for w, row in res["friend"].items():
            p_ = row["pessimistic"]
            print(f"{w:14s} dev {p_['dev']['trades']:4d} trades {p_['dev']['total_r']:+7.1f}R | "
                  f"test {p_['test']['trades']:4d} trades {p_['test']['total_r']:+7.1f}R")
        print("written to reports/results.json")
    elif a.cmd == "timeframes":
        from .timeframes import run
        run()
        print("written to reports/timeframes.json")
    elif a.cmd == "portfolio":
        from .portfolio import run
        res = run()
        for k, p_ in res["portfolios"].items():
            print(f"{k:22s} dev {p_['dev']['total_r']:+7.1f}R  test {p_['test']['total_r']:+7.1f}R")
    elif a.cmd == "paper":
        from .paper import update
        j = update()
        print(json.dumps(j["summary"], indent=1))
    elif a.cmd == "scan":
        from .backtest import FRIEND_WINDOW, in_window
        from .research import FRIEND_RULES
        from .strategy import pending, with_indicators
        cfg = FRIEND_RULES[0]
        df = with_indicators(data.load_candles(a.csv), cfg)
        last = df.index[-1]
        print(f"last candle {last} · journal window: {'yes' if in_window(last, FRIEND_WINDOW) else 'no'}")
        print(json.dumps(pending(df, cfg), indent=1))
    elif a.cmd == "journal":
        from . import journal
        print(json.dumps(journal.summary(journal.load()), indent=1))
    elif a.cmd == "site":
        from website.build import build
        print(build())


if __name__ == "__main__":
    main()
