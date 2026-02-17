from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from polymarket_arb.command_bus import CommandBus
from polymarket_arb.types import Command, CommandType


class CommandAPI(Protocol):
    async def run(self, bus: CommandBus) -> None:
        ...


class CLICommandAPI:
    """Minimal control interface; Telegram can implement same protocol later."""

    def __init__(self) -> None:
        self.log = logging.getLogger("CLICommandAPI")

    async def run(self, bus: CommandBus) -> None:
        self.log.info("command api ready: pause|resume|flatten(cancel mode from config)|reload|set|backtest|stop")
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, input, "> ")
            cmd = line.strip().lower()
            if cmd == "pause":
                await bus.publish(Command(CommandType.PAUSE))
            elif cmd == "resume":
                await bus.publish(Command(CommandType.RESUME))
            elif cmd == "flatten":
                await bus.publish(Command(CommandType.FLATTEN))
            elif cmd == "reload":
                await bus.publish(Command(CommandType.RELOAD_CONFIG))
            elif cmd.startswith("markets on "):
                m = [x.strip() for x in cmd.replace("markets on ", "").split(",") if x.strip()]
                await bus.publish(Command(CommandType.MARKETS_ON, {"markets": m}))
            elif cmd.startswith("markets off "):
                m = [x.strip() for x in cmd.replace("markets off ", "").split(",") if x.strip()]
                await bus.publish(Command(CommandType.MARKETS_OFF, {"markets": m}))
            elif cmd.startswith("set "):
                # Example: set min_edge_threshold=0.01 failure_buffer=0.002
                tokens = cmd.replace("set ", "").split()
                payload: dict[str, float | int | str] = {}
                for t in tokens:
                    if "=" not in t:
                        continue
                    k, v = t.split("=", 1)
                    if "." in v:
                        payload[k] = float(v)
                    else:
                        try:
                            payload[k] = int(v)
                        except ValueError:
                            payload[k] = v
                await bus.publish(Command(CommandType.SET_PARAMS, payload))
            elif cmd == "backtest":
                await bus.publish(Command(CommandType.BACKTEST))
            elif cmd == "stop":
                await bus.publish(Command(CommandType.STOP))
                return
            else:
                self.log.warning("unknown command")
