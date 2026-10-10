"""Minimal MyFundedPerps client: REST trading API and historical candles from the market stream.

Docs: https://docs.myfundedperpetuals.com — test keys (fp_test_…) only work on the sandbox host,
live keys (fp_live_…) only on the developers host. The key is read from the environment
(FP_API_KEY) and never logged.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pandas as pd

HOSTS = {"test": "https://sandbox.myfundedperpetuals.com", "live": "https://developers.myfundedperpetuals.com"}
STREAM = "wss://api-stream.myfundedperpetuals.com/v1/market-data"


class ApiError(Exception):
    def __init__(self, status: int, body: dict):
        self.status, self.body = status, body
        err = body.get("error", {}) if isinstance(body, dict) else {}
        super().__init__(f"{status} {err.get('code', '')}: {err.get('message', body)}")

    @property
    def code(self) -> str:
        return (self.body.get("error") or {}).get("code", "") if isinstance(self.body, dict) else ""


class Client:
    def __init__(self, key: str | None = None, host: str | None = None, timeout: float = 20):
        self.key = key if key is not None else os.environ.get("FP_API_KEY", "")
        env = "live" if self.key.startswith("fp_live_") else "test"
        self.host = host or HOSTS[env]
        self.environment = env
        self.timeout = timeout

    @property
    def authenticated(self) -> bool:
        return bool(self.key)

    def _call(self, method: str, path: str, query: dict | None = None, body: dict | None = None,
              idempotent: bool = False, retries: int = 3):
        url = self.host + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        headers = {"Accept": "application/json", "User-Agent": "friendsbot/1"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if idempotent:
            headers["Idempotency-Key"] = str(uuid.uuid4())   # one key per intended operation, reused on retry
        for attempt in range(retries):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                try:
                    payload = json.loads(e.read() or b"{}")
                except ValueError:
                    payload = {}
                if e.code in (429, 500, 502, 503) and attempt + 1 < retries:
                    time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                    continue
                raise ApiError(e.code, payload) from None
            except urllib.error.URLError:
                if attempt + 1 < retries:
                    time.sleep(2 ** attempt)
                    continue
                raise

    # public
    def markets(self) -> dict[str, dict]:
        return {m["market_id"]: m for m in self._call("GET", "/v1/markets")["data"]}

    # account
    def accounts(self) -> list[dict]:
        return self._call("GET", "/v1/accounts")["data"]

    def account(self, account_id: str) -> dict:
        return self._call("GET", f"/v1/accounts/{account_id}")["data"]

    def positions(self, account_id: str, status: str = "open", limit: int = 100) -> list[dict]:
        return self._call("GET", "/v1/positions", {"account_id": account_id, "status": status, "limit": limit})["data"]

    def orders(self, account_id: str, status: str | None = None, limit: int = 100) -> list[dict]:
        return self._call("GET", "/v1/orders", {"account_id": account_id, "status": status, "limit": limit})["data"]

    def orders_by_client_id(self, client_order_id: str) -> list[dict]:
        return self._call("GET", "/v1/orders", {"client_order_id": client_order_id})["data"]

    # trading
    def place_order(self, body: dict) -> dict:
        return self._call("POST", "/v1/orders", body=body, idempotent=True)["data"]

    def cancel_order(self, order_id: str) -> dict:
        return self._call("DELETE", f"/v1/orders/{order_id}")

    def close_position(self, position_id: str, client_order_id: str | None = None) -> dict:
        body = {"client_order_id": client_order_id} if client_order_id else {}
        return self._call("POST", f"/v1/positions/{position_id}/close", body=body, idempotent=True)

    def set_exits(self, position_id: str, body: dict) -> dict:
        return self._call("PUT", f"/v1/positions/{position_id}/exit-orders", body=body, idempotent=True)


def candles(requests: list[tuple[str, str, str]], limit: int = 400) -> dict[tuple, pd.DataFrame]:
    """Closed candles for [(provider, symbol, interval)] from the public market stream.
    The still-forming last candle is dropped."""
    import websockets

    async def run():
        out = {}
        async with websockets.connect(STREAM, open_timeout=20, max_size=2 ** 24) as ws:
            pending = {}
            for i, (prov, sym, iv) in enumerate(requests, start=1):
                pending[i] = (prov, sym, iv)
                await ws.send(json.dumps({"op": "req", "id": i, "method": "candles.history",
                                          "payload": {"provider": prov, "symbol": sym, "interval": iv, "limit": limit}}))
                if len(pending) >= 4:                     # at most 4 history requests in flight
                    await _drain(ws, pending, out, keep=3)
            await _drain(ws, pending, out, keep=0)
        return out

    async def _drain(ws, pending, out, keep):
        while len(pending) > keep:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 30))
            if msg.get("op") not in ("res", "err") or msg.get("id") not in pending:
                continue
            key = pending.pop(msg["id"])
            if msg["op"] == "err":
                raise RuntimeError(f"candles {key}: {msg.get('error')}")
            rows = [c for c in msg["result"] if c.get("isFinal", True)]
            df = pd.DataFrame({
                "open": [float(c["open"]) for c in rows], "high": [float(c["high"]) for c in rows],
                "low": [float(c["low"]) for c in rows], "close": [float(c["close"]) for c in rows],
                "volume": [float(c["volume"]) for c in rows],
            }, index=pd.to_datetime([c["openTime"] for c in rows], unit="ms", utc=True))
            out[key] = df[~df.index.duplicated(keep="last")].sort_index()

    return asyncio.run(run())
