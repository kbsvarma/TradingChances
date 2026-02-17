from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from polymarket_arb.types import Command


CommandHandler = Callable[[Command], Awaitable[None]]


class CommandBus:
    """In-process command queue abstraction (Redis Pub/Sub compatible interface)."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Command] = asyncio.Queue()
        self._handlers: list[CommandHandler] = []

    async def publish(self, cmd: Command) -> None:
        await self._queue.put(cmd)

    def subscribe(self, handler: CommandHandler) -> None:
        self._handlers.append(handler)

    async def run_forever(self) -> None:
        while True:
            cmd = await self._queue.get()
            for handler in self._handlers:
                await handler(cmd)
