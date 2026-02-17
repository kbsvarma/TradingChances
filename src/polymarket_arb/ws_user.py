from __future__ import annotations

import asyncio
import json
import logging

try:
    import websockets
except ModuleNotFoundError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]

from polymarket_arb.types import NormalizedEvent
from polymarket_arb.wss_auth import build_user_subscribe_payload, redact_payload


class UserWSClient:
    """Private user websocket client (acks/fills/rejects are source of truth)."""

    def __init__(
        self,
        ws_url: str,
        auth: dict[str, str],
        normalizer,
        out_queue: asyncio.Queue[NormalizedEvent],
        on_user_event=None,
    ) -> None:
        self.ws_url = ws_url
        self.auth = auth
        self.normalizer = normalizer
        self.out_queue = out_queue
        self.on_user_event = on_user_event
        self.log = logging.getLogger("UserWSClient")
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets package is required for UserWSClient")
        backoff = 1
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=10, ping_timeout=10) as ws:
                    await self._subscribe(ws)
                    self.log.info("user ws connected")
                    backoff = 1
                    async for raw in ws:
                        msg = json.loads(raw)
                        event = self.normalizer.from_user_ws(msg)
                        if event is not None:
                            if self.on_user_event is not None:
                                self.on_user_event()
                            await self.out_queue.put(event)
                        if self._stop.is_set():
                            break
            except Exception:
                self.log.exception("user ws disconnected")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _subscribe(self, ws) -> None:
        payload = build_user_subscribe_payload(
            api_key=self.auth.get("api_key", ""),
            secret=self.auth.get("api_secret", ""),
            passphrase=self.auth.get("api_passphrase", ""),
        )
        self.log.info("user ws subscribe payload prepared: %s", redact_payload(payload))
        await ws.send(json.dumps(payload))
