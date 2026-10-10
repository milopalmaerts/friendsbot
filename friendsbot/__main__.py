"""Command line: python -m friendsbot <command>"""
from __future__ import annotations

import argparse
import json
import os

from . import data


def main(argv=None):
    p = argparse.ArgumentParser(prog="friendsbot", description="Second-entry gold strategy toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download and merge the public candle histories")
    f.add_argument("--all", action="store_true", help="also the 1-minute gold, US100 and BTC data (data/cache/)")
    sub.add_parser("timeframes", help="compare 1m…4h on gold, US100 and BTCUSDT")
    sub.add_parser("portfolio", help="one bot over several markets, sessions chosen on Feb–Jun only")
    sub.add_parser("paper", help="update the paper-trading journal with the latest candles")
    sub.add_parser("sandbox", help="fetch the bot's real trades from MyFundedPerps into reports/live/sandbox.json")
    rn = sub.add_parser("run", help="always-on: react to candle closes and fills within seconds (key in FP_API_KEY)")
    rn.add_argument("--dry-run", action="store_true", help="compute orders without sending them")
    rn.add_argument("--account", help="account id (default: first active account the key can see)")
    lv = sub.add_parser("live", help="one run against MyFundedPerps (key in FP_API_KEY; sandbox for fp_test_ keys)")
    lv.add_argument("--dry-run", action="store_true", help="compute and print the orders without sending them")
    lv.add_argument("--account", help="account id (default: first active account the key can see)")
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
    elif a.cmd == "run":
        from pathlib import Path
        from .mfp import Client
        from .runner import run
        client = Client()
        if client.environment == "live" and not a.dry_run and os.environ.get("FB_ALLOW_LIVE") != "1":
            raise SystemExit("live key detected: set FB_ALLOW_LIVE=1 to trade a real challenge account")
        run(client, a.account, dry_run=a.dry_run or not client.authenticated, log_file=Path("logs/live.jsonl"))
    elif a.cmd == "live":
        from .live import tick
        from .mfp import Client
        client = Client()
        if client.environment == "live" and not a.dry_run and os.environ.get("FB_ALLOW_LIVE") != "1":
            raise SystemExit("live key detected: set FB_ALLOW_LIVE=1 to trade a real challenge account")
        plan = tick(client, account_id=a.account, dry_run=a.dry_run or not client.authenticated)
        print(json.dumps({"environment": client.environment, "dry_run": a.dry_run or not client.authenticated,
                          "notes": plan.notes, "actions": plan.actions}, indent=1, default=str))
    elif a.cmd == "sandbox":
        from .mfp import Client
        from .sandbox import update
        client = Client()
        if not client.authenticated:
            raise SystemExit("FP_API_KEY ontbreekt")
        d = update(client=client)
        closed = [t for t in d["trades"] if t["status"] == "closed"]
        print(f"{d['environment']} account {d['account']['number']}: balance {d['account']['balance']:,.2f}, "
              f"{len(closed)} gesloten en {len(d['trades']) - len(closed)} open trades van de bot")
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
