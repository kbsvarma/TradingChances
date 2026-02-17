import asyncio

from polymarket_arb.config import OrderConfig
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
            "post_order": {"burst_tokens": 100, "burst_window_sec": 1, "sustained_tokens": 1000, "sustained_window_sec": 10},
            "delete_order": {"burst_tokens": 100, "burst_window_sec": 1, "sustained_tokens": 1000, "sustained_window_sec": 10},
            "adaptive_backoff_base_ms": 1,
            "adaptive_backoff_max_ms": 2,
        }
    )


def test_semantic_dedupe():
    async def run() -> None:
        om = OrderManager(mk_cfg(), DummyExecution(), mk_rl(), Normalizer())
        intent = Intent(IntentType.PLACE, "m1", "t1", side="buy", price=0.5, size=1.0, ttl_ms=1000, maker_or_taker="maker")
        d1 = await om.process_intent(intent)
        d2 = await om.process_intent(intent)
        assert d1.accepted
        assert not d2.accepted
        assert d2.reason in {"intent_duplicate", "semantic_duplicate"}

    asyncio.run(run())


def test_cancel_churn_governor():
    async def run() -> None:
        om = OrderManager(mk_cfg(), DummyExecution(), mk_rl(), Normalizer())
        intent = Intent(IntentType.PLACE, "m1", "t1", side="buy", price=0.5, size=1.0, ttl_ms=1000, maker_or_taker="maker")
        d = await om.process_intent(intent)
        assert d.accepted and d.client_order_id
        order_id = d.client_order_id

        await asyncio.sleep(0.12)
        c1 = await om.process_intent(Intent(IntentType.CANCEL, "m1", "t1", order_id=order_id))
        c2 = await om.process_intent(Intent(IntentType.CANCEL, "m1", "t1", order_id=order_id))
        assert c1.accepted
        assert not c2.accepted

    asyncio.run(run())


def test_ttl_auto_cancel_marks_expired():
    async def run() -> None:
        om = OrderManager(mk_cfg(), DummyExecution(), mk_rl(), Normalizer())
        d = await om.process_intent(Intent(IntentType.PLACE, "m1", "t1", side="buy", price=0.5, size=1.0, ttl_ms=50, maker_or_taker="maker"))
        assert d.accepted and d.client_order_id
        await asyncio.sleep(0.11)
        canceled = await om.auto_cancel_expired()
        assert d.client_order_id in canceled
        assert om.orders_by_client_id[d.client_order_id].status.value in {"EXPIRED", "CANCEL_SENT"}

    asyncio.run(run())


def test_replace_fallback_cancel_then_new():
    async def run() -> None:
        om = OrderManager(mk_cfg(), DummyExecution(), mk_rl(), Normalizer())
        d1 = await om.process_intent(Intent(IntentType.PLACE, "m1", "t1", side="buy", price=0.5, size=1.0, ttl_ms=1000, maker_or_taker="maker"))
        assert d1.accepted and d1.client_order_id
        await asyncio.sleep(0.11)
        d2 = await om.process_intent(Intent(IntentType.PLACE, "m1", "t1", side="buy", price=0.49, size=1.0, ttl_ms=1000, maker_or_taker="maker"))
        assert d2.accepted
        assert d2.client_order_id != d1.client_order_id
        assert om.orders_by_client_id[d1.client_order_id].status.value in {"CANCEL_SENT", "CANCELED", "EXPIRED"}

    asyncio.run(run())
