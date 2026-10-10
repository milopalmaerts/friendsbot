"""Run the paper bot's rules against a MyFundedPerps account (sandbox first).

`tick()` is meant to run every few minutes (GitHub Actions, cron, or a loop). Each run is
independent: everything it needs is read back from the API, so a missed or repeated run is safe.

Per run:
1. Read this month's combinations (market × session × timeframe, ranked) from the paper journal.
2. Read the account: equity, loss floors, open positions, working orders, today's closed trades.
3. Manage open positions: stop loss + 2R take profit for half (OCO), stop to breakeven after the
   take profit, close the runner on a candle close through the EMA 21.
4. For combinations whose session is open: keep exactly one stop-entry order at the second-entry
   trigger, or cancel it when the setup is gone. Risk and limits as in paper.py, plus guards that
   keep the account clear of the prop firm's daily-loss and maximum-drawdown floors.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import paper
from .mfp import ApiError, Client, candles
from .research import FRIEND_RULES, window_filter
from .strategy import pending, with_indicators

# our market name → MyFundedPerps market
MARKETS = {
    "xauusd": "binance|XAUUSDT",
    "nas100": "hyperliquid|xyz:XYZ100",
    "btcusdt": "binance|BTCUSDT",
}
INTERVAL = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h"}
TAG = "fb"                                   # prefix of every client_order_id this bot creates

# Prop accounts allow only 3–6% total drawdown and 3% per day, so risk is far smaller than in the
# paper journal's €-view. Overridable with environment variables.
BASE_RISK_PCT = float(os.environ.get("FB_BASE_RISK_PCT", "0.10"))
HOT_RISK_PCT = float(os.environ.get("FB_HOT_RISK_PCT", "0.20"))
LEVERAGE = float(os.environ.get("FB_LEVERAGE", "5"))
NY = "America/New_York"
# Gold and the Nasdaq follow CME futures hours: Sunday 18:00 → Friday 17:00 New York time, with a
# daily break 17:00–18:00. The perps on MyFundedPerps keep quoting through those hours, but the
# backtest never traded them, so neither does the bot. BTC trades around the clock.
SESSION_MARKETS = {"xauusd", "nas100"}
CLOSE_BEFORE_BREAK = pd.Timedelta(minutes=5)
# Open positions stay through the pause and weekend (paper.GAP_POLICY = "hold"); set
# FB_CLOSE_BEFORE_BREAK=1 to flatten gold and US100 five minutes before each break instead.
FLATTEN_BEFORE_BREAK = os.environ.get("FB_CLOSE_BEFORE_BREAK") == "1"
DAILY_ROOM_TRADES = 3        # need room for this many full losses before the daily-loss floor
DRAWDOWN_ROOM_TRADES = 6     # … and this many before the maximum-drawdown floor


def market_open(asset: str, t: pd.Timestamp) -> bool:
    if asset not in SESSION_MARKETS:
        return True
    ny = t.tz_convert(NY)
    h = ny.hour + ny.minute / 60
    if ny.weekday() == 5:
        return False                     # Saturday
    if ny.weekday() == 6:
        return h >= 18                   # opens Sunday 18:00
    if ny.weekday() == 4 and h >= 17:
        return False                     # closes Friday 17:00
    return not 17 <= h < 18              # daily break


@dataclass
class Plan:
    """Everything one run decided, for logging and for dry runs."""
    notes: list[str] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)

    def act(self, kind: str, **kw):
        self.actions.append({"action": kind, **kw})

    def note(self, msg: str):
        self.notes.append(msg)


def client_id(asset, tf, session, direction, zero_time, stop, trigger) -> str:
    """Identity of one entry order. A moved trigger is a new order with a new id."""
    return f"{TAG}|{asset}|{tf}|{session}|{direction[0]}|{int(zero_time.timestamp())}|{stop:.8g}|{trigger:.8g}"


def parse_client_id(cid: str | None) -> dict | None:
    if not cid or not cid.startswith(TAG + "|"):
        return None
    parts = cid.split("|")
    if len(parts) < 8:
        return None
    _, asset, tf, session, d, zero, stop, trigger = parts[:8]
    try:
        return {"asset": asset, "tf": tf, "session": session, "direction": "long" if d == "l" else "short",
                "zero": int(zero), "stop": float(stop), "trigger": float(trigger), "cell": f"{asset}|{tf}|{session}"}
    except ValueError:
        return None


def round_step(x: float, step: float) -> float:
    return math.floor(x / step + 1e-9) * step


def current_selection(journal_path: Path = paper.JOURNAL, now: pd.Timestamp | None = None) -> tuple[str, list[dict]]:
    j = json.loads(Path(journal_path).read_text())
    sel = j["selections"][-1]
    return sel["month"], sel["cells"]


def entry_order(market: dict, setup: dict, cell: dict, risk_usd: float, account_id: str) -> dict | None:
    side = "buy" if setup["direction"] == "long" else "sell"
    per_unit = abs(setup["entry_stop"] - setup["stop"])
    if per_unit <= 0:
        return None
    size = round_step(risk_usd / per_unit, market["size_step"])
    if size < market["min_size"] or size * setup["entry_stop"] < market["min_notional"]:
        return None
    return {
        "client_order_id": client_id(cell["asset"], cell["tf"], cell["session"], setup["direction"],
                                     pd.Timestamp(setup["zero_time"]), setup["stop"], setup["entry_stop"]),
        "type": "stop_market", "account_id": account_id, "market_id": MARKETS[cell["asset"]],
        "side": side, "size": size, "trigger_price": round(setup["entry_stop"], 8),
        "leverage": min(LEVERAGE, market["max_leverage"]), "margin_mode": "cross",
    }


def tick(client: Client, account_id: str | None = None, now: pd.Timestamp | None = None,
         dry_run: bool = False, journal_path: Path = paper.JOURNAL, bars: dict | None = None) -> Plan:
    plan = Plan()
    now = now or pd.Timestamp.now(tz="UTC")
    cfg = FRIEND_RULES[0]
    month, cells = current_selection(journal_path)
    if month != now.strftime("%Y-%m"):
        plan.note(f"selectie is van {month}; draai `python -m friendsbot paper` voor {now:%Y-%m}")
    ranks = [c for c in cells]
    markets = client.markets()

    # ---- account state -------------------------------------------------------------------
    if client.authenticated:
        accounts = [a for a in client.accounts() if a["status"] == "active"]
        if account_id:
            accounts = [a for a in accounts if a["id"] == account_id]
        if not accounts:
            plan.note("geen actief account gevonden")
            return plan
        acct = client.account(accounts[0]["id"])
        account_id = acct["id"]
        risk = acct.get("risk") or {}
        equity = risk.get("equity") or acct["balance"]
        positions = client.positions(account_id)
        working = client.orders(account_id, status="working")
        recent = client.orders(account_id, limit=100)
        closed = client.positions(account_id, status="closed", limit=100)
    else:
        account_id = account_id or "DRY-RUN"
        equity, risk, positions, working, recent, closed = 100_000.0, {}, [], [], [], []
        plan.note("geen API-sleutel: droog draaien met een fictief account van $100.000")

    # ---- what we know about each market today ---------------------------------------------
    by_market = {v: k for k, v in MARKETS.items()}
    day = now.tz_convert("America/New_York").strftime("%Y-%m-%d")   # prop daily reset is NY midnight
    entries = {o["id"]: (o, parse_client_id(o.get("client_order_id"))) for o in recent}
    ours = [(o, meta) for o, meta in entries.values() if meta]

    def entry_for(position) -> dict | None:
        """Our filled entry order that opened this position (latest fill on that market before it opened)."""
        cands = [(o, m) for o, m in ours if o["market_id"] == position["market_id"] and o.get("status") == "filled"
                 and o.get("reduce_only") is False and o.get("requested_at", 0) <= position["opened_at"] + 60_000]
        return max(cands, key=lambda x: x[0].get("requested_at", 0)) if cands else None

    losses_today, streak = {}, {}
    for p in sorted(closed, key=lambda p: p.get("closed_at") or 0):
        e = entry_for(p)
        if not e:
            continue
        meta = e[1]
        pnl = p.get("realized_pnl") or 0
        closed_day = pd.Timestamp(p["closed_at"], unit="ms", tz="UTC").tz_convert("America/New_York").strftime("%Y-%m-%d")
        if pnl < 0:
            if closed_day == day:
                losses_today[meta["asset"]] = losses_today.get(meta["asset"], 0) + 1
            streak[meta["cell"]] = streak.get(meta["cell"], 0) + 1
        elif pnl > 0:
            streak[meta["cell"]] = 0

    demoted = {c for c, n in streak.items() if n >= paper.HOT_DEMOTE_AFTER}
    hot = [f"{c['asset']}|{c['tf']}|{c['session']}" for c in ranks
           if f"{c['asset']}|{c['tf']}|{c['session']}" not in demoted][:paper.HOT_N]

    # ---- candles for every timeframe in use ------------------------------------------------
    needed = sorted({(c["asset"], c["tf"]) for c in cells} | {(by_market.get(p["market_id"]), m["tf"])
                     for p in positions for _, m in [entry_for(p) or (None, None)] if m and by_market.get(p["market_id"])})
    if bars is None:
        reqs = [(MARKETS[a].split("|")[0], MARKETS[a].split("|")[1], INTERVAL[tf]) for a, tf in needed]
        raw = candles(reqs)
        bars = {(a, tf): raw[(MARKETS[a].split("|")[0], MARKETS[a].split("|")[1], INTERVAL[tf])] for a, tf in needed}
    frames = {k: with_indicators(v, cfg) for k, v in bars.items() if len(v) > 60}

    # ---- 1. manage open positions ------------------------------------------------------------
    for p in positions:
        asset = by_market.get(p["market_id"])
        e = entry_for(p)
        if not asset or not e:
            plan.note(f"positie {p['market_id']} is niet van de bot; niet aangeraakt")
            continue
        try:
            manage_position(client, plan, p, asset, e, working, markets, frames, now, dry_run)
        except ApiError as err:
            plan.note(f"positie {p['market_id']}: {err}")

    # ---- 2. entries -------------------------------------------------------------------------
    return place_entries(client, plan, cfg, ranks, frames, markets, positions, working, by_market, losses_today,
                         hot, equity, risk, account_id, now, dry_run)


def manage_position(client, plan, p, asset, e, working, markets, frames, now, dry_run):
        order, meta = e
        sign = 1 if p["side"] == "long" else -1
        if FLATTEN_BEFORE_BREAK and not market_open(asset, now + CLOSE_BEFORE_BREAK):
            plan.act("close_before_break", market=p["market_id"])
            if not dry_run:
                client.close_position(p["id"], client_order_id=f"{TAG}-brk-{p['id'][:40]}-{int(now.timestamp())}")
            return
        entry, stop = p["entry_price"], meta["stop"]
        r = abs(entry - stop)
        initial = order.get("filled_size") or order["size"]
        partial_done = p["size"] <= initial * 0.75
        exits = [o for o in working if o["market_id"] == p["market_id"] and o.get("reduce_only")]
        sl = [o for o in exits if o["type"] == "stop_market" or o.get("mode") == "stop_market"]
        tp = [o for o in exits if o["type"] in ("take_profit", "take_market") or o.get("mode") in ("take_profit", "take_market")]
        step = markets[p["market_id"]]["size_step"]
        if not sl and not tp and not partial_done:
            half = round_step(p["size"] / 2, step)
            ops = [{"kind": "place", "group": "sl", "execution_type": "market", "price": round(stop, 8),
                    "size": p["size"], "oco_pair_with_operation_index": 1},
                   {"kind": "place", "group": "tp", "execution_type": "market",
                    "price": round(entry + sign * 2 * r, 8), "size": half, "oco_pair_with_operation_index": 0}]
            plan.act("exits", market=p["market_id"], stop=stop, tp=entry + sign * 2 * r, size=p["size"], half=half)
            if not dry_run:
                client.set_exits(p["id"], {"expected_position_size": p["size"], "expected_orders": [], "operations": ops})
        elif partial_done and sl and abs(sl[0].get("trigger_price", 0) - entry) > 1e-9:
            plan.act("breakeven", market=p["market_id"], price=entry)
            if not dry_run:
                client.set_exits(p["id"], {
                    "expected_position_size": p["size"],
                    "expected_orders": [{"order_id": o["id"], "execution_type": "market",
                                         "price": o.get("trigger_price"), "size": o["size"]} for o in sl + tp],
                    "operations": [{"kind": "modify", "order_id": sl[0]["id"], "price": round(entry, 8)}]})
        elif not sl:
            plan.act("stop", market=p["market_id"], price=entry if partial_done else stop)
            if not dry_run:
                client.set_exits(p["id"], {
                    "expected_position_size": p["size"],
                    "expected_orders": [{"order_id": o["id"], "execution_type": "market",
                                         "price": o.get("trigger_price"), "size": o["size"]} for o in tp],
                    "operations": [{"kind": "place", "group": "sl", "execution_type": "market",
                                    "price": round(entry if partial_done else stop, 8), "size": p["size"]}]})
        # runner: close on a candle close through the EMA once the first half is banked
        df = frames.get((asset, meta["tf"]))
        if partial_done and df is not None and (df.close.iloc[-1] - df.ema.iloc[-1]) * sign < 0:
            plan.act("close_runner", market=p["market_id"], close=float(df.close.iloc[-1]), ema=float(df.ema.iloc[-1]))
            if not dry_run:
                client.close_position(p["id"], client_order_id=f"{TAG}-close-{p['id'][:40]}-{int(now.timestamp())}")

def place_entries(client, plan, cfg, ranks, frames, markets, positions, working, by_market, losses_today,
                  hot, equity, risk, account_id, now, dry_run) -> Plan:
    open_assets = {by_market.get(p["market_id"]) for p in positions}
    our_entries = {o["id"]: (o, m) for o, m in ((o, parse_client_id(o.get("client_order_id"))) for o in working)
                   if m and not o.get("reduce_only")}
    blocked = None
    if client.authenticated and risk:
        daily_room, dd_room = risk.get("daily_loss_room"), risk.get("max_drawdown_room")
        r_usd = equity * HOT_RISK_PCT / 100
        if daily_room is not None and daily_room < DAILY_ROOM_TRADES * r_usd:
            blocked = f"te dicht bij de daglimiet (nog ${daily_room:,.0f})"
        if dd_room is not None and dd_room < DRAWDOWN_ROOM_TRADES * r_usd:
            blocked = f"te dicht bij de maximale drawdown (nog ${dd_room:,.0f})"
    if blocked:
        plan.note("geen nieuwe entries: " + blocked)

    wanted = {}
    slots = paper.MAX_OPEN - len([p for p in positions if by_market.get(p["market_id"])])
    for c in ranks:
        asset, tf, session = c["asset"], c["tf"], c["session"]
        cell = f"{asset}|{tf}|{session}"
        df = frames.get((asset, tf))
        if df is None:
            continue
        bar = df.index[-1] - df.index[-2]
        next_bar = df.index[-1] + bar
        if blocked or not window_filter(session)(next_bar) or asset in open_assets:
            continue
        if not (market_open(asset, now) and market_open(asset, now + bar)):
            continue                     # weekend, daily break, or closing before this bar ends
        if losses_today.get(asset, 0) >= paper.ASSET_DAY_LOSSES:
            plan.note(f"{asset}: {losses_today[asset]} verliezen vandaag, markt gestopt tot morgen")
            continue
        if slots <= 0:
            break
        risk_pct = HOT_RISK_PCT if cell in hot else BASE_RISK_PCT
        for s in pending(df, cfg):
            if s.get("entry_stop") and s.get("trend_ok"):
                body = entry_order(markets[MARKETS[asset]], s, c, equity * risk_pct / 100, account_id)
                if body and body["market_id"] not in {w["market_id"] for w in wanted.values()}:
                    wanted[body["client_order_id"]] = {**body, "risk_pct": risk_pct}
                    slots -= 1
                    break

    # cancel entries that are no longer wanted (setup gone, trigger moved, session over), place new ones
    have = {"|".join(o["client_order_id"].split("|")[:8]): o for o, _ in our_entries.values()}
    for cid, o in have.items():
        if cid in wanted:
            wanted.pop(cid)          # already working with the same trigger and stop
            continue
        plan.act("cancel_entry", client_order_id=o["client_order_id"], market=o["market_id"])
        if not dry_run:
            try:
                client.cancel_order(o["id"])
            except ApiError as err:
                plan.note(f"annuleren mislukt {cid}: {err}")
    for cid, body in wanted.items():
        risk_pct = body.pop("risk_pct")
        plan.act("place_entry", client_order_id=cid, market=body["market_id"], side=body["side"],
                 trigger=body["trigger_price"], stop=parse_client_id(cid)["stop"], size=body["size"], risk_pct=risk_pct)
        if not dry_run:
            try:
                client.place_order(body)
            except ApiError as err:
                if err.status != 409:
                    plan.note(f"order geweigerd {cid}: {err}")
                    continue
                # this exact order exists already: another run placed it, it filled, or it was cancelled
                states = {o["status"] for o in client.orders_by_client_id(cid)}
                if states & {"pending", "resting", "filled"}:
                    plan.note(f"{cid} bestaat al ({', '.join(sorted(states))}); niet opnieuw geplaatst")
                    continue
                body["client_order_id"] = cid + f"|{int(now.timestamp())}"
                try:
                    client.place_order(body)
                except ApiError as err2:
                    plan.note(f"order geweigerd {cid}: {err2}")
    plan.note(f"account {account_id}: equity ${equity:,.0f}, {len(positions)} posities, "
              f"{len(our_entries)} wachtende entries, toppers {hot}")
    return plan
