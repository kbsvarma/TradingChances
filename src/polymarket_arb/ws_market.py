from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import aiohttp
import websockets

from polymarket_arb.book import BookStore
from polymarket_arb.types import NormalizedEvent


class MarketWSClient:
    """Public market websocket client with reconnect + snapshot resync."""

    def __init__(
        self,
        ws_url: str,
        rest_url: str,
        markets: list[str],
        normalizer,
        out_queue: asyncio.Queue[NormalizedEvent],
        book_store: BookStore,
    ) -> None:
        self.ws_url = ws_url
        self.rest_url = rest_url
        self.markets = markets
        self.normalizer = normalizer
        self.out_queue = out_queue
        self.book_store = book_store
        self.log = logging.getLogger("MarketWSClient")
        self._stop = asyncio.Event()
        self._paused_markets: set[str] = set()

    async def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=10, ping_timeout=10) as ws:
                    for market in self.markets:
                        await self.resync_market(market)
                    await self._subscribe(ws)
                    self.log.info("market ws connected")
                    backoff = 1
                    async for raw in ws:
                        msg = json.loads(raw)
                        event = self.normalizer.from_market_ws(msg)
                        if event is None:
                            continue
                        if event.market_id in self._paused_markets:
                            continue
                        if event.event_type.value == "OrderBookUpdate":
                            try:
                                self._apply_book(event)
                            except Exception:
                                self.log.exception("book anomaly; triggering resync")
                                self._paused_markets.add(event.market_id)
                                await self.resync_market(event.market_id)
                                continue
                        await self.out_queue.put(event)
                        if self._stop.is_set():
                            break
            except Exception:
                self.log.exception("market ws disconnected")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _subscribe(self, ws) -> None:
        payload = {
            "type": "subscribe",
            "channel": "market",
            "markets": self.markets,
        }
        await ws.send(json.dumps(payload))

    def _apply_book(self, event: NormalizedEvent) -> None:
        p = event.payload
        bids = p.get("bids", [])
        asks = p.get("asks", [])
        self.book_store.upsert(
            market_id=event.market_id,
            token_id=str(event.token_id),
            bids=bids,
            asks=asks,
            recv_ts=event.recv_ts,
            exchange_ts=event.exchange_ts,
            active=bool(p.get("market_active", True)),
        )

    async def resync_market(self, market_id: str) -> None:
        # Sequence-independent recovery: discard stream assumptions, rehydrate from REST snapshot.
        self._paused_markets.add(market_id)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            url = f"{self.rest_url}/book"
            params = {"market": market_id}
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    self.log.error("resync failed", extra={"market_id": market_id})
                    return
                data: dict[str, Any] = await resp.json()
        for token in data.get("tokens", []):
            self.book_store.upsert(
                market_id=market_id,
                token_id=str(token.get("asset_id")),
                bids=token.get("bids", []),
                asks=token.get("asks", []),
                recv_ts=time.time(),
                exchange_ts=data.get("timestamp"),
                active=bool(data.get("market_active", True)),
            )
        self._paused_markets.discard(market_id)
        self.log.info("market resynced", extra={"market_id": market_id})
