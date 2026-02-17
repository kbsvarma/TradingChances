from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

try:
    import aiosqlite
except ModuleNotFoundError:  # pragma: no cover - fallback for lightweight test envs
    aiosqlite = None  # type: ignore[assignment]


class Persistence:
    def __init__(
        self,
        db_path: str,
        flush_interval_sec: int = 2,
        buffer_maxsize: int = 100000,
        buffer_high_watermark: int = 80000,
    ) -> None:
        self.db_path = db_path
        self.flush_interval_sec = flush_interval_sec
        self.buffer_high_watermark = buffer_high_watermark
        self.log = logging.getLogger("Persistence")
        self.queue: asyncio.Queue[tuple[str, tuple[Any, ...]]] = asyncio.Queue(maxsize=buffer_maxsize)
        self._stop = asyncio.Event()
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if aiosqlite is None:
            raise RuntimeError("aiosqlite is required for persistence init")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL;")
        migration = Path("migrations/001_init.sql").read_text()
        await self._db.executescript(migration)
        await self._db.commit()

    async def stop(self, flush_timeout_sec: int = 3) -> None:
        self._stop.set()
        await self.flush_with_timeout(flush_timeout_sec)
        if self._db is not None:
            await self._db.close()

    async def run_writer(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.flush_interval_sec)
            await self.flush_once()

    async def flush_with_timeout(self, timeout_sec: int) -> None:
        try:
            await asyncio.wait_for(self.flush_once(), timeout=timeout_sec)
        except TimeoutError:
            self.log.error("flush timeout")

    async def flush_once(self) -> None:
        if self._db is None:
            return
        batch: list[tuple[str, tuple[Any, ...]]] = []
        while True:
            try:
                batch.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not batch:
            return
        await self._db.execute("BEGIN")
        for sql, params in batch:
            await self._db.execute(sql, params)
        await self._db.commit()

    async def _enqueue(self, sql: str, params: tuple[Any, ...]) -> None:
        await self.queue.put((sql, params))
        if self.queue.qsize() >= self.buffer_high_watermark:
            self.log.warning("persistence high watermark reached; emergency flush")
            await self.flush_once()

    async def record_event(self, event_type: str, market_id: str | None, token_id: str | None, payload: dict[str, Any], correlation_id: str | None = None) -> None:
        await self._enqueue(
            "INSERT INTO events(ts,event_type,market_id,token_id,correlation_id,payload_json) VALUES(?,?,?,?,?,?)",
            (time.time(), event_type, market_id, token_id, correlation_id, json.dumps(payload, separators=(",", ":"))),
        )

    async def record_intent(self, market_id: str, token_id: str, intent_type: str, payload: dict[str, Any]) -> None:
        await self._enqueue(
            "INSERT INTO order_intents(ts,market_id,token_id,intent_type,payload_json) VALUES(?,?,?,?,?)",
            (time.time(), market_id, token_id, intent_type, json.dumps(payload, separators=(",", ":"))),
        )

    async def upsert_order(self, order: dict[str, Any]) -> None:
        await self._enqueue(
            """
            INSERT INTO orders(client_order_id,venue_order_id,market_id,token_id,side,price,size,remaining_size,status,created_ts,last_update_ts,ttl_ms)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(client_order_id) DO UPDATE SET
              venue_order_id=excluded.venue_order_id,
              remaining_size=excluded.remaining_size,
              status=excluded.status,
              last_update_ts=excluded.last_update_ts
            """,
            (
                order["client_order_id"],
                order.get("venue_order_id"),
                order["market_id"],
                order["token_id"],
                order["side"],
                order["price"],
                order["size"],
                order["remaining_size"],
                order["status"],
                order["created_ts"],
                order["last_update_ts"],
                order["ttl_ms"],
            ),
        )

    async def record_fill(self, fill: dict[str, Any]) -> None:
        await self._enqueue(
            "INSERT INTO fills(ts,market_id,token_id,side,price,size,order_id,client_order_id) VALUES(?,?,?,?,?,?,?,?)",
            (
                fill["ts"],
                fill["market_id"],
                fill["token_id"],
                fill["side"],
                fill["price"],
                fill["size"],
                fill.get("order_id"),
                fill.get("client_order_id"),
            ),
        )

    async def upsert_position(self, key: str, market_id: str, token_id: str, qty: float, avg_price: float) -> None:
        await self._enqueue(
            """
            INSERT INTO positions(key,market_id,token_id,qty,avg_price,updated_ts)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET
              qty=excluded.qty,
              avg_price=excluded.avg_price,
              updated_ts=excluded.updated_ts
            """,
            (key, market_id, token_id, qty, avg_price, time.time()),
        )

    async def record_pnl_snapshot(self, equity: float, drawdown: float, daily_pnl: float, hourly_pnl: float) -> None:
        await self._enqueue(
            "INSERT INTO pnl_snapshots(ts,equity,drawdown,daily_pnl,hourly_pnl) VALUES(?,?,?,?,?)",
            (time.time(), equity, drawdown, daily_pnl, hourly_pnl),
        )

    async def record_latency_metric(self, metric_key: str, p50: float, p95: float, p99: float, mean: float) -> None:
        await self._enqueue(
            "INSERT INTO latency_metrics(ts,metric_key,p50,p95,p99,mean) VALUES(?,?,?,?,?,?)",
            (time.time(), metric_key, p50, p95, p99, mean),
        )

    async def record_book_snapshot(self, market_id: str, token_id: str, bids: list[dict[str, float]], asks: list[dict[str, float]]) -> None:
        await self._enqueue(
            "INSERT INTO book_snapshots(ts,market_id,token_id,bids_json,asks_json) VALUES(?,?,?,?,?)",
            (time.time(), market_id, token_id, json.dumps(bids, separators=(",", ":")), json.dumps(asks, separators=(",", ":"))),
        )

    async def record_error(self, component: str, error_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
        await self._enqueue(
            "INSERT INTO errors(ts,component,error_type,message,payload_json) VALUES(?,?,?,?,?)",
            (time.time(), component, error_type, message, json.dumps(payload or {}, separators=(",", ":"))),
        )

    async def load_events_for_replay(self, start_ts: float | None = None, end_ts: float | None = None) -> list[dict[str, Any]]:
        if self._db is None:
            raise RuntimeError("db not initialized")
        sql = "SELECT ts,event_type,market_id,token_id,correlation_id,payload_json FROM events WHERE 1=1"
        params: list[Any] = []
        if start_ts is not None:
            sql += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            sql += " AND ts <= ?"
            params.append(end_ts)
        sql += " ORDER BY ts ASC"
        cur = await self._db.execute(sql, tuple(params))
        rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "ts": row["ts"],
                    "event_type": row["event_type"],
                    "market_id": row["market_id"],
                    "token_id": row["token_id"],
                    "correlation_id": row["correlation_id"],
                    "payload": json.loads(row["payload_json"]),
                }
            )
        return out
