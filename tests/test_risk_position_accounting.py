import time

from polymarket_arb.config import RiskConfig
from polymarket_arb.risk import RiskManager
from polymarket_arb.types import FillRecord


def mk_cfg() -> RiskConfig:
    return RiskConfig(
        max_position_per_market=100.0,
        max_total_exposure=10000.0,
        max_hourly_loss=1000.0,
        max_daily_loss=2000.0,
        max_open_orders_per_market=10,
        p95_latency_ms_limit=1000,
        reject_rate_limit=1.0,
        drawdown_limit=10000.0,
        ws_health_timeout_sec=100,
        picked_off_spike_count=100,
        picked_off_window_sec=60,
        picked_off_freshness_ms=250,
    )


def f(side, price, size):
    return FillRecord("m", "t", side, price, size, time.time())


def test_open_and_add_long_avg_updates():
    r = RiskManager(mk_cfg())
    r.on_fill(f("buy", 0.4, 2))
    r.on_fill(f("buy", 0.6, 2))
    p = r.positions["m:t"]
    assert p.qty == 4
    assert abs(p.avg_price - 0.5) < 1e-9


def test_reduce_long_avg_stays_and_realized_changes():
    r = RiskManager(mk_cfg())
    r.on_fill(f("buy", 0.5, 4))
    before = r.realized_pnl
    r.on_fill(f("sell", 0.7, 1))
    p = r.positions["m:t"]
    assert p.qty == 3
    assert abs(p.avg_price - 0.5) < 1e-9
    assert r.realized_pnl > before


def test_flip_long_to_short_resets_avg():
    r = RiskManager(mk_cfg())
    r.on_fill(f("buy", 0.5, 2))
    r.on_fill(f("sell", 0.4, 5))
    p = r.positions["m:t"]
    assert p.qty == -3
    assert abs(p.avg_price - 0.4) < 1e-9


def test_open_and_add_short_avg_updates():
    r = RiskManager(mk_cfg())
    r.on_fill(f("sell", 0.6, 2))
    r.on_fill(f("sell", 0.4, 2))
    p = r.positions["m:t"]
    assert p.qty == -4
    assert abs(p.avg_price - 0.5) < 1e-9


def test_cover_short_avg_stays_and_realized_changes():
    r = RiskManager(mk_cfg())
    r.on_fill(f("sell", 0.5, 4))
    before = r.realized_pnl
    r.on_fill(f("buy", 0.3, 1))
    p = r.positions["m:t"]
    assert p.qty == -3
    assert abs(p.avg_price - 0.5) < 1e-9
    assert r.realized_pnl > before


def test_flip_short_to_long_resets_avg():
    r = RiskManager(mk_cfg())
    r.on_fill(f("sell", 0.5, 2))
    r.on_fill(f("buy", 0.6, 5))
    p = r.positions["m:t"]
    assert p.qty == 3
    assert abs(p.avg_price - 0.6) < 1e-9
