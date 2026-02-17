from __future__ import annotations

import time
from typing import Any

from polymarket_arb.types import EventType, NormalizedEvent


class Normalizer:
    def __init__(self, tick_size: float = 0.001, min_order_size: float = 1.0) -> None:
        self.tick_size = tick_size
        self.min_order_size = min_order_size

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

    def validate_order(self, price: float, size: float) -> bool:
        if size < self.min_order_size:
            return False
        ticks = round(price / self.tick_size)
        return abs((ticks * self.tick_size) - price) < 1e-9
