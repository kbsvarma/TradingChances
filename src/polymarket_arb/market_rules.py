from __future__ import annotations

from dataclasses import dataclass

from polymarket_arb.market_registry import MarketRegistry


@dataclass(slots=True)
class MarketRulesProvider:
    registry: MarketRegistry
    default_tick_size: float = 0.001
    default_min_order_size: float = 1.0
    default_fee_rate: float = 0.002

    def _meta_by_market_or_token(self, market_id: str | None, token_id: str | None):
        if market_id:
            return self.registry.get(market_id)
        if token_id:
            mid = self.registry.get_market_id_by_token(token_id)
            if mid:
                return self.registry.get(mid)
        return None

    def get_tick_size(self, market_id: str | None, token_id: str | None) -> float:
        meta = self._meta_by_market_or_token(market_id, token_id)
        return meta.tick_size if meta else self.default_tick_size

    def get_min_order_size(self, market_id: str | None, token_id: str | None) -> float:
        meta = self._meta_by_market_or_token(market_id, token_id)
        return meta.min_order_size if meta else self.default_min_order_size

    async def get_fee_rate(self, market_id: str, token_id: str | None = None) -> float:
        meta = self._meta_by_market_or_token(market_id, token_id)
        return meta.fee_rate if meta else self.default_fee_rate

    def quantize_price(self, market_id: str | None, token_id: str | None, price: float) -> tuple[float, int]:
        tick = self.get_tick_size(market_id, token_id)
        ticks = int(round(price / tick))
        return ticks * tick, ticks

    def quantize_size(self, market_id: str | None, token_id: str | None, size: float) -> tuple[float, int]:
        step = self.get_min_order_size(market_id, token_id)
        units = int(round(size / step))
        return max(step, units * step), units
