import time

from polymarket_arb.config import RiskConfig
from polymarket_arb.risk import RiskManager
from polymarket_arb.types import FillRecord


def mk_cfg() -> RiskConfig:
    return RiskConfig(
        max_position_per_market=100.0,
        max_total_exposure=1000.0,
        max_hourly_loss=0.1,
        max_daily_loss=0.15,
        max_open_orders_per_market=8,
        p95_latency_ms_limit=9999,
        reject_rate_limit=1.0,
        drawdown_limit=9999.0,
        ws_health_timeout_sec=30,
        picked_off_spike_count=99,
        picked_off_window_sec=60,
        picked_off_freshness_ms=250,
    )


def test_hourly_breaker_trips_on_long_reduce_loss():
    rm = RiskManager(mk_cfg())
    rm.on_ws_health(time.time())
    ts = time.time()
    rm.on_fill(FillRecord("m1", "t1", "buy", 0.7, 1.0, ts, fee=0.0))
    rm.on_fill(FillRecord("m1", "t1", "sell", 0.55, 1.0, ts + 1, fee=0.01))

    snap = rm.snapshot()
    assert snap.hourly_pnl < -0.1
    assert snap.hourly_pnl == rm.realized_pnl
    tripped, reason = rm.evaluate_circuit_breakers()
    assert tripped
    assert reason == "hourly_loss"


def test_daily_breaker_trips_on_short_cover_loss():
    rm = RiskManager(mk_cfg())
    rm.cfg.max_hourly_loss = 999.0
    rm.on_ws_health(time.time())
    ts = time.time()
    rm.on_fill(FillRecord("m1", "t1", "sell", 0.3, 1.0, ts, fee=0.0))
    rm.on_fill(FillRecord("m1", "t1", "buy", 0.55, 1.0, ts + 1, fee=0.01))

    snap = rm.snapshot()
    assert snap.daily_pnl < -0.15
    tripped, reason = rm.evaluate_circuit_breakers()
    assert tripped
    assert reason == "daily_loss"


def test_flip_long_to_short_loss_is_counted_for_breaker():
    rm = RiskManager(mk_cfg())
    rm.on_ws_health(time.time())
    ts = time.time()
    rm.on_fill(FillRecord("m1", "t1", "buy", 0.6, 1.0, ts, fee=0.0))
    rm.on_fill(FillRecord("m1", "t1", "sell", 0.4, 2.0, ts + 1, fee=0.02))

    snap = rm.snapshot()
    assert snap.hourly_pnl < -0.1
    assert len(rm.pnl_hour_events) == 1
    tripped, reason = rm.evaluate_circuit_breakers()
    assert tripped
    assert reason == "hourly_loss"
