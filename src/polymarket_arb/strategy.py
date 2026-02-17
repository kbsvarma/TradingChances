from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from polymarket_arb.market_rules import MarketRulesProvider
from polymarket_arb.types import BookState, Intent, IntentType, Position


@dataclass(slots=True)
class StrategyParams:
    min_edge_threshold: float
    failure_buffer: float
    max_slippage_bps: float
    ttl_ms: int


class SlippageModel:
    def estimate(self, book: BookState, side: str, size: float) -> float:
        levels = book.asks if side == "buy" else book.bids
        if not levels:
            return 1.0
        remaining = size
        weighted = 0.0
        filled = 0.0
        for level in levels:
            take = min(level.size, remaining)
            weighted += take * level.price
            filled += take
            remaining -= take
            if remaining <= 0:
                break
        if filled == 0:
            return 1.0
        avg = weighted / filled
        ref = levels[0].price
        return abs(avg - ref)


class Strategy:
    """Deterministic strategy: no randomness, no LLM."""

    def __init__(
        self,
        params: StrategyParams,
        slippage_model: SlippageModel,
        rules_provider: MarketRulesProvider,
        slippage_buffer_provider: Callable[[str], float] | None = None,
    ) -> None:
        self.params = params
        self.slippage_model = slippage_model
        self.rules_provider = rules_provider
        self.slippage_buffer_provider = slippage_buffer_provider

    async def compute_intents(
        self,
        book_yes: BookState | None,
        book_no: BookState | None,
        positions: dict[str, Position],
        market_id: str,
        token_yes: str,
        token_no: str,
    ) -> list[Intent]:
        if book_yes is None or book_no is None:
            return [Intent(IntentType.NOOP, market_id, token_yes, reason="missing_book")]
        if not book_yes.active or not book_no.active:
            return [Intent(IntentType.NOOP, market_id, token_yes, reason="market_inactive")]

        best_yes = book_yes.best_ask()
        best_no = book_no.best_ask()
        if best_yes is None or best_no is None:
            return [Intent(IntentType.NOOP, market_id, token_yes, reason="empty_book")]

        fee_rate = await self.rules_provider.get_fee_rate(market_id, token_yes)
        size = self.rules_provider.get_min_order_size(market_id, token_yes)
        slip_yes = self.slippage_model.estimate(book_yes, "buy", size)
        slip_no = self.slippage_model.estimate(book_no, "buy", size)
        slippage = slip_yes + slip_no

        adaptive_buffer = self.params.failure_buffer
        if self.slippage_buffer_provider is not None:
            adaptive_buffer = max(adaptive_buffer, float(self.slippage_buffer_provider(market_id)))

        executable_edge = 1 - (best_yes + best_no) - fee_rate - slippage - adaptive_buffer
        if executable_edge <= self.params.min_edge_threshold:
            return [Intent(IntentType.NOOP, market_id, token_yes, reason="edge_below_threshold")]

        return [
            Intent(
                intent_type=IntentType.PLACE,
                market_id=market_id,
                token_id=token_yes,
                side="buy",
                price=best_yes,
                size=size,
                ttl_ms=self.params.ttl_ms,
                maker_or_taker="maker",
                reason=f"edge={executable_edge:.6f}",
            ),
            Intent(
                intent_type=IntentType.PLACE,
                market_id=market_id,
                token_id=token_no,
                side="buy",
                price=best_no,
                size=size,
                ttl_ms=self.params.ttl_ms,
                maker_or_taker="maker",
                reason=f"edge={executable_edge:.6f}",
            ),
        ]
