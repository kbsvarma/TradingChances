from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass

from polymarket_arb.config import OrderConfig
from polymarket_arb.market_rules import MarketRulesProvider
from polymarket_arb.normalizer import Normalizer
from polymarket_arb.types import Intent, IntentType, ManagedOrder, OrderStatus


@dataclass(slots=True)
class OrderDecision:
    accepted: bool
    reason: str
    client_order_id: str | None = None


class OrderManager:
    def __init__(
        self,
        cfg: OrderConfig,
        execution,
        rate_limiter,
        normalizer: Normalizer,
        rules_provider: MarketRulesProvider,
    ) -> None:
        self.cfg = cfg
        self.execution = execution
        self.rate_limiter = rate_limiter
        self.normalizer = normalizer
        self.rules_provider = rules_provider
        self.log = logging.getLogger("OrderManager")

        self.orders_by_client_id: dict[str, ManagedOrder] = {}
        self.client_by_venue_id: dict[str, str] = {}
        self.semantic_index: dict[str, str] = {}
        self.cancel_windows: dict[str, deque[float]] = defaultdict(deque)
        self._intent_seen: set[str] = set()
        self._intent_seen_ts: dict[str, float] = {}
        self._intent_seen_ttl_sec = 2.0

    def _semantic_key(self, intent: Intent) -> str:
        q_price, p_ticks = self.rules_provider.quantize_price(intent.market_id, intent.token_id, float(intent.price or 0.0))
        q_size, s_units = self.rules_provider.quantize_size(intent.market_id, intent.token_id, float(intent.size or 0.0))
        _ = q_price, q_size
        return f"{intent.market_id}:{intent.token_id}:{intent.side}:{p_ticks}:{s_units}"

    def _dedupe_key(self, intent: Intent) -> str:
        if intent.intent_type == IntentType.PLACE:
            return f"{intent.intent_type}:{self._semantic_key(intent)}"
        return f"{intent.intent_type}:{intent.market_id}:{intent.token_id}:{intent.order_id}"

    async def process_intent(self, intent: Intent, risk_breach: bool = False) -> OrderDecision:
        if intent.intent_type == IntentType.NOOP:
            return OrderDecision(False, "noop")

        self._prune_intent_seen()
        dedupe = self._dedupe_key(intent)
        if dedupe in self._intent_seen:
            return OrderDecision(False, "intent_duplicate")
        self._intent_seen.add(dedupe)
        self._intent_seen_ts[dedupe] = time.time()
        if len(self._intent_seen) > 20000:
            self._intent_seen.clear()
            self._intent_seen_ts.clear()

        if intent.intent_type == IntentType.PLACE:
            return await self._handle_place(intent)
        if intent.intent_type == IntentType.CANCEL:
            return await self._handle_cancel(intent, risk_breach)
        return OrderDecision(False, "unsupported_intent")

    async def _handle_place(self, intent: Intent) -> OrderDecision:
        if None in (intent.side, intent.price, intent.size):
            return OrderDecision(False, "missing_place_fields")

        q_price, _ = self.rules_provider.quantize_price(intent.market_id, intent.token_id, float(intent.price))
        q_size, _ = self.rules_provider.quantize_size(intent.market_id, intent.token_id, float(intent.size))
        if not self.normalizer.validate_order(intent.market_id, intent.token_id, q_price, q_size):
            return OrderDecision(False, "tick_or_size_invalid")

        semantic_key = f"{intent.market_id}:{intent.token_id}:{intent.side}:{self.rules_provider.quantize_price(intent.market_id, intent.token_id, q_price)[1]}:{self.rules_provider.quantize_size(intent.market_id, intent.token_id, q_size)[1]}"
        existing = self.semantic_index.get(semantic_key)
        if existing and self._is_live(existing):
            return OrderDecision(False, "semantic_duplicate", client_order_id=existing)

        conflict = self._find_live_conflict(intent.market_id, intent.token_id, str(intent.side), q_price, q_size)
        if conflict is not None:
            cancel_decision = await self._handle_cancel(
                Intent(
                    intent_type=IntentType.CANCEL,
                    market_id=conflict.market_id,
                    token_id=conflict.token_id,
                    order_id=conflict.client_order_id,
                ),
                risk_breach=False,
            )
            if not cancel_decision.accepted:
                return OrderDecision(False, f"replace_cancel_failed:{cancel_decision.reason}", client_order_id=conflict.client_order_id)

        client_order_id = str(uuid.uuid4())
        ttl_ms = intent.ttl_ms or self.cfg.default_ttl_ms
        now = time.time()
        managed = ManagedOrder(
            client_order_id=client_order_id,
            market_id=intent.market_id,
            token_id=intent.token_id,
            side=str(intent.side),
            price=q_price,
            size=q_size,
            remaining_size=q_size,
            created_ts=now,
            ttl_ms=ttl_ms,
            status=OrderStatus.SENT,
            last_update_ts=now,
        )
        self.orders_by_client_id[client_order_id] = managed
        self.semantic_index[semantic_key] = client_order_id

        await self.rate_limiter.acquire_post()
        result = await self.execution.place_order(
            market_id=intent.market_id,
            token_id=intent.token_id,
            side=str(intent.side),
            price=q_price,
            size=q_size,
            client_order_id=client_order_id,
            ttl_ms=ttl_ms,
        )
        self.rate_limiter.record_response(int(result.get("status_code", 500)))
        if not result.get("ok"):
            managed.status = OrderStatus.REJECTED
            managed.last_update_ts = time.time()
            return OrderDecision(False, result.get("error", "place_failed"), client_order_id=client_order_id)
        venue_order_id = result.get("order_id")
        if venue_order_id:
            managed.venue_order_id = str(venue_order_id)
            self.client_by_venue_id[str(venue_order_id)] = client_order_id
        return OrderDecision(True, "sent", client_order_id=client_order_id)

    async def _handle_cancel(self, intent: Intent, risk_breach: bool) -> OrderDecision:
        if not intent.order_id:
            return OrderDecision(False, "missing_order_id")

        order = self._get_order(intent.order_id)
        if order is None:
            return OrderDecision(False, "order_not_found")
        if order.status in {OrderStatus.CANCELED, OrderStatus.CLOSED, OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
            return OrderDecision(False, "already_terminal")

        now = time.time()
        if not risk_breach:
            lifetime_ms = (now - order.created_ts) * 1000
            if lifetime_ms < self.cfg.min_order_lifetime_ms:
                return OrderDecision(False, "min_lifetime_not_met", client_order_id=order.client_order_id)

        if not self._allow_cancel(order.market_id):
            return OrderDecision(False, "cancel_churn_limited", client_order_id=order.client_order_id)

        order.status = OrderStatus.CANCEL_SENT
        order.last_update_ts = now
        venue_id = order.venue_order_id or order.client_order_id

        await self.rate_limiter.acquire_delete()
        result = await self.execution.cancel_order(venue_id)
        self.rate_limiter.record_response(int(result.get("status_code", 500)))
        if not result.get("ok"):
            return OrderDecision(False, result.get("error", "cancel_failed"), client_order_id=order.client_order_id)
        return OrderDecision(True, "cancel_sent", client_order_id=order.client_order_id)

    def on_ack(self, client_order_id: str, venue_order_id: str | None = None) -> None:
        order = self.orders_by_client_id.get(client_order_id)
        if order is None:
            return
        order.status = OrderStatus.ACKED
        order.ack_ts = time.time()
        order.last_update_ts = time.time()
        if venue_order_id:
            order.venue_order_id = venue_order_id
            self.client_by_venue_id[venue_order_id] = client_order_id

    def on_fill(self, client_order_id: str, fill_size: float) -> None:
        order = self.orders_by_client_id.get(client_order_id)
        if order is None:
            return
        if order.first_fill_ts is None:
            order.first_fill_ts = time.time()
        order.remaining_size = max(0.0, order.remaining_size - fill_size)
        order.last_update_ts = time.time()
        if order.remaining_size <= 0:
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.PARTIAL

    def on_close(self, client_order_id: str) -> None:
        order = self.orders_by_client_id.get(client_order_id)
        if order is None:
            return
        order.status = OrderStatus.CLOSED
        order.last_update_ts = time.time()

    def on_cancel(self, client_order_id: str) -> None:
        order = self.orders_by_client_id.get(client_order_id)
        if order is None:
            return
        order.status = OrderStatus.CANCELED
        order.last_update_ts = time.time()

    def on_reject(self, client_order_id: str) -> None:
        order = self.orders_by_client_id.get(client_order_id)
        if order is None:
            return
        order.status = OrderStatus.REJECTED
        order.last_update_ts = time.time()

    async def auto_cancel_expired(self, risk_breach: bool = False) -> list[str]:
        now = time.time()
        canceled: list[str] = []
        for order in list(self.orders_by_client_id.values()):
            if order.status not in {OrderStatus.SENT, OrderStatus.ACKED, OrderStatus.PARTIAL}:
                continue
            if (now - order.created_ts) * 1000 >= order.ttl_ms:
                decision = await self._handle_cancel(
                    Intent(
                        intent_type=IntentType.CANCEL,
                        market_id=order.market_id,
                        token_id=order.token_id,
                        order_id=order.client_order_id,
                    ),
                    risk_breach=risk_breach,
                )
                if decision.accepted and decision.client_order_id:
                    canceled.append(decision.client_order_id)
                    order.status = OrderStatus.EXPIRED
        return canceled

    def live_open_orders_count(self, market_id: str | None = None) -> int:
        statuses = {OrderStatus.SENT, OrderStatus.ACKED, OrderStatus.PARTIAL, OrderStatus.CANCEL_SENT}
        if market_id is None:
            return sum(1 for o in self.orders_by_client_id.values() if o.status in statuses)
        return sum(1 for o in self.orders_by_client_id.values() if o.market_id == market_id and o.status in statuses)

    def _allow_cancel(self, market_id: str) -> bool:
        now = time.time()
        dq = self.cancel_windows[market_id]
        while dq and (now - dq[0]) > 1.0:
            dq.popleft()
        if len(dq) >= self.cfg.max_cancels_per_sec_per_market:
            return False
        dq.append(now)
        return True

    def _get_order(self, order_ref: str) -> ManagedOrder | None:
        if order_ref in self.orders_by_client_id:
            return self.orders_by_client_id[order_ref]
        client = self.client_by_venue_id.get(order_ref)
        if client:
            return self.orders_by_client_id.get(client)
        return None

    def _is_live(self, client_order_id: str) -> bool:
        order = self.orders_by_client_id.get(client_order_id)
        if order is None:
            return False
        return order.status in {OrderStatus.SENT, OrderStatus.ACKED, OrderStatus.PARTIAL, OrderStatus.CANCEL_SENT}

    def _find_live_conflict(self, market_id: str, token_id: str, side: str, price: float, size: float) -> ManagedOrder | None:
        for order in self.orders_by_client_id.values():
            if order.market_id != market_id:
                continue
            if order.token_id != token_id:
                continue
            if order.side != side:
                continue
            if order.status not in {OrderStatus.SENT, OrderStatus.ACKED, OrderStatus.PARTIAL, OrderStatus.CANCEL_SENT}:
                continue
            if abs(order.price - price) < 1e-12 and abs(order.size - size) < 1e-12:
                continue
            return order
        return None

    def _prune_intent_seen(self) -> None:
        now = time.time()
        stale = [k for k, ts in self._intent_seen_ts.items() if (now - ts) > self._intent_seen_ttl_sec]
        for k in stale:
            self._intent_seen_ts.pop(k, None)
            self._intent_seen.discard(k)
