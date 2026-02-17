import asyncio
import time
from types import SimpleNamespace

from polymarket_arb.book import BookStore
from polymarket_arb.config import load_config
from polymarket_arb.engine import TradingEngine
from polymarket_arb.types import EngineState, Intent, IntentType, OrderStatus


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
        self.intents.append((intent.intent_type.value, intent.side, intent.market_id, risk_breach))
        return SimpleNamespace(accepted=True, client_order_id="oX")

    def live_open_orders_count(self, market_id):
        return 0


def test_decision_cycle_skips_new_orders_in_flattening(monkeypatch):
    cfg = load_config("config.yaml")
    cfg.markets = ["market_1"]
    e = TradingEngine(cfg)
    e.risk.state = EngineState.FLATTENING
    e.order_manager = DummyOrderManager()

    async def bad_strategy(*args, **kwargs):
        raise AssertionError("strategy should not run while flattening")

    monkeypatch.setattr(e.strategy, "compute_intents", bad_strategy)
    asyncio.run(e._run_decision_cycle("market_1", time.time()))
    assert e.order_manager.intents == []


def test_flattening_unwind_still_allowed():
    cfg = load_config("config.yaml")
    cfg.trading_safety.flatten_mode = "cancel_and_unwind"
    e = TradingEngine(cfg)
    e.order_manager = DummyOrderManager()
    e.book_store = BookStore()
    e.risk.state = EngineState.FLATTENING
    e.risk.positions = {"m1:yes": SimpleNamespace(market_id="m1", token_id="yes", qty=2.0, avg_price=0.4)}
    e.book_store.upsert(
        "m1",
        "yes",
        [{"price": 0.4995, "size": 5.0}],
        [{"price": 0.5, "size": 5.0}],
        time.time(),
        None,
        active=True,
        require_nonempty_if_active=False,
    )

    asyncio.run(e._flatten_all())
    kinds = [x[0] for x in e.order_manager.intents]
    assert "cancel" in kinds
    assert "place" in kinds
