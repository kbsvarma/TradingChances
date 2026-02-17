from __future__ import annotations

import time
from typing import Any

from polymarket_arb.market_rules import MarketRulesProvider
from polymarket_arb.types import EventType, NormalizedEvent


class Normalizer:
    def __init__(self, rules_provider: MarketRulesProvider) -> None:
        self.rules_provider = rules_provider

    def from_market_ws(self, raw: dict[str, Any]) -> NormalizedEvent | None:
        recv_ts = time.time()
        event = raw.get("event")
        market_id = str(raw.get("market", ""))
        token_id = str(raw.get("asset_id", ""))
        if event in {"book", "price_change", "snapshot"}:
            return NormalizedEvent(
                event_type=EventType.ORDER_BOOK_UPDATE,
                market_id=market_id,
                token_id=token_id,
                payload=raw,
                recv_ts=recv_ts,
                exchange_ts=raw.get("timestamp"),
                correlation_id=raw.get("id"),
            )
        if event == "health":
            return NormalizedEvent(
                event_type=EventType.WS_HEALTH,
                market_id=market_id,
                token_id=token_id,
                payload=raw,
                recv_ts=recv_ts,
                exchange_ts=raw.get("timestamp"),
                correlation_id=raw.get("id"),
            )
        return None

    def from_user_ws(self, raw: dict[str, Any]) -> NormalizedEvent | None:
        recv_ts = time.time()
        event = str(raw.get("event", "")).lower()
        market_id = str(raw.get("market", ""))
        token_id = str(raw.get("asset_id", "")) if raw.get("asset_id") else None
        mapping = {
            "order": EventType.ORDER_ACK,
            "fill": EventType.FILL,
            "cancel": EventType.CANCEL,
            "reject": EventType.REJECT,
        }
        etype = mapping.get(event)
        if etype is None:
            return None
        return NormalizedEvent(
            event_type=etype,
            market_id=market_id,
            token_id=token_id,
            payload=raw,
            recv_ts=recv_ts,
            exchange_ts=raw.get("timestamp"),
            correlation_id=raw.get("id") or raw.get("client_order_id"),
        )

    def validate_order(self, market_id: str, token_id: str, price: float, size: float) -> bool:
        min_size = self.rules_provider.get_min_order_size(market_id, token_id)
        if size < min_size:
            return False
        tick_size = self.rules_provider.get_tick_size(market_id, token_id)
        ticks = round(price / tick_size)
        return abs((ticks * tick_size) - price) < 1e-9
