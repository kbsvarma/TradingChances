import asyncio

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


def test_strict_mode_rejects_true_false_labels():
    reg = MarketRegistry({})
    payload = {"clobTokenIds": ["yes_token", "no_token"], "outcomes": ["true", "false"]}
    meta = asyncio.run(reg._fetch_market_meta(DummySession(payload), "https://gamma", "m1"))
    assert meta is not None
    assert not meta.is_binary_yes_no


def test_permissive_mode_accepts_true_false_labels():
    reg = MarketRegistry({}, allow_nonstandard_yes_no_labels=True)
    payload = {"clobTokenIds": ["yes_token", "no_token"], "outcomes": ["true", "false"]}
    meta = asyncio.run(reg._fetch_market_meta(DummySession(payload), "https://gamma", "m1"))
    assert meta is not None
    assert meta.is_binary_yes_no
    assert meta.yes_token_id == "yes_token"
    assert meta.no_token_id == "no_token"
