from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from polymarket_arb.config import RiskConfig
from polymarket_arb.types import EngineState, FillRecord, Intent, IntentType, Position


@dataclass(slots=True)
class RiskSnapshot:
    exposure: float
    hourly_pnl: float
    daily_pnl: float
    reject_rate: float
    p95_latency_ms: float
    drawdown: float
    ws_healthy: bool
    picked_off_spike: bool


@dataclass(slots=True)
class RiskManager:
    cfg: RiskConfig
    state: EngineState = EngineState.PAUSED
    positions: dict[str, Position] = field(default_factory=dict)
    open_orders_per_market: dict[str, int] = field(default_factory=dict)
    pnl_hour_events: deque[tuple[float, float]] = field(default_factory=deque)
    pnl_day_events: deque[tuple[float, float]] = field(default_factory=deque)
    rejects: deque[tuple[float, int]] = field(default_factory=deque)
    latencies_ms: deque[float] = field(default_factory=deque)
    picked_off_events: deque[tuple[float, int]] = field(default_factory=deque)
    ws_last_seen_ts: float = 0.0
    peak_equity: float = 0.0
    equity: float = 0.0

    def transition(self, target: EngineState) -> None:
        legal = {
            EngineState.RUNNING: {EngineState.PAUSED, EngineState.SAFE, EngineState.FLATTENING},
            EngineState.PAUSED: {EngineState.RUNNING, EngineState.FLATTENING, EngineState.SAFE},
            EngineState.FLATTENING: {EngineState.SAFE, EngineState.PAUSED},
            EngineState.SAFE: {EngineState.PAUSED},
        }
        if target in legal[self.state]:
            self.state = target

    def on_ws_health(self, ts: float) -> None:
        self.ws_last_seen_ts = ts

    def on_latency(self, latency_ms: float) -> None:
        self.latencies_ms.append(latency_ms)
        while len(self.latencies_ms) > 2000:
            self.latencies_ms.popleft()

    def on_reject(self, ts: float) -> None:
        self.rejects.append((ts, 1))
        self._trim(self.rejects, 60)

    def on_picked_off(self, ts: float) -> None:
        self.picked_off_events.append((ts, 1))
        self._trim(self.picked_off_events, self.cfg.picked_off_window_sec)

    def on_fill(self, fill: FillRecord) -> None:
        key = f"{fill.market_id}:{fill.token_id}"
        pos = self.positions.setdefault(key, Position(fill.market_id, fill.token_id))
        sign = 1 if fill.side == "buy" else -1
        old_qty = pos.qty
        new_qty = old_qty + (sign * fill.size)
        if new_qty != 0:
            pos.avg_price = ((pos.avg_price * old_qty) + (fill.price * sign * fill.size)) / new_qty
        else:
            pos.avg_price = 0.0
        pos.qty = new_qty

    def on_pnl(self, pnl_delta: float, ts: float | None = None) -> None:
        ts = ts or time.time()
        self.equity += pnl_delta
        self.peak_equity = max(self.peak_equity, self.equity)
        self.pnl_hour_events.append((ts, pnl_delta))
        self.pnl_day_events.append((ts, pnl_delta))
        self._trim(self.pnl_hour_events, 3600)
        self._trim(self.pnl_day_events, 86400)

    def set_open_orders(self, market_id: str, value: int) -> None:
        self.open_orders_per_market[market_id] = value

    def can_place(self, intent: Intent) -> tuple[bool, str]:
        if self.state != EngineState.RUNNING:
            return False, f"state={self.state.value}"
        if intent.intent_type != IntentType.PLACE:
            return True, "ok"
        if intent.size is None:
            return False, "missing_size"

        open_count = self.open_orders_per_market.get(intent.market_id, 0)
        if open_count >= self.cfg.max_open_orders_per_market:
            return False, "too_many_open_orders"

        key = f"{intent.market_id}:{intent.token_id}"
        pos = self.positions.get(key, Position(intent.market_id, intent.token_id))
        projected = pos.qty + (intent.size if intent.side == "buy" else -intent.size)
        if abs(projected) > self.cfg.max_position_per_market:
            return False, "max_position_per_market"

        exposure = self._exposure() + abs((intent.price or 0.0) * intent.size)
        if exposure > self.cfg.max_total_exposure:
            return False, "max_total_exposure"

        snap = self.snapshot()
        if abs(snap.hourly_pnl) > self.cfg.max_hourly_loss:
            return False, "max_hourly_loss"
        if abs(snap.daily_pnl) > self.cfg.max_daily_loss:
            return False, "max_daily_loss"
        if snap.reject_rate > self.cfg.reject_rate_limit:
            return False, "reject_rate_limit"
        if snap.p95_latency_ms > self.cfg.p95_latency_ms_limit:
            return False, "latency_limit"
        if snap.drawdown > self.cfg.drawdown_limit:
            return False, "drawdown_limit"
        if snap.picked_off_spike:
            return False, "picked_off_spike"
        if not snap.ws_healthy:
            return False, "ws_unhealthy"
        return True, "ok"

    def evaluate_circuit_breakers(self) -> tuple[bool, str]:
        snap = self.snapshot()
        if snap.p95_latency_ms > self.cfg.p95_latency_ms_limit:
            return True, "p95_latency"
        if snap.reject_rate > self.cfg.reject_rate_limit:
            return True, "reject_rate"
        if snap.drawdown > self.cfg.drawdown_limit:
            return True, "drawdown"
        if snap.picked_off_spike:
            return True, "picked_off_spike"
        if not snap.ws_healthy:
            return True, "ws_health"
        if abs(snap.hourly_pnl) > self.cfg.max_hourly_loss:
            return True, "hourly_loss"
        if abs(snap.daily_pnl) > self.cfg.max_daily_loss:
            return True, "daily_loss"
        return False, "ok"

    def snapshot(self) -> RiskSnapshot:
        now = time.time()
        self._trim(self.pnl_hour_events, 3600)
        self._trim(self.pnl_day_events, 86400)
        self._trim(self.rejects, 60)
        self._trim(self.picked_off_events, self.cfg.picked_off_window_sec)
        p95 = self._p95(self.latencies_ms)
        reject_rate = len(self.rejects) / 60.0
        ws_healthy = (now - self.ws_last_seen_ts) <= self.cfg.ws_health_timeout_sec
        picked_off_spike = len(self.picked_off_events) >= self.cfg.picked_off_spike_count
        return RiskSnapshot(
            exposure=self._exposure(),
            hourly_pnl=sum(x[1] for x in self.pnl_hour_events),
            daily_pnl=sum(x[1] for x in self.pnl_day_events),
            reject_rate=reject_rate,
            p95_latency_ms=p95,
            drawdown=self.peak_equity - self.equity,
            ws_healthy=ws_healthy,
            picked_off_spike=picked_off_spike,
        )

    def _exposure(self) -> float:
        return sum(abs(pos.qty * pos.avg_price) for pos in self.positions.values())

    @staticmethod
    def _trim(buf: deque[tuple[float, float] | tuple[float, int]], window_sec: int) -> None:
        now = time.time()
        while buf and (now - float(buf[0][0])) > window_sec:
            buf.popleft()

    @staticmethod
    def _p95(values: deque[float]) -> float:
        if not values:
            return 0.0
        arr = sorted(values)
        idx = int(0.95 * (len(arr) - 1))
        return float(arr[idx])
