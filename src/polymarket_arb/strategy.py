from __future__ import annotations

from dataclasses import dataclass

from polymarket_arb.types import BookState, Intent, IntentType, Position


@dataclass(slots=True)
class StrategyParams:
    min_edge_threshold: float
    failure_buffer: float
    default_fee_rate: float
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


class FeeProvider:
    def __init__(self, default_fee_rate: float) -> None:
        self.default_fee_rate = default_fee_rate
        self._cache: dict[str, float] = {}

    async def get_fee_rate(self, market_id: str) -> float:
        return self._cache.get(market_id, self.default_fee_rate)


class Strategy:
    """Deterministic strategy: no randomness, no LLM."""

    def __init__(self, params: StrategyParams, slippage_model: SlippageModel, fee_provider: FeeProvider) -> None:
        self.params = params
        self.slippage_model = slippage_model
        self.fee_provider = fee_provider

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

        fee_rate = await self.fee_provider.get_fee_rate(market_id)
        size = 1.0
        slip_yes = self.slippage_model.estimate(book_yes, "buy", size)
        slip_no = self.slippage_model.estimate(book_no, "buy", size)
        slippage = slip_yes + slip_no

        executable_edge = 1 - (best_yes + best_no) - fee_rate - slippage - self.params.failure_buffer
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
