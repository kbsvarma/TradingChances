import time

from polymarket_arb.config import RiskConfig
from polymarket_arb.risk import RiskManager
from polymarket_arb.types import EngineState, Intent, IntentType, Position


def mk_cfg() -> RiskConfig:
    return RiskConfig(
        max_position_per_market=5.0,
        max_total_exposure=100.0,
        max_hourly_loss=10.0,
        max_daily_loss=20.0,
        max_open_orders_per_market=2,
        p95_latency_ms_limit=300,
        reject_rate_limit=0.2,
        drawdown_limit=5.0,
        ws_health_timeout_sec=5,
        picked_off_spike_count=5,
        picked_off_window_sec=60,
    )


def test_blocks_when_not_running():
    rm = RiskManager(mk_cfg())
    rm.state = EngineState.PAUSED
    ok, reason = rm.can_place(Intent(IntentType.PLACE, "m1", "t1", side="buy", price=0.4, size=1.0))
    assert not ok
    assert "state=" in reason


def test_position_limit_enforced():
    rm = RiskManager(mk_cfg())
    rm.state = EngineState.RUNNING
    rm.on_ws_health(time.time())
    ok, _ = rm.can_place(Intent(IntentType.PLACE, "m1", "t1", side="buy", price=0.5, size=3.0))
    assert ok
    rm.positions["m1:t1"] = Position("m1", "t1", qty=4.0, avg_price=0.5)
    ok2, reason2 = rm.can_place(Intent(IntentType.PLACE, "m1", "t1", side="buy", price=0.5, size=2.0))
    assert not ok2
    assert reason2 == "max_position_per_market"


def test_kill_switch_on_latency():
    rm = RiskManager(mk_cfg())
    rm.state = EngineState.RUNNING
    rm.on_ws_health(time.time())
    for _ in range(50):
        rm.on_latency(1000)
    tripped, reason = rm.evaluate_circuit_breakers()
    assert tripped
    assert reason == "p95_latency"
