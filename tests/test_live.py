import json

import numpy as np
import pandas as pd
import pytest

from friendsbot import live
from friendsbot.research import FRIEND_RULES

CFG = FRIEND_RULES[0]
MARKET = "binance|XAUUSDT"
NOW = pd.Timestamp("2026-03-02 06:21", tz="UTC")


@pytest.fixture(autouse=True)
def isolate_ownership_files(tmp_path,monkeypatch):
    monkeypatch.setattr(live,"ENTRY_LINKS",tmp_path/"links.json")
    monkeypatch.setattr(live,"SANDBOX_JOURNAL",tmp_path/"sandbox.json")


def bars_with_setup():
    """A steady uptrend, a push to 0, a pullback, 1 and P: the next candle's stop entry is at 103.81."""
    pre = [(80 + i * 0.3 + 1, 80 + i * 0.3) for i in range(70)]
    top = pre[-1][0]
    rows = pre + [(top + 1, top), (top + 3, top + 1.5), (top + 2.5, top + 1), (top + 2, top + 0.6),
                  (top + 2.4, top + 1.2), (top + 2.1, top + 0.4)]
    idx = pd.date_range("2026-03-02 00:00", periods=len(rows), freq="5min", tz="UTC")
    hi = np.array([r[0] for r in rows]); lo = np.array([r[1] for r in rows]); cl = hi * 0.7 + lo * 0.3
    op = np.r_[cl[0], cl[:-1]].clip(lo, hi)
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": cl, "volume": 1.0}, index=idx)


@pytest.fixture
def journal(tmp_path):
    p = tmp_path / "journal.json"
    p.write_text(json.dumps({"selections": [{"month": "2026-03", "cells": [
        {"asset": "xauusd", "session": "asia", "tf": "5m", "avg_r": 0.5, "rank": 1, "hot": True}]}]}))
    return p


class FakeClient:
    authenticated = True
    environment = "test"

    def __init__(self, equity=100_000, positions=(), working=(), recent=(), closed=(), risk=None):
        self.equity = equity
        self._positions, self._working, self._recent, self._closed = list(positions), list(working), list(recent), list(closed)
        self.risk = risk or {"equity": equity, "daily_loss_room": 3000, "max_drawdown_room": 6000}
        self.calls = []

    def markets(self):
        return {MARKET: {"market_id": MARKET, "size_step": 0.001, "min_size": 0.001, "min_notional": 10, "max_leverage": 20}}

    def accounts(self):
        return [{"id": "other", "account_number": "FP-1", "status": "active"},
                {"id": "acc1", "account_number": "FP-16539224", "status": "active"}]

    def account(self, account_id):
        return {"id": account_id, "balance": self.equity, "risk": self.risk}

    def positions(self, account_id, status="open", limit=100):
        return self._positions if status == "open" else self._closed

    def orders(self, account_id, status=None, limit=100):
        return self._working if status == "working" else self._recent

    def paged(self, path, query):
        return self._recent

    def place_order(self, body):
        self.calls.append(("place", body))
        return {"id": "o-new", **body}

    def cancel_order(self, order_id):
        self.calls.append(("cancel", order_id))

    def set_exits(self, position_id, body):
        self.calls.append(("exits", position_id, body))

    def close_position(self, position_id, client_order_id=None):
        self.calls.append(("close", position_id))


def run(client, journal, bars=None):
    return live.tick(client, now=NOW, journal_path=journal, bars={("xauusd", "5m"): bars if bars is not None else bars_with_setup()})


def test_places_stop_entry_sized_to_risk(journal):
    c = FakeClient()
    run(c, journal)
    (kind, body), = c.calls
    assert kind == "place" and body["type"] == "stop_market" and body["side"] == "buy"
    assert body["trigger_price"] == pytest.approx(103.81)
    stop = live.parse_client_id(body["client_order_id"])["stop"]
    assert stop == pytest.approx(102.0)
    # hot combination: risk = equity × HOT_RISK_PCT, size rounded down to the size step
    expected = np.floor(100_000 * live.HOT_RISK_PCT / 100 / (103.81 - 102.0) * 1000) / 1000
    assert body["size"] == pytest.approx(expected)
    assert body["account_id"] == "acc1"          # the pinned account, not the first one listed


def test_trades_nothing_without_the_pinned_account(journal, monkeypatch):
    monkeypatch.setattr(live, "ACCOUNT", "FP-99999999")
    c = FakeClient()
    plan = run(c, journal)
    assert c.calls == [] and any("niet gevonden" in n for n in plan.notes)


def test_keeps_identical_working_entry_and_replaces_a_moved_one(journal):
    first = FakeClient()
    run(first, journal)
    cid = first.calls[0][1]["client_order_id"]
    same = FakeClient(working=[{"id": "w1", "client_order_id": cid, "market_id": MARKET, "reduce_only": False,
                                "trigger_price": 103.81, "size": first.calls[0][1]["size"]}])
    run(same, journal)
    assert same.calls == []
    moved_cid = cid.rsplit("|", 1)[0] + "|104.5"
    moved = FakeClient(working=[{"id": "w1", "client_order_id": moved_cid, "market_id": MARKET, "reduce_only": False,
                                 "trigger_price": 104.5, "size": 1.0}])
    run(moved, journal)
    assert [k for k, *_ in moved.calls] == ["cancel", "place"]


def test_no_entries_close_to_the_daily_loss_floor(journal):
    c = FakeClient(risk={"equity": 100_000, "daily_loss_room": 100, "max_drawdown_room": 6000})
    plan = run(c, journal)
    assert c.calls == [] and any("daglimiet" in n for n in plan.notes)


def entry_order_filled(stop=102.0, trigger=103.81, size=10.0):
    cid = live.client_id("xauusd", "5m", "asia", "long", pd.Timestamp("2026-03-02 05:55", tz="UTC"), stop, trigger)
    return {"id": "e1", "client_order_id": cid, "market_id": MARKET, "status": "filled", "reduce_only": False,
            "requested_at": int(pd.Timestamp("2026-03-02 06:00", tz="UTC").timestamp() * 1000),
            "size": size, "filled_size": size}


def position(size=10.0, entry=103.81):
    return {"id": "p1", "market_id": MARKET, "side": "long", "size": size, "entry_price": entry,
            "opened_at": int(pd.Timestamp("2026-03-02 06:01", tz="UTC").timestamp() * 1000)}


def test_new_position_gets_stop_and_half_take_profit_at_2r(journal):
    c = FakeClient(positions=[position()], recent=[entry_order_filled()])
    run(c, journal)
    exits = [x for x in c.calls if x[0] == "exits"]
    assert len(exits) == 1
    ops = exits[0][2]["operations"]
    sl = next(o for o in ops if o["group"] == "sl"); tp = next(o for o in ops if o["group"] == "tp")
    assert sl["price"] == pytest.approx(102.0) and sl["size"] == 10.0
    assert tp["price"] == pytest.approx(103.81 + 2 * 1.81) and tp["size"] == 5.0
    assert not any(k == "place" for k, *_ in c.calls)     # market already has a position


def test_after_take_profit_stop_moves_to_breakeven_without_using_entry_ema(journal):
    sl = {"id": "s1", "market_id": MARKET, "reduce_only": True, "type": "stop_market", "trigger_price": 102.0, "size": 5.0}
    falling = bars_with_setup()
    falling.iloc[-1, falling.columns.get_loc("close")] = 90.0     # last close far below the EMA
    falling.iloc[-1, falling.columns.get_loc("low")] = 90.0
    c = FakeClient(positions=[position(size=5.0)], working=[sl], recent=[entry_order_filled()])
    run(c, journal, bars=falling)
    kinds = [x[0] for x in c.calls]
    assert "exits" in kinds and "close" not in kinds
    modify = next(x for x in c.calls if x[0] == "exits")[2]["operations"][0]
    assert modify == {"kind": "modify", "order_id": "s1", "price": pytest.approx(103.81)}


def runner_bars(close=110.0):
    idx = pd.date_range(end=NOW.floor("15min")-pd.Timedelta(minutes=15), periods=80, freq="15min")
    df = pd.DataFrame({"open": 100., "high": 101., "low": 99., "close": 100., "volume": 1.}, index=idx)
    df.iloc[-1] = [100.,max(101.,close),min(99.,close),close,1.]
    return df


def runner_client(side="long", tf="5m"):
    p = position(size=5.)
    p["side"] = side
    order = entry_order_filled()
    order["client_order_id"] = live.client_id("xauusd",tf,"asia",side,
        pd.Timestamp("2026-03-02 05:55",tz="UTC"),102.,103.81)
    sl = {"id":"s1","market_id":MARKET,"reduce_only":True,"type":"stop_market",
          "trigger_price":102.,"size":5.}
    return FakeClient(positions=[p],working=[sl],recent=[order])


@pytest.mark.parametrize("side,close,tf",[("long",90.,"5m"),("short",110.,"1h"),("short",110.,"15m")])
def test_runner_uses_closed_fifteen_minute_ema_independently_of_entry_frame(journal,side,close,tf):
    c = runner_client(side,tf)
    plan = live.tick(c,now=NOW,journal_path=journal,
                     bars={("xauusd","5m"):bars_with_setup(),("xauusd","15m"):runner_bars(close)})
    assert c.calls == [("close","p1")]  # exit takes precedence over modifying the protective stop
    action = next(a for a in plan.actions if a["action"]=="close_runner")
    assert action["timeframe"] == "15m"
    assert plan.checks[0]["entry_timeframe"] == tf
    assert plan.checks[0]["reason"] == "ema_exit"


def test_five_minute_cross_alone_cannot_close_fifteen_minute_runner(journal):
    c = runner_client()
    five = bars_with_setup();five.iloc[-1,five.columns.get_loc("close")] = 90.
    plan = live.tick(c,now=NOW,journal_path=journal,
                     bars={("xauusd","5m"):five,("xauusd","15m"):runner_bars(110.)})
    assert ("close","p1") not in c.calls
    assert plan.checks[0]["reason"] == "holding_runner"


def test_forming_fifteen_minute_cross_is_filtered_before_ema(journal):
    c = runner_client()
    df = runner_bars(110.)
    df.loc[NOW.floor("15min")] = [110.,111.,50.,50.,1.]
    plan = live.tick(c,now=NOW,journal_path=journal,
                     bars={("xauusd","5m"):bars_with_setup(),("xauusd","15m"):df})
    assert ("close","p1") not in c.calls
    assert plan.checks[0]["close"] == 110.


def test_wick_through_ema_is_not_a_runner_close(journal):
    c = runner_client()
    df = runner_bars(110.);df.iloc[-1,df.columns.get_loc("low")] = 50.
    live.tick(c,now=NOW,journal_path=journal,
              bars={("xauusd","5m"):bars_with_setup(),("xauusd","15m"):df})
    assert ("close","p1") not in c.calls


def test_ema_exit_still_requires_partial_profit(journal):
    c = runner_client();c._positions[0]["size"] = 10.
    plan = live.tick(c,now=NOW,journal_path=journal,
                     bars={("xauusd","5m"):bars_with_setup(),("xauusd","15m"):runner_bars(90.)})
    assert ("close","p1") not in c.calls
    assert plan.checks[0]["reason"] == "awaiting_partial"


def test_old_runner_entry_is_recovered_beyond_the_latest_order_page(journal):
    c = runner_client();original = c._recent[0]
    original["position_id"] = "p1"
    c._recent = [{"id":f"later-{i}","client_order_id":"manual","status":"canceled"} for i in range(100)]
    c.paged = lambda path,query: [original]+c._recent
    plan = live.tick(c,now=NOW,journal_path=journal,
                     bars={("xauusd","5m"):bars_with_setup(),("xauusd","15m"):runner_bars(90.)})
    assert ("close","p1") in c.calls
    assert plan.checks[0]["reason"] == "ema_exit"


def test_order_paging_cannot_adopt_a_manual_position_from_an_unrelated_old_fill(journal):
    c=runner_client();old=c._recent[0];c._recent=[]
    c.paged=lambda path,query:[old]
    plan=live.tick(c,now=NOW,journal_path=journal,
                   bars={("xauusd","5m"):bars_with_setup(),("xauusd","15m"):runner_bars(90.)})
    assert c.calls==[]
    assert plan.checks[0]["reason"]=="unmatched_entry"


def test_saved_entry_identity_survives_order_history_rollover(journal):
    c=runner_client()
    frames={("xauusd","5m"):bars_with_setup(),("xauusd","15m"):runner_bars(110.)}
    live.tick(c,now=NOW,journal_path=journal,bars=frames)
    c.calls.clear();c._recent=[]
    frames[("xauusd","15m")]=runner_bars(90.)
    live.tick(c,now=NOW,journal_path=journal,bars=frames)
    assert ("close","p1") in c.calls


def test_recorded_bot_journal_can_recover_an_older_tagged_entry(journal):
    c=runner_client();original=c._recent[0];c._recent=[]
    c.paged=lambda path,query:[original]
    row={"id":"p1","entry_time":pd.Timestamp(c._positions[0]["opened_at"],unit="ms",tz="UTC").isoformat(),
         "asset":"xauusd","direction":"long","tf":"5m","session":"asia","stop":102.}
    live.SANDBOX_JOURNAL.write_text(json.dumps({"account":{"id":"acc1"},"environment":"test","trades":[row]}))
    live.tick(c,now=NOW,journal_path=journal,
              bars={("xauusd","5m"):bars_with_setup(),("xauusd","15m"):runner_bars(90.)})
    assert ("close","p1") in c.calls


@pytest.mark.parametrize("account,environment",[("different-account","test"),("acc1","live")])
def test_ownership_cache_from_another_account_or_environment_is_ignored(journal,account,environment):
    c=runner_client();order=c._recent[0];c._recent=[]
    live.ENTRY_LINKS.write_text(json.dumps({"account":{"id":account},"environment":environment,
        "links":[{"id":"p1","opened_at":c._positions[0]["opened_at"],"order":order}]}))
    plan=live.tick(c,now=NOW,journal_path=journal,
                   bars={("xauusd","5m"):bars_with_setup(),("xauusd","15m"):runner_bars(90.)})
    assert c.calls==[]
    assert plan.checks[0]["reason"]=="unmatched_entry"


def test_runner_status_is_logged_even_when_no_order_was_sent(tmp_path):
    from friendsbot.runner import Runner
    plan=live.Plan();plan.check("missing_runner_candles",position_id="p1")
    path=tmp_path/"live.jsonl"
    Runner(FakeClient(),log_file=path)._append("safety",plan)
    row=json.loads(path.read_text())
    assert row["actions"]==[]
    assert row["checks"][0]["reason"]=="missing_runner_candles"


def test_missing_tagged_entry_is_reported_without_adopting_manual_position(journal):
    c = runner_client();c._recent = []
    plan = live.tick(c,now=NOW,journal_path=journal,
                     bars={("xauusd","5m"):bars_with_setup(),("xauusd","15m"):runner_bars(90.)})
    assert c.calls == []
    assert plan.checks[0]["reason"] == "unmatched_entry"


def test_fifteen_minute_history_is_requested_for_an_hourly_entry(journal,monkeypatch):
    c = runner_client("short","1h");requests=[]
    def get_candles(items):
        requests.extend(items)
        return {item:runner_bars(110.) if item[2]=="15m" else bars_with_setup() for item in items}
    monkeypatch.setattr(live,"candles",get_candles)
    live.tick(c,now=NOW,journal_path=journal)
    assert ("binance","XAUUSDT","15m") in requests
    assert ("close","p1") in c.calls


def test_dry_run_checks_runner_without_sending_a_close(journal):
    c = runner_client()
    plan = live.tick(c,now=NOW,journal_path=journal,dry_run=True,
                     bars={("xauusd","5m"):bars_with_setup(),("xauusd","15m"):runner_bars(90.)})
    assert c.calls == []
    assert any(a["action"]=="close_runner" for a in plan.actions)


def test_market_stops_after_two_losses_today(journal):
    closed = []
    for i in range(2):
        closed.append({"id": f"c{i}", "market_id": MARKET, "side": "long", "realized_pnl": -100.0,
                       "opened_at": int(pd.Timestamp("2026-03-02 06:01", tz="UTC").timestamp() * 1000),
                       "closed_at": int(pd.Timestamp(f"2026-03-02 06:1{i}", tz="UTC").timestamp() * 1000)})
    c = FakeClient(closed=closed, recent=[entry_order_filled()])
    plan = run(c, journal)
    assert not any(k == "place" for k, *_ in c.calls)
    assert any("verliezen vandaag" in n for n in plan.notes)


def test_client_id_round_trip():
    cid = live.client_id("nas100", "15m", "friend_window", "short", pd.Timestamp("2026-10-10 04:00", tz="UTC"), 30950.5, 30900.25)
    m = live.parse_client_id(cid)
    assert (m["asset"], m["tf"], m["session"], m["direction"], m["stop"], m["trigger"]) == \
           ("nas100", "15m", "friend_window", "short", 30950.5, 30900.25)
    assert live.parse_client_id(cid + "|1791650000")["trigger"] == 30900.25
    assert live.parse_client_id("someone-else") is None
    assert len(cid) <= 128


@pytest.mark.parametrize("ts,asset,is_open", [
    ("2026-10-10 16:00", "xauusd", False),   # Saturday
    ("2026-10-11 21:30", "nas100", False),   # Sunday 17:30 New York
    ("2026-10-11 22:05", "nas100", True),    # Sunday 18:05 New York: open
    ("2026-10-12 21:30", "xauusd", False),   # Monday 17:30 New York: daily break
    ("2026-10-16 21:05", "xauusd", False),   # Friday after 17:00 New York
    ("2026-10-10 16:00", "btcusdt", True),   # BTC trades through the weekend
    ("2026-01-12 22:30", "xauusd", False),   # winter: break is 22:00–23:00 UTC
])
def test_market_hours(ts, asset, is_open):
    assert live.market_open(asset, pd.Timestamp(ts, tz="UTC")) is is_open


def test_no_gold_entries_in_the_weekend(journal):
    c = FakeClient()
    weekend_bars = bars_with_setup()
    weekend_bars.index = weekend_bars.index + (pd.Timestamp("2026-03-07", tz="UTC") - pd.Timestamp("2026-03-02", tz="UTC"))
    live.tick(c, now=pd.Timestamp("2026-03-07 06:21", tz="UTC"), journal_path=journal, bars={("xauusd", "5m"): weekend_bars})
    assert c.calls == []


def test_gold_position_held_through_the_daily_break_unless_configured(journal, monkeypatch):
    before_break = pd.Timestamp("2026-03-02 21:57", tz="UTC")
    c = FakeClient(positions=[position()], recent=[entry_order_filled()])
    live.tick(c, now=before_break, journal_path=journal, bars={("xauusd", "5m"): bars_with_setup()})
    assert ("close", "p1") not in c.calls
    monkeypatch.setattr(live, "FLATTEN_BEFORE_BREAK", True)
    c = FakeClient(positions=[position()], recent=[entry_order_filled()])
    live.tick(c, now=before_break, journal_path=journal, bars={("xauusd", "5m"): bars_with_setup()})
    assert ("close", "p1") in c.calls


class ConflictClient(FakeClient):
    def __init__(self, existing_status, **kw):
        super().__init__(**kw)
        self.existing_status = existing_status

    def place_order(self, body):
        self.calls.append(("place", body))
        if "|" in body["client_order_id"].split("|", 8)[-1] or body["client_order_id"].count("|") > 7:
            return {"id": "o-new", **body}
        raise live.ApiError(409, {"error": {"code": "conflict"}})

    def orders_by_client_id(self, cid):
        return [{"client_order_id": cid, "status": self.existing_status}]


@pytest.mark.parametrize("status,places", [("resting", 1), ("filled", 1), ("canceled", 2)])
def test_conflicting_client_id_never_duplicates_a_live_order(journal, status, places):
    c = ConflictClient(status)
    run(c, journal)
    assert sum(1 for k, *_ in c.calls if k == "place") == places
