import asyncio

from polymarket_arb.execution import ExecutionAdapter


class DummyClient:
    def create_order(self, **kwargs):
        return {"id": "oid"}

    def cancel(self, order_id):
        return {"ok": True, "id": order_id}


def test_execution_uses_to_thread(monkeypatch):
    called = {"to_thread": 0}

    async def fake_to_thread(fn, *args, **kwargs):
        called["to_thread"] += 1
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    adapter = ExecutionAdapter(dry_run=False, env={
        "CLOB_REST_URL": "http://x",
        "CHAIN_ID": "137",
        "PRIVATE_KEY": "0x0",
        "SIGNATURE_TYPE": "EOA",
        "CLOB_API_KEY": "",
        "CLOB_API_SECRET": "",
        "CLOB_API_PASSPHRASE": "",
    })
    adapter._clob = DummyClient()
    adapter.dry_run = False

    async def run():
        await adapter.place_order("m", "t", "buy", 0.5, 1.0, "c1", 1000)
        await adapter.cancel_order("o1")

    asyncio.run(run())
    assert called["to_thread"] >= 2
