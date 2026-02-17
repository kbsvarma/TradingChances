from polymarket_arb.config import load_config
from polymarket_arb.edge_quality import EdgeQualityGuard
from polymarket_arb.engine import TradingEngine
from polymarket_arb.types import FillRecord


def test_edge_quality_guard_disables_on_low_ratio():
    guard = EdgeQualityGuard(window_size=30, min_ratio=0.5, min_trades=15)
    for _ in range(15):
        guard.record("m1", predicted_edge=0.02, realized_edge=0.001)
    ratio = guard.quality_ratio("m1")
    assert ratio is not None
    assert ratio < 0.5
    assert guard.should_disable("m1")


def test_edge_decay_disables_only_affected_market():
    cfg = load_config("config.yaml")
    cfg.markets = ["m1", "m2"]
    cfg.raw["market_metadata"] = {
        "m1": {"yes_token_id": "m1y", "no_token_id": "m1n", "tick_size": 0.001, "min_order_size": 1.0, "fee_rate": 0.002},
        "m2": {"yes_token_id": "m2y", "no_token_id": "m2n", "tick_size": 0.001, "min_order_size": 1.0, "fee_rate": 0.002},
    }
    cfg.safety.edge_decay_min_trades = 3
    cfg.safety.edge_decay_min_ratio = 0.5
    cfg.safety.edge_decay_window_size = 5
    engine = TradingEngine(cfg)
    engine.enabled_markets = {"m1", "m2"}

    for i in range(3):
        cid = f"c{i}"
        engine.predicted_edge_by_client_id[cid] = 0.02
        fill = FillRecord(market_id="m1", token_id="m1y", side="buy", price=0.5, size=1.0, ts=1.0, client_order_id=cid)
        engine._update_edge_quality(fill, realized_pnl_delta=-0.02)

    assert "m1" not in engine.enabled_markets
    assert "m2" in engine.enabled_markets
