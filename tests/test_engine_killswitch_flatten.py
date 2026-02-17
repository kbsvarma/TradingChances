import asyncio
import time
from types import SimpleNamespace

from polymarket_arb.book import BookStore
from polymarket_arb.config import load_config
from polymarket_arb.engine import TradingEngine
from polymarket_arb.types import EngineState, OrderStatus


class DummyOrderManager:
    def __init__(self):
        self.intents = []
        self.orders_by_client_id = {
            "o1": SimpleNamespace(
                status=OrderStatus.SENT,
                market_id="m1",
                token_id="yes",
                client_order_id="o1",
            )
        }

    async def process_intent(self, intent, risk_breach=False):
        self.intents.append((intent.intent_type.value, intent.side, intent.market_id, intent.token_id, risk_breach))
        return SimpleNamespace(accepted=True, client_order_id="oX")


def test_health_loop_uses_flatten_all_on_killswitch(monkeypatch):
    cfg = load_config("config.yaml")
    cfg.trading_safety.flatten_mode = "cancel_only"
    e = TradingEngine(cfg)
    e.risk.state = EngineState.RUNNING
    monkeypatch.setattr(type(e.risk), "evaluate_circuit_breakers", lambda self: (True, "test_trip"))

    called = {"flatten": 0}

    async def fake_flatten():
        called["flatten"] += 1

    monkeypatch.setattr(e, "_flatten_all", fake_flatten)

    async def run_once():
        t = asyncio.create_task(e._health_loop())
        await asyncio.sleep(1.15)
        e._stop.set()
        await asyncio.sleep(0.05)
        t.cancel()

    asyncio.run(run_once())
    assert called["flatten"] >= 1


def test_flatten_cancel_and_unwind_issues_unwind_intent():
    cfg = load_config("config.yaml")
    cfg.trading_safety.flatten_mode = "cancel_and_unwind"
    e = TradingEngine(cfg)
    e.order_manager = DummyOrderManager()
    e.book_store = BookStore()
    e.book_store.upsert("m1", "yes", [{"price": 0.4995, "size": 10}], [{"price": 0.5, "size": 10}], time.time(), None, active=True, require_nonempty_if_active=False)
    e.risk.positions = {"m1:yes": SimpleNamespace(market_id="m1", token_id="yes", qty=2.0, avg_price=0.4)}

    asyncio.run(e._flatten_all())

    kinds = [x[0] for x in e.order_manager.intents]
    assert "cancel" in kinds
    assert "place" in kinds
    place = [x for x in e.order_manager.intents if x[0] == "place"][-1]
    assert place[1] == "sell"
    assert place[4] is True
