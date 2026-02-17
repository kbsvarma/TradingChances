import asyncio
import time

from polymarket_arb.backtest import Backtester
from polymarket_arb.book import BookStore
from polymarket_arb.config import load_config
from polymarket_arb.market_registry import MarketMeta, MarketRegistry
from polymarket_arb.types import FillRecord


def test_round_trip_increases_cash_and_equity(tmp_path):
    cfg = load_config("config.yaml")
    cfg.persistence.db_path = str(tmp_path / "bt.db")
    cfg.raw.setdefault("backtest", {})["initial_capital"] = 1000.0
    reg = MarketRegistry({"m1": MarketMeta("m1", "yes", "no", 0.01, 0.1, 0.0)})
    bt = Backtester(cfg, reg)
    bt.book_store = BookStore()
    bt.book_store.upsert("m1", "yes", [{"price": 0.7, "size": 10}], [{"price": 0.8, "size": 10}], time.time(), None, active=True, require_nonempty_if_active=False)

    bt._apply_fill(FillRecord("m1", "yes", "buy", 0.5, 1, time.time(), fee=0.0))
    bt._apply_fill(FillRecord("m1", "yes", "sell", 0.6, 1, time.time(), fee=0.0))
    bt._mark_to_market()

    assert bt.risk.cash > 1000.0
    assert bt.risk.equity >= bt.risk.cash


def test_mark_to_market_changes_unrealized_not_cash(tmp_path):
    cfg = load_config("config.yaml")
    cfg.persistence.db_path = str(tmp_path / "bt2.db")
    cfg.raw.setdefault("backtest", {})["initial_capital"] = 1000.0
    reg = MarketRegistry({"m1": MarketMeta("m1", "yes", "no", 0.01, 0.1, 0.0)})
    bt = Backtester(cfg, reg)
    bt.book_store = BookStore()

    bt._apply_fill(FillRecord("m1", "yes", "buy", 0.5, 1, time.time(), fee=0.0))
    cash_before = bt.risk.cash
    bt.book_store.upsert("m1", "yes", [{"price": 0.7, "size": 10}], [{"price": 0.8, "size": 10}], time.time(), None, active=True, require_nonempty_if_active=False)
    bt._mark_to_market()

    assert bt.risk.cash == cash_before
    assert bt.risk.unrealized_pnl > 0
