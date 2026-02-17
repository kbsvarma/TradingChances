import asyncio
import time

from polymarket_arb.config import load_config
from polymarket_arb.engine import TradingEngine
from polymarket_arb.types import EngineState


def test_user_ws_watchdog_triggers_flatten_and_safe(monkeypatch):
    cfg = load_config("config.yaml")
    engine = TradingEngine(cfg)
    engine.risk.state = EngineState.RUNNING
    engine.user_ws_watchdog.timeout_sec = 0.1
    engine.user_ws_watchdog.touch(time.time() - 5)

    monkeypatch.setattr(type(engine.risk), "evaluate_circuit_breakers", lambda self: (False, "ok"))

    called = {"flatten": 0}

    async def fake_flatten():
        called["flatten"] += 1

    async def fake_record_error(*args, **kwargs):
        return None

    monkeypatch.setattr(engine, "_flatten_all", fake_flatten)
    monkeypatch.setattr(engine.persistence, "record_error", fake_record_error)

    async def run_once():
        task = asyncio.create_task(engine._health_loop())
        await asyncio.sleep(1.2)
        engine._stop.set()
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(run_once())
    assert called["flatten"] >= 1
    assert engine.risk.state == EngineState.SAFE
