import asyncio

from polymarket_arb.config import OrderConfig
from polymarket_arb.market_registry import MarketMeta, MarketRegistry
from polymarket_arb.market_rules import MarketRulesProvider
from polymarket_arb.normalizer import Normalizer
from polymarket_arb.order_manager import OrderManager
from polymarket_arb.rate_limiter import RateLimiter
from polymarket_arb.types import Intent, IntentType


class DummyExecution:
    async def place_order(self, **kwargs):
        return {
            "ok": True,
            "status_code": 200,
            "order_id": f"venue-{kwargs['client_order_id']}",
            "client_order_id": kwargs["client_order_id"],
        }

    async def cancel_order(self, order_id: str):
        return {"ok": True, "status_code": 200, "order_id": order_id}


def mk_cfg() -> OrderConfig:
    return OrderConfig(
        default_ttl_ms=150,
        min_order_lifetime_ms=100,
        max_cancels_per_sec_per_market=1,
        intent_price_epsilon=0.0001,
        intent_size_epsilon=0.1,
    )


def mk_rl() -> RateLimiter:
    return RateLimiter(
        {
            "global": {"tokens": 1000, "window_sec": 10},
            "post_order": {"burst_tokens": 100, "burst_window_sec": 1, "sustained_tokens": 1000, "sustained_window_sec": 10},
            "delete_order": {"burst_tokens": 100, "burst_window_sec": 1, "sustained_tokens": 1000, "sustained_window_sec": 10},
            "adaptive_backoff_base_ms": 1,
            "adaptive_backoff_max_ms": 2,
        }
    )


def mk_rules() -> MarketRulesProvider:
    reg = MarketRegistry(
        {
            "m1": MarketMeta("m1", "yes", "no", tick_size=0.01, min_order_size=0.1, fee_rate=0.002),
        }
    )
    return MarketRulesProvider(registry=reg, default_tick_size=0.01, default_min_order_size=0.1, default_fee_rate=0.002)


def test_semantic_dedupe_quantized():
    async def run() -> None:
        rules = mk_rules()
        om = OrderManager(mk_cfg(), DummyExecution(), mk_rl(), Normalizer(rules), rules)
        i1 = Intent(IntentType.PLACE, "m1", "yes", side="buy", price=0.501, size=0.11, ttl_ms=1000, maker_or_taker="maker")
        i2 = Intent(IntentType.PLACE, "m1", "yes", side="buy", price=0.499, size=0.09, ttl_ms=1000, maker_or_taker="maker")
        d1 = await om.process_intent(i1)
        d2 = await om.process_intent(i2)
        assert d1.accepted
        assert not d2.accepted
        assert d2.reason in {"intent_duplicate", "semantic_duplicate"}

    asyncio.run(run())


def test_cancel_churn_governor():
    async def run() -> None:
        rules = mk_rules()
        om = OrderManager(mk_cfg(), DummyExecution(), mk_rl(), Normalizer(rules), rules)
        intent = Intent(IntentType.PLACE, "m1", "yes", side="buy", price=0.5, size=0.2, ttl_ms=1000, maker_or_taker="maker")
        d = await om.process_intent(intent)
        assert d.accepted and d.client_order_id
        order_id = d.client_order_id

        await asyncio.sleep(0.12)
        c1 = await om.process_intent(Intent(IntentType.CANCEL, "m1", "yes", order_id=order_id))
        c2 = await om.process_intent(Intent(IntentType.CANCEL, "m1", "yes", order_id=order_id))
        assert c1.accepted
        assert not c2.accepted

    asyncio.run(run())


def test_ttl_auto_cancel_marks_expired():
    async def run() -> None:
        rules = mk_rules()
        om = OrderManager(mk_cfg(), DummyExecution(), mk_rl(), Normalizer(rules), rules)
        d = await om.process_intent(Intent(IntentType.PLACE, "m1", "yes", side="buy", price=0.5, size=0.2, ttl_ms=50, maker_or_taker="maker"))
        assert d.accepted and d.client_order_id
        await asyncio.sleep(0.11)
        canceled = await om.auto_cancel_expired()
        assert d.client_order_id in canceled
        assert om.orders_by_client_id[d.client_order_id].status.value in {"EXPIRED", "CANCEL_SENT"}

    asyncio.run(run())


def test_filled_state_not_overwritten():
    async def run() -> None:
        rules = mk_rules()
        om = OrderManager(mk_cfg(), DummyExecution(), mk_rl(), Normalizer(rules), rules)
        d = await om.process_intent(Intent(IntentType.PLACE, "m1", "yes", side="buy", price=0.5, size=0.2, ttl_ms=1000, maker_or_taker="maker"))
        assert d.accepted and d.client_order_id
        om.on_fill(d.client_order_id, 0.2)
        assert om.orders_by_client_id[d.client_order_id].status.value == "FILLED"

    asyncio.run(run())


def test_intent_dedupe_ttl_expires():
    async def run() -> None:
        rules = mk_rules()
        om = OrderManager(mk_cfg(), DummyExecution(), mk_rl(), Normalizer(rules), rules)
        intent = Intent(IntentType.PLACE, "m1", "yes", side="buy", price=0.5, size=0.2, ttl_ms=1000, maker_or_taker="maker")
        d1 = await om.process_intent(intent)
        assert d1.accepted and d1.client_order_id
        d2 = await om.process_intent(intent)
        assert not d2.accepted
        om.on_cancel(d1.client_order_id)
        await asyncio.sleep(2.05)
        d3 = await om.process_intent(intent)
        assert d3.accepted

    asyncio.run(run())
