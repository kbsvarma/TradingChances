from __future__ import annotations

import time
from typing import Any

import aiohttp


def _parse_levels(raw: Any) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    if not isinstance(raw, list):
        return out
    for level in raw:
        if isinstance(level, dict):
            price = float(level.get("price", 0.0))
            size = float(level.get("size", 0.0))
        elif isinstance(level, list) and len(level) >= 2:
            price = float(level[0])
            size = float(level[1])
        else:
            continue
        out.append({"price": price, "size": size})
    return out


async def fetch_token_orderbook(rest_url: str, token_id: str, session: aiohttp.ClientSession | None = None) -> dict[str, Any]:
    own_session = session is None
    sess = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))
    try:
        async with sess.get(f"{rest_url.rstrip('/')}/book", params={"token_id": token_id}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"snapshot status={resp.status}")
            payload = await resp.json()
    finally:
        if own_session:
            await sess.close()

    bids = _parse_levels(payload.get("bids") or payload.get("buy") or payload.get("bid") or [])
    asks = _parse_levels(payload.get("asks") or payload.get("sell") or payload.get("ask") or [])
    return {
        "token_id": token_id,
        "bids": bids,
        "asks": asks,
        "ts": payload.get("timestamp") or payload.get("ts") or int(time.time() * 1000),
        "market_active": bool(payload.get("market_active", True)),
    }
