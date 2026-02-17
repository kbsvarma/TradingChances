from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable


class ExecutionAdapter:
    """Thin wrapper around py-clob-client calls; deterministic and strategy-free."""

    def __init__(self, dry_run: bool, env: dict[str, str], max_in_flight: int = 8) -> None:
        self.dry_run = dry_run
        self.env = env
        self.log = logging.getLogger("ExecutionAdapter")
        self._clob = None
        self._sem = asyncio.Semaphore(max_in_flight)
        if not dry_run:
            self._init_client()

    def _init_client(self) -> None:
        try:
            from py_clob_client.client import ClobClient  # type: ignore

            self._clob = ClobClient(
                host=self.env["CLOB_REST_URL"],
                chain_id=int(self.env["CHAIN_ID"]),
                key=self.env["PRIVATE_KEY"],
                signature_type=self.env["SIGNATURE_TYPE"],
            )
            if self.env["CLOB_API_KEY"]:
                self._clob.set_api_creds(
                    self.env["CLOB_API_KEY"],
                    self.env["CLOB_API_SECRET"],
                    self.env["CLOB_API_PASSPHRASE"],
                )
        except Exception as exc:
            self.log.error("failed to init py-clob-client; forcing dry_run: %s", exc)
            self.dry_run = True
            self._clob = None

    async def _call_sync(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        async with self._sem:
            return await asyncio.to_thread(fn, *args, **kwargs)

    async def place_order(
        self,
        market_id: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
        client_order_id: str,
        ttl_ms: int,
    ) -> dict[str, Any]:
        if self.dry_run or self._clob is None:
            return {
                "ok": True,
                "status_code": 200,
                "order_id": f"dry-{client_order_id}",
                "client_order_id": client_order_id,
                "sent_ts": time.time(),
            }

        try:
            response = await self._call_sync(
                self._clob.create_order,  # type: ignore[attr-defined]
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                client_order_id=client_order_id,
                expiration_ms=ttl_ms,
            )
            return {
                "ok": True,
                "status_code": 200,
                "order_id": response.get("orderID") or response.get("id"),
                "client_order_id": client_order_id,
                "raw": response,
                "sent_ts": time.time(),
            }
        except Exception as exc:
            return {
                "ok": False,
                "status_code": 500,
                "error": str(exc),
                "client_order_id": client_order_id,
                "sent_ts": time.time(),
            }

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        if self.dry_run or self._clob is None:
            return {"ok": True, "status_code": 200, "order_id": order_id, "sent_ts": time.time()}

        try:
            response = await self._call_sync(self._clob.cancel, order_id)  # type: ignore[attr-defined]
            return {"ok": True, "status_code": 200, "order_id": order_id, "raw": response, "sent_ts": time.time()}
        except Exception as exc:
            return {"ok": False, "status_code": 500, "order_id": order_id, "error": str(exc), "sent_ts": time.time()}
