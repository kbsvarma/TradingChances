from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass(slots=True)
class BucketConfig:
    tokens: int
    window_sec: int


class TokenBucket:
    def __init__(self, cfg: BucketConfig) -> None:
        self.cfg = cfg
        self.tokens = float(cfg.tokens)
        self.updated = time.monotonic()
        self.rate = cfg.tokens / cfg.window_sec

    async def acquire(self, n: int = 1) -> None:
        while True:
            now = time.monotonic()
            elapsed = now - self.updated
            self.updated = now
            self.tokens = min(float(self.cfg.tokens), self.tokens + elapsed * self.rate)
            if self.tokens >= n:
                self.tokens -= n
                return
            sleep_for = (n - self.tokens) / self.rate
            await asyncio.sleep(max(0.001, sleep_for))


class RateLimiter:
    """Global + endpoint buckets with adaptive backoff on 429/5xx."""

    def __init__(self, cfg: dict) -> None:
        post = cfg["post_order"]
        delete = cfg["delete_order"]
        global_cfg = cfg.get("global", {"tokens": 100000, "window_sec": 600})
        self.global_bucket = TokenBucket(BucketConfig(global_cfg["tokens"], global_cfg["window_sec"]))
        self.post_burst = TokenBucket(BucketConfig(post["burst_tokens"], post["burst_window_sec"]))
        self.post_sustained = TokenBucket(BucketConfig(post["sustained_tokens"], post["sustained_window_sec"]))
        self.delete_burst = TokenBucket(BucketConfig(delete["burst_tokens"], delete["burst_window_sec"]))
        self.delete_sustained = TokenBucket(BucketConfig(delete["sustained_tokens"], delete["sustained_window_sec"]))
        self.backoff_base = cfg.get("adaptive_backoff_base_ms", 100)
        self.backoff_max = cfg.get("adaptive_backoff_max_ms", 5000)
        self.error_streak = 0

    async def acquire_post(self) -> None:
        await self.global_bucket.acquire(1)
        await self.post_burst.acquire(1)
        await self.post_sustained.acquire(1)
        await self._adaptive_wait()

    async def acquire_delete(self) -> None:
        await self.global_bucket.acquire(1)
        await self.delete_burst.acquire(1)
        await self.delete_sustained.acquire(1)
        await self._adaptive_wait()

    def record_response(self, status_code: int) -> None:
        if status_code == 429 or status_code >= 500:
            self.error_streak += 1
        else:
            self.error_streak = max(0, self.error_streak - 1)

    async def _adaptive_wait(self) -> None:
        if self.error_streak <= 0:
            return
        backoff_ms = min(self.backoff_max, self.backoff_base * (2 ** (self.error_streak - 1)))
        await asyncio.sleep(backoff_ms / 1000.0)
