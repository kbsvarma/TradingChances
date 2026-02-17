from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict

from polymarket_arb.book import BookStore
from polymarket_arb.config import BotConfig
from polymarket_arb.metrics import Metrics
from polymarket_arb.normalizer import Normalizer
from polymarket_arb.order_manager import OrderManager
from polymarket_arb.persistence import Persistence
from polymarket_arb.rate_limiter import RateLimiter
from polymarket_arb.risk import RiskManager
from polymarket_arb.strategy import FeeProvider, SlippageModel, Strategy, StrategyParams
from polymarket_arb.types import EngineState, EventType, IntentType, NormalizedEvent


class SimExecutionAdapter:
    def __init__(self) -> None:
        self.now = time.time

    async def place_order(self, **kwargs):
        return {
            "ok": True,
            "status_code": 200,
            "order_id": f"sim-{kwargs['client_order_id']}",
            "client_order_id": kwargs["client_order_id"],
            "sent_ts": self.now(),
        }

    async def cancel_order(self, order_id: str):
        return {"ok": True, "status_code": 200, "order_id": order_id, "sent_ts": self.now()}


class Backtester:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self.log = logging.getLogger("Backtester")
        self.book_store = BookStore()
        self.normalizer = Normalizer()
        self.persistence = Persistence(cfg.persistence.db_path, cfg.persistence.flush_interval_sec)
        self.metrics = Metrics()
        self.risk = RiskManager(cfg.risk)
        self.rate_limiter = RateLimiter(cfg.raw["rate_limits"])
        self.order_manager = OrderManager(cfg.order, SimExecutionAdapter(), self.rate_limiter, self.normalizer)
        self.strategy = Strategy(
            params=StrategyParams(
                min_edge_threshold=cfg.thresholds.min_edge_threshold,
                failure_buffer=cfg.thresholds.failure_buffer,
                default_fee_rate=cfg.thresholds.default_fee_rate,
                max_slippage_bps=cfg.thresholds.max_slippage_bps,
                ttl_ms=cfg.order.default_ttl_ms,
            ),
            slippage_model=SlippageModel(),
            fee_provider=FeeProvider(cfg.thresholds.default_fee_rate),
        )
        self.market_tokens: dict[str, set[str]] = {}

    async def run(self) -> dict:
        await self.persistence.init()
        self.risk.state = EngineState.RUNNING
        pnl_curve: list[dict[str, float]] = []
        events = await self.persistence.load_events_for_replay()
        replay_speed = float(self.cfg.raw.get("backtest", {}).get("replay_speed", 1.0))
        prev_ts: float | None = None
        for e in events:
            cur_ts = float(e["ts"])
            if prev_ts is not None and replay_speed > 0:
                gap = max(0.0, cur_ts - prev_ts)
                # Keep deterministic but optionally pace timeline.
                await asyncio.sleep(min(0.01, gap / replay_speed))
            prev_ts = cur_ts
            ne = NormalizedEvent(
                event_type=EventType(e["event_type"]),
                market_id=e["market_id"],
                token_id=e["token_id"],
                payload=e["payload"],
                recv_ts=float(e["ts"]),
                correlation_id=e.get("correlation_id"),
            )
            if ne.event_type == EventType.ORDER_BOOK_UPDATE:
                p = ne.payload
                bids = p.get("bids", [])
                asks = p.get("asks", [])
                if ne.token_id:
                    self.market_tokens.setdefault(ne.market_id, set()).add(str(ne.token_id))
                    self.book_store.upsert(ne.market_id, str(ne.token_id), bids, asks, ne.recv_ts, ne.exchange_ts)
                await self._run_cycle(ne.market_id)
            elif ne.event_type == EventType.FILL:
                self.metrics.inc("fill")
                client_order_id = ne.payload.get("client_order_id")
                if client_order_id:
                    size = float(ne.payload.get("size", 0.0))
                    self.order_manager.on_fill(str(client_order_id), size)
            elif ne.event_type == EventType.REJECT:
                self.metrics.inc("reject")
            elif ne.event_type == EventType.CANCEL:
                self.metrics.inc("cancel")
            pnl_curve.append({"ts": float(ne.recv_ts), "equity": float(self.risk.equity)})

        report = {
            "metrics": self.metrics.summary(),
            "orders_total": len(self.order_manager.orders_by_client_id),
            "open_orders": self.order_manager.live_open_orders_count(),
            "equity": self.risk.equity,
            "pnl_curve": pnl_curve[-1000:],
            "slippage_stats": {"model": "depth_based_v1"},
            "partial_fills": float(self.metrics.counters.get("partial_fill", 0)),
            "rejects": float(self.metrics.counters.get("reject", 0)),
            "edge_capture_rate": float(self.metrics.ratio("fill", "sent")),
        }
        await self.persistence.stop()
        return report

    async def _run_cycle(self, market_id: str) -> None:
        tokens = sorted(self.market_tokens.get(market_id, set()))
        if len(tokens) < 2:
            return
        yes, no = tokens[0], tokens[1]
        intents = await self.strategy.compute_intents(
            self.book_store.get(market_id, yes),
            self.book_store.get(market_id, no),
            self.risk.positions,
            market_id,
            yes,
            no,
        )
        for intent in intents:
            if intent.intent_type == IntentType.NOOP:
                continue
            ok, _ = self.risk.can_place(intent)
            if not ok:
                continue
            decision = await self.order_manager.process_intent(intent)
            if decision.accepted and decision.client_order_id:
                order = self.order_manager.orders_by_client_id[decision.client_order_id]
                self.order_manager.on_ack(order.client_order_id, order.venue_order_id)
                filled, partial = self._simulate_fill(order.market_id, order.token_id, order.side, order.price, order.remaining_size)
                if filled > 0:
                    self.order_manager.on_fill(order.client_order_id, filled)
                    self.metrics.inc("fill")
                    if partial:
                        self.metrics.inc("partial_fill")
                await self.persistence.upsert_order(
                    {
                        "client_order_id": order.client_order_id,
                        "venue_order_id": order.venue_order_id,
                        "market_id": order.market_id,
                        "token_id": order.token_id,
                        "side": order.side,
                        "price": order.price,
                        "size": order.size,
                        "remaining_size": order.remaining_size,
                        "status": order.status.value,
                        "created_ts": order.created_ts,
                        "last_update_ts": order.last_update_ts,
                        "ttl_ms": order.ttl_ms,
                    }
                )
                self.metrics.inc("sent")
            await self.persistence.record_intent(
                intent.market_id,
                intent.token_id,
                intent.intent_type.value,
                asdict(intent),
            )

    def _simulate_fill(self, market_id: str, token_id: str, side: str, price: float, size: float) -> tuple[float, bool]:
        book = self.book_store.get(market_id, token_id)
        if book is None:
            return 0.0, False
        if side == "buy":
            best = book.best_ask()
            if best is None or price < best:
                return 0.0, False
            top_size = book.asks[0].size if book.asks else 0.0
        else:
            best = book.best_bid()
            if best is None or price > best:
                return 0.0, False
            top_size = book.bids[0].size if book.bids else 0.0
        fill_size = min(size, top_size if top_size > 0 else size)
        return fill_size, fill_size < size
