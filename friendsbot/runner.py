"""Always-on runner: react to candle closes and account events within seconds.

Listens to two WebSockets and runs `live.tick` whenever something happens:
* the public market stream: a candle of a traded market/timeframe closes → place, move or cancel
  the stop entry for the next candle, and check the runner exit;
* the private account stream: an order fills or a position changes → put the stop loss and
  take profit on immediately, or move the stop to breakeven.
A safety tick runs every SAFETY_SECONDS regardless, so a dropped connection never leaves a
position unmanaged for long. Both streams reconnect with backoff.

Start with: FP_API_KEY=fp_test_… python -m friendsbot run
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path

import pandas as pd

from . import live
from .mfp import STREAM, Client

ACCOUNT_STREAM = {"test": "wss://account-stream.myfundedperpetuals.com/v1/sandbox/stream",
                  "live": "wss://account-stream.myfundedperpetuals.com/v1/stream"}
INTERVALS = ["5m", "15m", "30m", "1h", "4h"]
SAFETY_SECONDS = 60
DEBOUNCE_SECONDS = 1.5           # candles of several timeframes close together; run once
ACCOUNT_EVENTS = {("order", "filled"), ("order", "partially_filled"), ("order", "canceled"), ("order", "rejected"),
                  ("position", "opened"), ("position", "changed"), ("position", "closed"), ("account", "updated")}


def log(event: str, **kw):
    print(json.dumps({"t": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"), "event": event, **kw},
                     default=str), flush=True)


class Runner:
    def __init__(self, client: Client, account_id: str | None = None, dry_run: bool = False,
                 log_file: Path | None = None):
        self.client, self.account_id, self.dry_run = client, account_id, dry_run
        self.wake = asyncio.Event()
        self.reason = "start"
        self.log_file = log_file

    def poke(self, reason: str):
        self.reason = reason
        self.wake.set()

    async def market_stream(self):
        by_provider: dict[str, list[str]] = {}
        for market in live.MARKETS.values():
            prov, sym = market.split("|", 1)
            by_provider.setdefault(prov, []).append(sym)
        await self._forever("market", STREAM, None, self._market_session, by_provider)

    async def _market_session(self, ws, by_provider):
        for i, (prov, syms) in enumerate(by_provider.items(), start=1):
            await ws.send(json.dumps({"op": "sub", "id": i, "channel": "candles",
                                      "payload": {"symbols": syms, "providers": [prov], "intervals": INTERVALS,
                                                  "historyLimit": 0}}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("op") == "events":
                closed = [f"{e['symbol']} {e['interval']}" for e in msg["events"]
                          if e.get("isFinal") and e.get("interval") in INTERVALS]
                if closed:
                    self.poke("candle " + ", ".join(sorted(set(closed))))
            elif msg.get("op") in ("sub_err", "err"):
                log("market_stream_error", error=msg.get("error"))
            elif msg.get("op") == "draining":
                return                      # server rotates the connection; reconnect

    async def account_stream(self):
        if not self.client.authenticated:
            return
        url = ACCOUNT_STREAM[self.client.environment]
        headers = {"Authorization": f"Bearer {self.client.key}"}
        await self._forever("account", url, headers, self._account_session)

    async def _account_session(self, ws):
        async for raw in ws:
            msg = json.loads(raw)
            kind = (msg.get("type"), msg.get("event"))
            if kind in ACCOUNT_EVENTS:
                obj = msg.get("order") or msg.get("position") or {}
                self.poke(f"{kind[0]} {kind[1]} {obj.get('market_id', '')}".strip())
            elif msg.get("type") == "snapshot_end":
                self.poke("account snapshot")

    async def _forever(self, name, url, headers, session, *args):
        import websockets
        delay = 1
        while True:
            try:
                kw = {"open_timeout": 20, "max_size": 2 ** 24}
                if headers:
                    kw["additional_headers"] = headers
                async with websockets.connect(url, **kw) as ws:
                    log(f"{name}_stream_connected")
                    delay = 1
                    await session(ws, *args)
            except Exception as err:            # noqa: BLE001 — any failure: log, back off, reconnect
                log(f"{name}_stream_down", error=str(err)[:200], retry_in=delay)
            await asyncio.sleep(delay + random.random())
            delay = min(delay * 2, 60)
            self.poke(f"{name} reconnect")      # state may have changed while disconnected

    async def ticker(self):
        while True:
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=SAFETY_SECONDS)
                await asyncio.sleep(DEBOUNCE_SECONDS)
            except asyncio.TimeoutError:
                self.reason = "safety"
            self.wake.clear()
            reason, started = self.reason, time.monotonic()
            try:
                plan = await asyncio.to_thread(live.tick, self.client, self.account_id, None, self.dry_run)
                if plan.actions or reason != "safety":
                    log("tick", reason=reason, seconds=round(time.monotonic() - started, 1),
                        actions=plan.actions, notes=plan.notes[-3:])
                self._append(reason, plan)
            except Exception as err:            # noqa: BLE001 — keep running; next event or safety tick retries
                log("tick_failed", reason=reason, error=str(err)[:300])

    def _append(self, reason, plan):
        if not self.log_file or not plan.actions:
            return
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a") as f:
            f.write(json.dumps({"t": pd.Timestamp.now(tz="UTC").isoformat(), "reason": reason,
                                "actions": plan.actions}, default=str) + "\n")

    async def main(self):
        log("runner_start", environment=self.client.environment, dry_run=self.dry_run)
        await asyncio.gather(self.market_stream(), self.account_stream(), self.ticker())


def run(client: Client, account_id: str | None = None, dry_run: bool = False, log_file: Path | None = None):
    asyncio.run(Runner(client, account_id, dry_run, log_file).main())
