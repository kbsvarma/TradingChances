from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict

from polymarket_arb.book import BookStore
from polymarket_arb.config import BotConfig
from polymarket_arb.market_registry import MarketRegistry
from polymarket_arb.market_rules import MarketRulesProvider
from polymarket_arb.metrics import Metrics
from polymarket_arb.normalizer import Normalizer
from polymarket_arb.order_manager import OrderManager
from polymarket_arb.persistence import Persistence
from polymarket_arb.rate_limiter import RateLimiter
from polymarket_arb.risk import RiskManager
from polymarket_arb.strategy import SlippageModel, Strategy, StrategyParams
from polymarket_arb.types import EngineState, EventType, FillRecord, IntentType, NormalizedEvent


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
    def __init__(self, cfg: BotConfig, registry: MarketRegistry | None = None) -> None:
        self.cfg = cfg
        self.log = logging.getLogger("Backtester")
        self.book_store = BookStore()
        self.registry = registry or MarketRegistry.from_config(cfg.raw, cfg.markets)
        self.rules_provider = MarketRulesProvider(
            registry=self.registry,
            default_tick_size=0.001,
            default_min_order_size=1.0,
            default_fee_rate=cfg.thresholds.default_fee_rate,
        )
        self.normalizer = Normalizer(self.rules_provider)
        self.persistence = Persistence(
            cfg.persistence.db_path,
            cfg.persistence.flush_interval_sec,
            cfg.persistence.buffer_maxsize,
            cfg.persistence.buffer_high_watermark,
        )
        self.metrics = Metrics()
        self.risk = RiskManager(cfg.risk)
        initial_capital = float(cfg.raw.get("backtest", {}).get("initial_capital", 0.0))
        self.risk.cash = initial_capital
        self.risk.equity = initial_capital
        self.risk.peak_equity = initial_capital

        self.rate_limiter = RateLimiter(cfg.raw["rate_limits"])
        self.order_manager = OrderManager(cfg.order, SimExecutionAdapter(), self.rate_limiter, self.normalizer, self.rules_provider)
        self.strategy = Strategy(
            params=StrategyParams(
                min_edge_threshold=cfg.thresholds.min_edge_threshold,
                failure_buffer=cfg.thresholds.failure_buffer,
                max_slippage_bps=cfg.thresholds.max_slippage_bps,
                ttl_ms=cfg.order.default_ttl_ms,
            ),
            slippage_model=SlippageModel(),
            rules_provider=self.rules_provider,
        )
        self._trades: list[float] = []

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
                    self.book_store.upsert(ne.market_id, str(ne.token_id), bids, asks, ne.recv_ts, ne.exchange_ts, active=True, require_nonempty_if_active=False)
                await self._run_cycle(ne.market_id)
            elif ne.event_type == EventType.FILL:
                self.metrics.inc("fill")
                fill = FillRecord(
                    market_id=ne.market_id,
                    token_id=str(ne.token_id or ""),
                    side=str(ne.payload.get("side", "buy")),
                    price=float(ne.payload.get("price", 0.0)),
                    size=float(ne.payload.get("size", 0.0)),
                    ts=ne.recv_ts,
                    client_order_id=str(ne.payload.get("client_order_id", "")) or None,
                    fee=float(ne.payload.get("fee", 0.0)),
                )
                self._apply_fill(fill)
            elif ne.event_type == EventType.REJECT:
                self.metrics.inc("reject")
            elif ne.event_type == EventType.CANCEL:
                self.metrics.inc("cancel")

            self._mark_to_market()
            pnl_curve.append({"ts": float(ne.recv_ts), "equity": float(self.risk.equity)})

        wins = sum(1 for t in self._trades if t > 0)
        report = {
            "metrics": self.metrics.summary(),
            "orders_total": len(self.order_manager.orders_by_client_id),
            "open_orders": self.order_manager.live_open_orders_count(),
            "final_equity": self.risk.equity,
            "max_drawdown": self.risk.snapshot().drawdown,
            "win_rate": (wins / len(self._trades)) if self._trades else 0.0,
            "trade_count": len(self._trades),
            "realized_pnl": self.risk.realized_pnl,
            "unrealized_pnl": self.risk.unrealized_pnl,
            "cash": self.risk.cash,
            "pnl_curve": pnl_curve[-1000:],
            "partial_fill_frequency": float(self.metrics.counters.get("partial_fill", 0)),
            "reject_reasons": {"reject_count": float(self.metrics.counters.get("reject", 0))},
            "fill_ratio": float(self.metrics.ratio("fill", "sent")),
            "cancel_ratio": float(self.metrics.ratio("cancel", "sent")),
            "edge_predicted_vs_realized": {
                "predicted": float(self.metrics.counters.get("sent", 0)),
                "realized": float(self.metrics.counters.get("fill", 0)),
            },
        }
        await self.persistence.stop(self.cfg.persistence.flush_timeout_sec)
        return report

    async def _run_cycle(self, market_id: str) -> None:
        meta = self.registry.get_binary_market(market_id)
        if meta is None:
            return

        intents = await self.strategy.compute_intents(
            self.book_store.get(market_id, meta.yes_token_id),
            self.book_store.get(market_id, meta.no_token_id),
            self.risk.positions,
            market_id,
            meta.yes_token_id,
            meta.no_token_id,
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
                    fee_rate = await self.rules_provider.get_fee_rate(order.market_id, order.token_id)
                    fill = FillRecord(
                        market_id=order.market_id,
                        token_id=order.token_id,
                        side=order.side,
                        price=order.price,
                        size=filled,
                        ts=time.time(),
                        client_order_id=order.client_order_id,
                        fee=order.price * filled * fee_rate,
                    )
                    self.order_manager.on_fill(order.client_order_id, filled)
                    self._apply_fill(fill)
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

    def _apply_fill(self, fill: FillRecord) -> None:
        prev_realized = self.risk.realized_pnl
        self.risk.on_fill(fill)
        if self.risk.realized_pnl != prev_realized:
            self._trades.append(self.risk.realized_pnl - prev_realized)

    def _mark_to_market(self) -> float:
        price_by_position: dict[str, float] = {}
        for key, pos in self.risk.positions.items():
            book = self.book_store.get(pos.market_id, pos.token_id)
            if book is None:
                continue
            bid = book.best_bid()
            ask = book.best_ask()
            if bid is None or ask is None:
                continue
            price_by_position[key] = (bid + ask) / 2.0
        return self.risk.mark_to_market(price_by_position)
