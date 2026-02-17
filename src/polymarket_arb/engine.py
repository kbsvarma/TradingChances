from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict

from polymarket_arb.backtest import Backtester
from polymarket_arb.book import BookStore
from polymarket_arb.command_bus import CommandBus
from polymarket_arb.config import BotConfig, load_config, required_env
from polymarket_arb.control import CLICommandAPI
from polymarket_arb.execution import ExecutionAdapter
from polymarket_arb.market_registry import MarketRegistry
from polymarket_arb.market_rules import MarketRulesProvider
from polymarket_arb.metrics import Metrics, PickedOffDetector
from polymarket_arb.normalizer import Normalizer
from polymarket_arb.order_manager import OrderManager
from polymarket_arb.persistence import Persistence
from polymarket_arb.rate_limiter import RateLimiter
from polymarket_arb.risk import RiskManager
from polymarket_arb.strategy import SlippageModel, Strategy, StrategyParams
from polymarket_arb.types import Command, CommandType, EngineState, EventType, FillRecord, Intent, IntentType, NormalizedEvent
from polymarket_arb.ws_market import MarketWSClient
from polymarket_arb.ws_user import UserWSClient


class TradingEngine:
    """Single-writer trading engine. All order mutations happen here."""

    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self.env = required_env()
        self.log = logging.getLogger("TradingEngine")

        self.registry = MarketRegistry.from_config(cfg.raw, cfg.markets)
        self.rules_provider = MarketRulesProvider(
            registry=self.registry,
            default_tick_size=0.001,
            default_min_order_size=1.0,
            default_fee_rate=cfg.thresholds.default_fee_rate,
        )

        self.event_q: asyncio.Queue[NormalizedEvent] = asyncio.Queue(maxsize=cfg.runtime.event_queue_maxsize)
        self.book_store = BookStore()
        self.normalizer = Normalizer(self.rules_provider)
        self.command_bus = CommandBus()
        self.persistence = Persistence(
            cfg.persistence.db_path,
            cfg.persistence.flush_interval_sec,
            cfg.persistence.buffer_maxsize,
            cfg.persistence.buffer_high_watermark,
        )

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
        self.risk = RiskManager(cfg.risk)
        self.rate_limiter = RateLimiter(cfg.raw["rate_limits"])
        self.execution = ExecutionAdapter(dry_run=cfg.runtime.dry_run, env=self.env)
        self.order_manager = OrderManager(cfg.order, self.execution, self.rate_limiter, self.normalizer, self.rules_provider)
        self.metrics = Metrics()
        self.picked_off = PickedOffDetector()

        self.market_ws = MarketWSClient(
            ws_url=self.env["CLOB_WS_URL"],
            rest_url=self.env["CLOB_REST_URL"],
            markets=cfg.markets,
            registry=self.registry,
            normalizer=self.normalizer,
            out_queue=self.event_q,
            book_store=self.book_store,
            require_nonempty_active_book=cfg.snapshot.require_nonempty_active_book,
            snapshot_max_level_size=cfg.snapshot.max_level_size,
        )
        self.user_ws = UserWSClient(
            ws_url=self.env["CLOB_WS_URL"],
            auth={
                "api_key": self.env["CLOB_API_KEY"],
                "api_secret": self.env["CLOB_API_SECRET"],
                "api_passphrase": self.env["CLOB_API_PASSPHRASE"],
            },
            normalizer=self.normalizer,
            out_queue=self.event_q,
        )

        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self.enabled_markets = set(cfg.markets)

    async def start(self) -> None:
        await self.persistence.init()
        await self.registry.refresh_from_gamma(self.cfg.gamma_url, self.cfg.markets)
        await self._validate_registry()

        self.command_bus.subscribe(self._on_command)
        self.risk.state = EngineState.PAUSED if self.cfg.runtime.start_paused else EngineState.RUNNING

        self._tasks = [
            asyncio.create_task(self.command_bus.run_forever(), name="command_bus"),
            asyncio.create_task(self.persistence.run_writer(), name="db_writer"),
            asyncio.create_task(self.market_ws.run_forever(), name="market_ws"),
            asyncio.create_task(self.user_ws.run_forever(), name="user_ws"),
            asyncio.create_task(self._event_loop(), name="event_loop"),
            asyncio.create_task(self._ttl_loop(), name="ttl_loop"),
            asyncio.create_task(self._health_loop(), name="health_loop"),
            asyncio.create_task(self._snapshot_loop(), name="snapshot_loop"),
        ]
        if self.cfg.raw["control"].get("enable_cli", True):
            self._tasks.append(asyncio.create_task(CLICommandAPI().run(self.command_bus), name="cli_commands"))

        self.log.info("engine started", extra={"event_type": "engine_start", "correlation_id": "startup"})
        await self._stop.wait()
        await self.shutdown()

    async def shutdown(self) -> None:
        await self.market_ws.stop()
        await self.user_ws.stop()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await self.persistence.stop(self.cfg.persistence.flush_timeout_sec)

    async def _validate_registry(self) -> None:
        for market_id in list(self.enabled_markets):
            meta = self.registry.get(market_id)
            if meta is None:
                self.enabled_markets.discard(market_id)
                self.log.error("market disabled: missing yes/no token mapping", extra={"market_id": market_id})
                await self.persistence.record_error("market_registry", "missing_mapping", "market disabled due to missing mapping", {"market_id": market_id})
                continue
            if not meta.is_binary_yes_no:
                self.enabled_markets.discard(market_id)
                self.log.warning("market disabled: invalid binary yes/no mapping", extra={"market_id": market_id})
                await self.persistence.record_error(
                    "market_registry",
                    "invalid_mapping",
                    meta.validation_error or "invalid market mapping",
                    {"market_id": market_id},
                )

    async def _on_command(self, cmd: Command) -> None:
        if cmd.type == CommandType.PAUSE:
            self.risk.transition(EngineState.PAUSED)
            await self.persistence.flush_with_timeout(self.cfg.persistence.flush_timeout_sec)
            self.log.warning("trading paused")
        elif cmd.type == CommandType.RESUME:
            if self.risk.state != EngineState.SAFE:
                self.risk.transition(EngineState.RUNNING)
                self.log.warning("trading resumed")
        elif cmd.type == CommandType.FLATTEN:
            self.risk.transition(EngineState.FLATTENING)
            await self._flatten_all()
            self.risk.transition(EngineState.SAFE)
        elif cmd.type == CommandType.MARKETS_ON:
            self.enabled_markets.update(set(cmd.payload.get("markets", [])))
            await self._validate_registry()
        elif cmd.type == CommandType.MARKETS_OFF:
            self.enabled_markets -= set(cmd.payload.get("markets", []))
        elif cmd.type == CommandType.RELOAD_CONFIG:
            self.cfg = load_config()
            self.log.info("config reloaded")
        elif cmd.type == CommandType.SET_PARAMS:
            self._apply_params(cmd.payload)
        elif cmd.type == CommandType.BACKTEST:
            report = await Backtester(self.cfg, self.registry).run()
            self.log.info("backtest_report=%s", report, extra={"event_type": "backtest_report", "correlation_id": "command"})
        elif cmd.type == CommandType.STOP:
            self._stop.set()

    def _apply_params(self, payload: dict) -> None:
        if "min_edge_threshold" in payload:
            self.strategy.params.min_edge_threshold = float(payload["min_edge_threshold"])
        if "failure_buffer" in payload:
            self.strategy.params.failure_buffer = float(payload["failure_buffer"])
        if "default_ttl_ms" in payload:
            self.strategy.params.ttl_ms = int(payload["default_ttl_ms"])
        self.log.info("params_updated", extra={"event_type": "params_updated", "correlation_id": "command"})

    async def _event_loop(self) -> None:
        while not self._stop.is_set():
            event = await self.event_q.get()
            recv_ts = time.time()
            await self.persistence.record_event(
                event_type=event.event_type.value,
                market_id=event.market_id,
                token_id=event.token_id,
                payload=event.payload,
                correlation_id=event.correlation_id,
            )

            if event.market_id and event.market_id not in self.enabled_markets:
                continue

            if event.event_type == EventType.WS_HEALTH:
                self.risk.on_ws_health(recv_ts)
                continue

            if event.event_type == EventType.ORDER_ACK:
                client_order_id = str(event.payload.get("client_order_id", ""))
                venue_order_id = event.payload.get("order_id")
                if client_order_id:
                    order = self.order_manager.orders_by_client_id.get(client_order_id)
                    if order is not None:
                        self.metrics.observe_latency("send_to_ack", (recv_ts - order.created_ts) * 1000)
                    self.order_manager.on_ack(client_order_id, str(venue_order_id) if venue_order_id else None)
                continue

            if event.event_type == EventType.REJECT:
                client_order_id = str(event.payload.get("client_order_id", ""))
                if client_order_id:
                    self.order_manager.on_reject(client_order_id)
                self.risk.on_reject(recv_ts)
                self.metrics.inc("reject")
                continue

            if event.event_type == EventType.CANCEL:
                client_order_id = str(event.payload.get("client_order_id", ""))
                if client_order_id:
                    self.order_manager.on_cancel(client_order_id)
                self.metrics.inc("cancel")
                continue

            if event.event_type == EventType.FILL:
                fill = FillRecord(
                    market_id=event.market_id,
                    token_id=str(event.token_id or ""),
                    side=str(event.payload.get("side", "buy")),
                    price=float(event.payload.get("price", 0.0)),
                    size=float(event.payload.get("size", 0.0)),
                    ts=recv_ts,
                    order_id=event.payload.get("order_id"),
                    client_order_id=event.payload.get("client_order_id"),
                )
                if fill.client_order_id:
                    self.order_manager.on_fill(fill.client_order_id, fill.size)
                    order = self.order_manager.orders_by_client_id.get(fill.client_order_id)
                    if order and order.ack_ts is not None and order.first_fill_ts is not None:
                        self.metrics.observe_latency("ack_to_fill", (order.first_fill_ts - order.ack_ts) * 1000)
                self.risk.on_fill(fill)
                self.metrics.inc("fill")
                self._handle_picked_off(fill)
                await self.persistence.record_fill(asdict(fill))
                continue

            if event.event_type == EventType.ORDER_BOOK_UPDATE:
                await self._run_decision_cycle(event.market_id, recv_ts)

    async def _run_decision_cycle(self, market_id: str, recv_ts: float) -> None:
        if self.risk.state == EngineState.FLATTENING:
            return

        meta = self.registry.get_binary_market(market_id)
        if meta is None:
            self.enabled_markets.discard(market_id)
            self.log.error("market disabled: registry missing", extra={"market_id": market_id})
            await self.persistence.record_error("market_registry", "invalid_mapping", "market disabled in decision cycle", {"market_id": market_id})
            return

        book_yes = self.book_store.get(market_id, meta.yes_token_id)
        book_no = self.book_store.get(market_id, meta.no_token_id)

        intents = await self.strategy.compute_intents(
            book_yes=book_yes,
            book_no=book_no,
            positions=self.risk.positions,
            market_id=market_id,
            token_yes=meta.yes_token_id,
            token_no=meta.no_token_id,
        )
        decision_ts = time.time()
        self.metrics.observe_latency("ws_recv_to_decision", (decision_ts - recv_ts) * 1000)
        self.risk.on_latency((decision_ts - recv_ts) * 1000)

        for intent in intents:
            await self.persistence.record_intent(
                market_id=intent.market_id,
                token_id=intent.token_id,
                intent_type=intent.intent_type.value,
                payload={k: v for k, v in asdict(intent).items() if v is not None},
            )
            if intent.intent_type == IntentType.NOOP:
                continue

            can_place, _ = self.risk.can_place(intent)
            if not can_place and intent.intent_type == IntentType.PLACE:
                self.metrics.inc("risk_block")
                continue

            send_ts = time.time()
            decision = await self.order_manager.process_intent(intent)
            post_send_ts = time.time()
            self.metrics.observe_latency("decision_to_send", (post_send_ts - send_ts) * 1000)
            if decision.accepted:
                self.metrics.inc("sent")
            else:
                self.metrics.inc("dropped")

            if decision.client_order_id:
                order = self.order_manager.orders_by_client_id.get(decision.client_order_id)
                if order:
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

        self.risk.set_open_orders(market_id, self.order_manager.live_open_orders_count(market_id))

    async def _health_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(1)
            if self.event_q.qsize() > self.cfg.runtime.event_queue_high_watermark:
                self.log.error("event queue high watermark, pausing and resyncing")
                self.risk.transition(EngineState.PAUSED)
                await self._shed_load()
                await self.market_ws.force_resync_all()

            if self.persistence.queue.qsize() > self.cfg.persistence.buffer_high_watermark:
                self.log.error("persistence queue high watermark, pausing and flushing")
                self.risk.transition(EngineState.PAUSED)
                await self.persistence.flush_with_timeout(self.cfg.persistence.flush_timeout_sec)

            should_trip, reason = self.risk.evaluate_circuit_breakers()
            if should_trip and self.risk.state == EngineState.RUNNING:
                self.log.error("kill switch triggered", extra={"event_type": "kill_switch", "correlation_id": reason})
                self.risk.transition(EngineState.FLATTENING)
                await self._flatten_all()
                self.risk.transition(EngineState.SAFE)

    async def _shed_load(self) -> None:
        keep: list[NormalizedEvent] = []
        target = self.cfg.runtime.event_queue_high_watermark // 2
        while self.event_q.qsize() > target:
            try:
                ev = self.event_q.get_nowait()
            except asyncio.QueueEmpty:
                break
            if ev.event_type in {EventType.FILL, EventType.ORDER_ACK, EventType.CANCEL, EventType.REJECT}:
                keep.append(ev)
        for ev in keep:
            await self.event_q.put(ev)

    async def _ttl_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(0.25)
            canceled = await self.order_manager.auto_cancel_expired(risk_breach=False)
            if canceled:
                self.metrics.inc("ttl_cancel", len(canceled))

    async def _cancel_all(self, risk_breach: bool) -> None:
        for order in list(self.order_manager.orders_by_client_id.values()):
            if order.status.value in {"SENT", "ACKED", "PARTIAL"}:
                await self.order_manager.process_intent(
                    Intent(
                        intent_type=IntentType.CANCEL,
                        market_id=order.market_id,
                        token_id=order.token_id,
                        order_id=order.client_order_id,
                    ),
                    risk_breach=risk_breach,
                )

    async def _unwind_positions(self) -> None:
        for pos_key, pos in list(self.risk.positions.items()):
            if abs(pos.qty) <= 1e-9:
                continue
            book = self.book_store.get(pos.market_id, pos.token_id)
            if not book:
                continue
            if pos.qty > 0:
                px = book.best_bid()
                side = "sell"
                slip_ref = book.best_ask() or px
            else:
                px = book.best_ask()
                side = "buy"
                slip_ref = book.best_bid() or px
            if px is None or slip_ref is None or slip_ref <= 0:
                continue
            slippage_bps = abs(px - slip_ref) / slip_ref * 10000
            if slippage_bps > self.cfg.thresholds.max_slippage_bps:
                continue
            await self.order_manager.process_intent(
                Intent(
                    intent_type=IntentType.PLACE,
                    market_id=pos.market_id,
                    token_id=pos.token_id,
                    side=side,
                    price=px,
                    size=abs(pos.qty),
                    ttl_ms=self.cfg.order.default_ttl_ms,
                    maker_or_taker="taker",
                    reason=f"flatten:{pos_key}",
                ),
                risk_breach=True,
            )

    async def _flatten_all(self) -> None:
        await self._cancel_all(risk_breach=True)
        mode = self.cfg.trading_safety.flatten_mode
        if mode == "cancel_and_unwind":
            await self._unwind_positions()

    async def _snapshot_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(5)
            snap = self.risk.snapshot()
            await self.persistence.record_pnl_snapshot(
                equity=self.risk.equity,
                drawdown=snap.drawdown,
                daily_pnl=snap.daily_pnl,
                hourly_pnl=snap.hourly_pnl,
            )
            summary = self.metrics.summary()
            for key in ("ws_recv_to_decision", "decision_to_send", "send_to_ack", "ack_to_fill"):
                p50 = float(summary.get(f"{key}_p50", 0.0))
                p95 = float(summary.get(f"{key}_p95", 0.0))
                p99 = float(summary.get(f"{key}_p99", 0.0))
                mean = float(summary.get(f"{key}_mean", 0.0))
                await self.persistence.record_latency_metric(key, p50, p95, p99, mean)

            for key, pos in self.risk.positions.items():
                await self.persistence.upsert_position(
                    key=key,
                    market_id=pos.market_id,
                    token_id=pos.token_id,
                    qty=pos.qty,
                    avg_price=pos.avg_price,
                )

            for (market_id, token_id), book in list(self.book_store.books.items())[:50]:
                bids = [{"price": x.price, "size": x.size} for x in book.bids[:5]]
                asks = [{"price": x.price, "size": x.size} for x in book.asks[:5]]
                await self.persistence.record_book_snapshot(market_id, token_id, bids, asks)

    def _handle_picked_off(self, fill: FillRecord) -> None:
        snap = self.book_store.closest_snapshot(
            fill.market_id,
            fill.token_id,
            fill.ts,
            max_age_ms=self.cfg.risk.picked_off_freshness_ms,
        )
        if snap is None:
            return
        post_fill_best = snap.best_bid() if fill.side == "buy" else snap.best_ask()
        if post_fill_best is None:
            return
        if not self.picked_off.is_picked_off(fill.price, post_fill_best, fill.side):
            return
        self.risk.on_picked_off(fill.ts)
