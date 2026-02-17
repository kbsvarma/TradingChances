from __future__ import annotations

import asyncio
import json
import logging
import time

try:
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover
    aiohttp = None  # type: ignore[assignment]

try:
    import websockets
except ModuleNotFoundError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]

from polymarket_arb.book import BookStore
from polymarket_arb.market_registry import MarketRegistry
from polymarket_arb.orderbook_snapshot import fetch_token_orderbook
from polymarket_arb.types import NormalizedEvent


def is_book_anomalous(
    bids: list[dict],
    asks: list[dict],
    market_active: bool,
    require_nonempty_active: bool = True,
) -> bool:
    if any(float(x.get("size", 0.0)) < 0 for x in bids + asks):
        return True
    if require_nonempty_active and market_active and (not bids and not asks):
        return True
    if bids and asks:
        try:
            best_bid = max(float(x.get("price", 0.0)) for x in bids)
            best_ask = min(float(x.get("price", 0.0)) for x in asks)
            if best_bid >= best_ask:
                return True
        except Exception:
            return True
    return False


class MarketWSClient:
    """Public market websocket client with reconnect + snapshot resync."""

    def __init__(
        self,
        ws_url: str,
        rest_url: str,
        markets: list[str],
        registry: MarketRegistry,
        normalizer,
        out_queue: asyncio.Queue[NormalizedEvent],
        book_store: BookStore,
        require_nonempty_active_book: bool = True,
        snapshot_max_level_size: float | None = None,
    ) -> None:
        self.ws_url = ws_url
        self.rest_url = rest_url
        self.markets = markets
        self.registry = registry
        self.normalizer = normalizer
        self.out_queue = out_queue
        self.book_store = book_store
        self.require_nonempty_active_book = require_nonempty_active_book
        self.snapshot_max_level_size = snapshot_max_level_size
        self.log = logging.getLogger("MarketWSClient")
        self._stop = asyncio.Event()
        self._paused_markets: set[str] = set()

    async def stop(self) -> None:
        self._stop.set()

    async def force_resync_all(self) -> None:
        for market_id in self.markets:
            await self.resync_market(market_id)

    async def run_forever(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets package is required for MarketWSClient")
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
                            ok = await self._handle_book_event(event)
                            if not ok:
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
        active = bool(p.get("market_active", True))
        if is_book_anomalous(
            bids=bids,
            asks=asks,
            market_active=active,
            require_nonempty_active=self.require_nonempty_active_book,
        ):
            raise ValueError("book anomaly detected")

        self.book_store.upsert(
            market_id=event.market_id,
            token_id=str(event.token_id),
            bids=bids,
            asks=asks,
            recv_ts=event.recv_ts,
            exchange_ts=event.exchange_ts,
            active=active,
            require_nonempty_if_active=self.require_nonempty_active_book,
        )

    async def _handle_book_event(self, event: NormalizedEvent) -> bool:
        try:
            self._apply_book(event)
            return True
        except Exception:
            self.log.exception("book anomaly; triggering resync")
            self._paused_markets.add(event.market_id)
            await self.resync_market(event.market_id)
            return False

    async def resync_market(self, market_id: str) -> None:
        meta = self.registry.get_binary_market(market_id)
        if meta is None:
            self.log.error("resync skipped: missing/invalid market mapping", extra={"market_id": market_id})
            self._paused_markets.add(market_id)
            return

        self._paused_markets.add(market_id)
        if aiohttp is None:
            raise RuntimeError("aiohttp package is required for market resync")
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            snapshots = await asyncio.gather(
                fetch_token_orderbook(
                    self.rest_url,
                    meta.yes_token_id,
                    session=session,
                    max_level_size=self.snapshot_max_level_size,
                ),
                fetch_token_orderbook(
                    self.rest_url,
                    meta.no_token_id,
                    session=session,
                    max_level_size=self.snapshot_max_level_size,
                ),
                return_exceptions=True,
            )

        for snap in snapshots:
            if isinstance(snap, Exception):
                self.log.error("resync snapshot failed", extra={"market_id": market_id})
                return
            if is_book_anomalous(
                snap.get("bids", []),
                snap.get("asks", []),
                bool(snap.get("market_active", True)),
                self.require_nonempty_active_book,
            ):
                self.log.error("resync snapshot anomalous", extra={"market_id": market_id})
                return
            self.book_store.upsert(
                market_id=market_id,
                token_id=str(snap["token_id"]),
                bids=snap.get("bids", []),
                asks=snap.get("asks", []),
                recv_ts=time.time(),
                exchange_ts=int(snap.get("ts")) if snap.get("ts") else None,
                active=bool(snap.get("market_active", True)),
                require_nonempty_if_active=self.require_nonempty_active_book,
            )
        self._paused_markets.discard(market_id)
        self.log.info("market resynced", extra={"market_id": market_id})
