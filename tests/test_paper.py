import pandas as pd

from friendsbot import paper
from friendsbot.backtest import Trade


def trade(t, r, asset="xauusd", tf="15m", session="asia"):
    t = pd.Timestamp(t, tz="UTC")
    return Trade("long", t, t + pd.Timedelta(minutes=30), 100, 99, 100 + r, r, r, "win" if r > 0 else "loss",
                 "stop", session, 6, 0, 6, 0, 0, 0, {"asset": asset, "tf": tf, "session": session})


def test_selection_only_uses_the_lookback_before_the_month():
    good_before = [trade(f"2026-05-{d:02d} 04:00", 1.0) for d in range(1, 26)]
    bad_after = [trade(f"2026-06-{d:02d} 04:00", -1.0) for d in range(1, 26)]
    worse = [trade(f"2026-05-{d:02d} 05:00", -1.0, tf="1h") for d in range(1, 26)]
    cells = {("xauusd", "15m", paper.EXIT, "asia"): good_before + bad_after,
             ("xauusd", "1h", paper.EXIT, "asia"): worse}
    chosen = paper.select(cells, pd.Timestamp("2026-06-01", tz="UTC"))
    assert [(c["asset"], c["session"], c["tf"]) for c in chosen] == [("xauusd", "asia", "15m")]
    # a month later the losses of June are in the lookback and the cell drops out
    assert paper.select(cells, pd.Timestamp("2026-07-01", tz="UTC")) == []


def test_selection_needs_enough_trades():
    few = [trade(f"2026-05-{d:02d} 04:00", 2.0) for d in range(1, 10)]
    assert paper.select({("xauusd", "15m", paper.EXIT, "asia"): few}, pd.Timestamp("2026-06-01", tz="UTC")) == []


def test_trade_rows_mark_source_and_open_status():
    sim = paper._trade_row(trade("2026-09-01 04:00", 1.0), 0.25, False, pd.Timestamp("2026-10-01", tz="UTC"))
    assert sim["source"] == "simulatie" and sim["status"] == "closed"
    t = trade("2026-10-12 04:00", 0.0)
    t.exit_reason = "time"
    row = paper._trade_row(t, 0.5, True, t.exit_time)
    assert row["source"] == "papier" and row["status"] == "open"


def test_summary_of_rows():
    rows = [paper._trade_row(trade(f"2026-09-0{d} 04:00", r), 0.25, False, pd.Timestamp("2026-10-01", tz="UTC"))
            for d, r in [(1, 2.5), (2, -1.0), (3, -1.0)]]
    s = paper.summarize_rows(rows)
    assert s["trades"] == 3 and s["win_rate"] == round(1 / 3, 3) and s["avg_win_r"] == 2.5 and s["avg_loss_r"] == -1.0


def test_friend_window_competes_with_asia_for_the_same_slot():
    asia = [trade(f"2026-05-{d:02d} 03:00", 0.5) for d in range(1, 26)]
    window = [trade(f"2026-05-{d:02d} 04:30", 1.0, tf="5m", session="friend_window") for d in range(1, 26)]
    cells = {("xauusd", "15m", paper.EXIT, "asia"): asia, ("xauusd", "5m", paper.EXIT, "friend_window"): window}
    chosen = paper.select(cells, pd.Timestamp("2026-06-01", tz="UTC"))
    assert [(c["session"], c["tf"]) for c in chosen] == [("friend_window", "5m")]


def book(trades, ranks, **kw):
    return paper.run_book([(t, c, "2026-06") for t, c in trades], {"2026-06": ranks}, **kw)


def test_hot_cell_gets_double_risk_and_is_demoted_after_three_losses():
    a, b = "xauusd|15m|asia", "nas100|15m|asia"
    seq = [(trade(f"2026-06-0{d} 04:00", -1.0), a) for d in range(1, 5)] + [(trade("2026-06-05 04:00", 1.0, asset="nas100"), b)]
    taken, _ = book(seq, [a, b], hot_n=1, asset_day_losses=99, max_open=None)
    risks = [(c, r) for _, c, r, _ in taken]
    assert risks[:3] == [(a, 0.5)] * 3          # hot until the third loss is known
    assert risks[3] == (a, 0.25)                # demoted
    assert risks[4] == (b, 0.5)                 # next in rank takes over


def test_market_stops_for_the_day_after_two_losses():
    seq = [(trade(f"2026-06-01 0{h}:00", -1.0, asset="btcusdt"), "btcusdt|15m|asia") for h in (1, 2, 3)]
    seq.append((trade("2026-06-01 05:00", 1.0), "xauusd|15m|asia"))
    taken, skipped = book(seq, [], max_open=None)
    assert [t.tags["asset"] for t, *_ in taken] == ["btcusdt", "btcusdt", "xauusd"]
    assert skipped == {"dagstop markt": 1}


def test_max_open_positions():
    long_trades = []
    for h in range(4):
        t = trade(f"2026-06-01 0{h}:00", 1.0)
        t.exit_time = pd.Timestamp("2026-06-01 09:00", tz="UTC")
        long_trades.append((t, f"c{h}"))
    taken, skipped = book(long_trades, [], max_open=3)
    assert len(taken) == 3 and skipped == {"max. posities": 1}
