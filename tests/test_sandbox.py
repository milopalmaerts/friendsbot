import pandas as pd
import pytest

from friendsbot import live, sandbox

MS = lambda t: int(pd.Timestamp(t, tz="UTC").timestamp() * 1000)


class Client:
    environment = "test"

    def accounts(self):
        return [{"id": "a1", "status": "active"}]

    def account(self, aid):
        return {"id": aid, "account_number": "FP-1", "name": "API Sandbox 1", "status": "active",
                "starting_balance": 100_000, "balance": 100_150.0, "risk": {"equity": 100_150.0}}

    def paged(self, path, query):
        if path == "/v1/orders":
            cid = live.client_id("btcusdt", "15m", "london", "long", pd.Timestamp("2026-10-12 08:00", tz="UTC"), 99_000, 100_000)
            return [{"id": "o1", "client_order_id": cid, "market_id": "binance|BTCUSDT", "status": "filled",
                     "reduce_only": False, "requested_at": MS("2026-10-12 08:14"), "size": 0.2, "filled_size": 0.2},
                    {"id": "o2", "client_order_id": "manual", "market_id": "binance|ETHUSDT", "status": "filled",
                     "reduce_only": False, "requested_at": MS("2026-10-12 09:00"), "size": 1}]
        return [{"id": "p1", "market_id": "binance|BTCUSDT", "side": "long", "status": "closed", "size": 0.0,
                 "entry_price": 100_000.0, "exit_price": 101_000.0, "realized_pnl": 210.0, "fees": 12.0,
                 "funding": 3.0, "close_reason": "stop_loss", "opened_at": MS("2026-10-12 08:15"),
                 "closed_at": MS("2026-10-12 11:00")},
                {"id": "p2", "market_id": "binance|ETHUSDT", "side": "long", "status": "closed",
                 "entry_price": 4000.0, "realized_pnl": -50.0, "opened_at": MS("2026-10-12 09:01"),
                 "closed_at": MS("2026-10-12 10:00")}]

    def positions(self, aid):
        return []


def test_collects_only_bot_trades_with_r_after_costs():
    d = sandbox.collect(Client())
    assert d["account"]["number"] == "FP-1"
    (t,) = d["trades"]
    assert (t["asset"], t["tf"], t["session"], t["source"]) == ("btcusdt", "15m", "london", "sandbox")
    assert t["risk_usd"] == pytest.approx(1000 * 0.2)          # (entry − stop) × size
    assert t["pnl_usd"] == pytest.approx(210 - 12 - 3)
    assert t["r"] == pytest.approx(195 / 200, abs=1e-3)
