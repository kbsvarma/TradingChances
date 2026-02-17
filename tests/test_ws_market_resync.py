import asyncio
import time

from polymarket_arb.book import BookStore
from polymarket_arb.market_registry import MarketMeta, MarketRegistry
from polymarket_arb.market_rules import MarketRulesProvider
from polymarket_arb.normalizer import Normalizer
from polymarket_arb.types import EventType, NormalizedEvent
from polymarket_arb.ws_market import MarketWSClient


class MarketWSClientProbe(MarketWSClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.resync_calls = []

    async def resync_market(self, market_id: str) -> None:
        self.resync_calls.append(market_id)


def test_invariant_failure_triggers_resync_state():
    registry = MarketRegistry({"m1": MarketMeta("m1", "yes", "no", 0.01, 0.1, 0.002)})
    rules = MarketRulesProvider(registry)
    normalizer = Normalizer(rules)
    client = MarketWSClientProbe(
        ws_url="wss://x",
        rest_url="https://x",
        markets=["m1"],
        registry=registry,
        normalizer=normalizer,
        out_queue=asyncio.Queue(),
        book_store=BookStore(),
    )

    bad = NormalizedEvent(
        event_type=EventType.ORDER_BOOK_UPDATE,
        market_id="m1",
        token_id="yes",
        payload={
            "bids": [{"price": 0.6, "size": 1.0}],
            "asks": [{"price": 0.5, "size": 1.0}],
            "market_active": True,
        },
        recv_ts=time.time(),
    )

    ok = asyncio.run(client._handle_book_event(bad))
    assert not ok
    assert "m1" in client._paused_markets
    assert client.resync_calls == ["m1"]
