"""The bot's real trades on a MyFundedPerps account, for the journal website.

Reads every position the bot opened (matched to its entry order through the `fb|…` client
order id), and writes reports/live/sandbox.json with one row per trade: market, timeframe,
session, entry/stop/exit, the result in dollars after fees and funding, and in R (dollar
result ÷ dollar risk at entry). Positions the bot did not open are ignored.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .data import ROOT
from .live import ACCOUNT, HOT_RISK_PCT, MARKETS, find_account, parse_client_id
from .mfp import Client

OUT = ROOT / "reports" / "live" / "sandbox.json"


def _ms(t) -> str | None:
    return pd.Timestamp(t, unit="ms", tz="UTC").isoformat() if t else None


def collect(client: Client, account_id: str | None = None) -> dict:
    acct = find_account(client, account_id)
    if not acct:
        raise SystemExit(f"account {account_id or ACCOUNT} niet gevonden voor deze sleutel")
    aid = acct["id"]
    by_market = {v: k for k, v in MARKETS.items()}
    orders = client.paged("/v1/orders", {"account_id": aid})
    entries = [(o, parse_client_id(o.get("client_order_id"))) for o in orders]
    entries = [(o, m) for o, m in entries if m and o.get("status") == "filled" and not o.get("reduce_only")]
    positions = client.paged("/v1/positions", {"account_id": aid, "status": "closed"}) + client.positions(aid)

    rows = []
    for p in sorted(positions, key=lambda p: p["opened_at"]):
        asset = by_market.get(p["market_id"])
        cands = [(o, m) for o, m in entries if o["market_id"] == p["market_id"]
                 and o.get("requested_at", 0) <= p["opened_at"] + 60_000]
        if not asset or not cands:
            continue
        order, meta = max(cands, key=lambda x: x[0].get("requested_at", 0))
        size = order.get("filled_size") or order["size"]
        risk_usd = abs(p["entry_price"] - meta["stop"]) * size
        closed = p.get("status") == "closed"
        # realized_pnl is before fees; fees and funding are costs charged to the account
        net = (p.get("realized_pnl") or 0) - abs(p.get("fees") or 0) - (p.get("funding") or 0) if closed else None
        rows.append({
            "id": p["id"], "asset": asset, "tf": meta["tf"], "session": meta["session"],
            "direction": p["side"], "entry_time": _ms(p["opened_at"]), "exit_time": _ms(p.get("closed_at")),
            "entry": p["entry_price"], "stop": meta["stop"], "exit": p.get("exit_price"),
            "size": size, "risk_usd": round(risk_usd, 2),
            "pnl_usd": round(net, 2) if net is not None else None,
            "r": round(net / risk_usd, 3) if net is not None and risk_usd > 0 else None,
            "risk_pct": round(risk_usd / acct["starting_balance"] * 100, 3),
            "exit_reason": p.get("close_reason") or ("open" if not closed else "user"),
            "status": "closed" if closed else "open", "source": "sandbox",
            "hot": risk_usd / acct["starting_balance"] * 100 >= HOT_RISK_PCT * 0.9,
        })
    risk = acct.get("risk") or {}
    return {
        "updated": pd.Timestamp.now(tz="UTC").isoformat(),
        "account": {"id": aid, "number": acct.get("account_number"), "name": acct.get("name"),
                    "status": acct["status"], "starting_balance": acct["starting_balance"],
                    "balance": acct["balance"], "equity": risk.get("equity"),
                    "daily_loss_room": risk.get("daily_loss_room"), "max_drawdown_room": risk.get("max_drawdown_room")},
        "environment": client.environment,
        "trades": rows,
    }


def update(path: Path = OUT, client: Client | None = None) -> dict:
    data = collect(client or Client())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str))
    return data
