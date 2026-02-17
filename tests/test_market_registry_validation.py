import asyncio

from polymarket_arb.config import load_config
from polymarket_arb.engine import TradingEngine
from polymarket_arb.market_registry import MarketRegistry


class DummyResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class DummySession:
    def __init__(self, payload):
        self.payload = payload

    def get(self, url):
        return DummyResponse(200, self.payload)


def test_good_binary_yes_no_mapping():
    payload = {
        "clobTokenIds": ["tid_yes", "tid_no"],
        "outcomes": ["Yes", "No"],
    }
    reg = MarketRegistry({})
    meta = asyncio.run(reg._fetch_market_meta(DummySession(payload), "https://gamma", "m1"))
    assert meta is not None
    assert meta.is_binary_yes_no
    assert meta.yes_token_id == "tid_yes"
    assert meta.no_token_id == "tid_no"


def test_invalid_three_outcomes_disabled():
    payload = {
        "clobTokenIds": ["a", "b", "c"],
        "outcomes": ["Yes", "No", "Maybe"],
    }
    reg = MarketRegistry({})
    meta = asyncio.run(reg._fetch_market_meta(DummySession(payload), "https://gamma", "m1"))
    assert meta is not None
    assert not meta.is_binary_yes_no
    assert "expected 2 token ids" in (meta.validation_error or "")


def test_invalid_missing_yes_no_label():
    payload = {
        "clobTokenIds": ["a", "b"],
        "outcomes": ["Up", "Down"],
    }
    reg = MarketRegistry({})
    meta = asyncio.run(reg._fetch_market_meta(DummySession(payload), "https://gamma", "m1"))
    assert meta is not None
    assert not meta.is_binary_yes_no
    assert "ambiguous" in (meta.validation_error or "")


def test_invalid_token_count_mismatch():
    payload = {
        "clobTokenIds": ["a", "b"],
        "outcomes": ["Yes"],
    }
    reg = MarketRegistry({})
    meta = asyncio.run(reg._fetch_market_meta(DummySession(payload), "https://gamma", "m1"))
    assert meta is not None
    assert not meta.is_binary_yes_no
    assert "expected 2 outcomes" in (meta.validation_error or "")


def test_engine_skips_disabled_market():
    cfg = load_config("config.yaml")
    cfg.markets = ["bad_market"]
    cfg.raw["market_metadata"] = {
        "bad_market": {
            "yes_token_id": "",
            "no_token_id": "",
        }
    }
    engine = TradingEngine(cfg)
    asyncio.run(engine._validate_registry())
    assert "bad_market" not in engine.enabled_markets
