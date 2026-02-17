import asyncio
import time

from polymarket_arb.backtest import Backtester
from polymarket_arb.book import BookStore
from polymarket_arb.config import load_config
from polymarket_arb.market_registry import MarketMeta, MarketRegistry
from polymarket_arb.types import FillRecord, Intent, IntentType


class CaptureStrategy:
    def __init__(self):
        self.calls = []

    async def compute_intents(self, book_yes, book_no, positions, market_id, token_yes, token_no):
        self.calls.append((market_id, token_yes, token_no))
        return [Intent(IntentType.NOOP, market_id, token_yes)]


def test_backtester_uses_explicit_registry_mapping(tmp_path):
    cfg = load_config("config.yaml")
    cfg.persistence.db_path = str(tmp_path / "bt.db")
    registry = MarketRegistry(
        {
            "m1": MarketMeta("m1", "z_yes", "a_no", tick_size=0.01, min_order_size=0.1, fee_rate=0.002),
        }
    )
    bt = Backtester(cfg, registry)
    bt.strategy = CaptureStrategy()
    bt.book_store = BookStore()
    bt.book_store.upsert("m1", "z_yes", [{"price": 0.4, "size": 10}], [{"price": 0.41, "size": 10}], time.time(), None, active=True, require_nonempty_if_active=False)
    bt.book_store.upsert("m1", "a_no", [{"price": 0.5, "size": 10}], [{"price": 0.51, "size": 10}], time.time(), None, active=True, require_nonempty_if_active=False)

    asyncio.run(bt._run_cycle("m1"))
    assert bt.strategy.calls[0][1] == "z_yes"
    assert bt.strategy.calls[0][2] == "a_no"


def test_positions_and_pnl_change_after_fill(tmp_path):
    cfg = load_config("config.yaml")
    cfg.persistence.db_path = str(tmp_path / "bt2.db")
    registry = MarketRegistry(
        {
            "m1": MarketMeta("m1", "z_yes", "a_no", tick_size=0.01, min_order_size=0.1, fee_rate=0.002),
        }
    )
    bt = Backtester(cfg, registry)
    bt.book_store.upsert("m1", "z_yes", [{"price": 0.6, "size": 10}], [{"price": 0.62, "size": 10}], time.time(), None, active=True, require_nonempty_if_active=False)
    bt._apply_fill(FillRecord("m1", "z_yes", "buy", 0.5, 1.0, time.time(), fee=0.0))
    bt._mark_to_market()
    assert bt.risk.positions["m1:z_yes"].qty == 1.0
    assert bt.risk.equity > 0.0
