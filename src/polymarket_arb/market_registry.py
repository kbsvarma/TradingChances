from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp


@dataclass(slots=True)
class MarketMeta:
    market_id: str
    yes_token_id: str
    no_token_id: str
    tick_size: float
    min_order_size: float
    fee_rate: float


class MarketRegistry:
    def __init__(self, markets: dict[str, MarketMeta]) -> None:
        self._markets = markets
        self._token_to_market: dict[str, str] = {}
        for market_id, meta in markets.items():
            self._token_to_market[meta.yes_token_id] = market_id
            self._token_to_market[meta.no_token_id] = market_id

    @classmethod
    def from_config(cls, cfg: dict[str, Any], market_ids: list[str]) -> "MarketRegistry":
        raw = cfg.get("market_metadata", {})
        markets: dict[str, MarketMeta] = {}
        for market_id in market_ids:
            details = raw.get(market_id)
            if not details:
                continue
            yes = str(details.get("yes_token_id", ""))
            no = str(details.get("no_token_id", ""))
            if not yes or not no:
                continue
            markets[market_id] = MarketMeta(
                market_id=market_id,
                yes_token_id=yes,
                no_token_id=no,
                tick_size=float(details.get("tick_size", 0.001)),
                min_order_size=float(details.get("min_order_size", 1.0)),
                fee_rate=float(details.get("fee_rate", 0.002)),
            )
        return cls(markets)

    async def refresh_from_gamma(self, gamma_url: str, market_ids: list[str], timeout_sec: int = 8) -> None:
        if not market_ids:
            return
        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for market_id in market_ids:
                try:
                    meta = await self._fetch_market_meta(session, gamma_url, market_id)
                    if meta:
                        self._markets[market_id] = meta
                        self._token_to_market[meta.yes_token_id] = market_id
                        self._token_to_market[meta.no_token_id] = market_id
                except Exception:
                    logging.getLogger("MarketRegistry").exception("gamma refresh failed", extra={"market_id": market_id})

    async def _fetch_market_meta(self, session: aiohttp.ClientSession, gamma_url: str, market_id: str) -> MarketMeta | None:
        endpoints = [
            f"{gamma_url.rstrip('/')}/markets/{market_id}",
            f"{gamma_url.rstrip('/')}/markets?id={market_id}",
        ]
        data: dict[str, Any] | None = None
        for url in endpoints:
            async with session.get(url) as resp:
                if resp.status != 200:
                    continue
                payload = await resp.json()
                if isinstance(payload, list):
                    if not payload:
                        continue
                    data = payload[0]
                elif isinstance(payload, dict):
                    data = payload
                if data:
                    break
        if not data:
            return None

        token_ids = data.get("clobTokenIds") or data.get("clob_token_ids") or data.get("tokenIds")
        if isinstance(token_ids, str):
            parts = [x.strip() for x in token_ids.split(",") if x.strip()]
        elif isinstance(token_ids, list):
            parts = [str(x) for x in token_ids]
        else:
            parts = []
        if len(parts) < 2:
            return None

        outcomes = data.get("outcomes")
        yes_idx, no_idx = 0, 1
        if isinstance(outcomes, list) and len(outcomes) >= 2:
            out = [str(x).lower() for x in outcomes]
            if "yes" in out and "no" in out:
                yes_idx = out.index("yes")
                no_idx = out.index("no")

        yes = parts[yes_idx]
        no = parts[no_idx]
        return MarketMeta(
            market_id=market_id,
            yes_token_id=yes,
            no_token_id=no,
            tick_size=float(data.get("tickSize") or data.get("tick_size") or 0.001),
            min_order_size=float(data.get("minOrderSize") or data.get("min_order_size") or 1.0),
            fee_rate=float(data.get("feeRate") or data.get("fee_rate") or 0.002),
        )

    def get(self, market_id: str) -> MarketMeta | None:
        return self._markets.get(market_id)

    def get_market_id_by_token(self, token_id: str) -> str | None:
        return self._token_to_market.get(token_id)

    def enabled_market_ids(self) -> set[str]:
        return set(self._markets)

    def disable(self, market_id: str) -> None:
        meta = self._markets.pop(market_id, None)
        if meta is None:
            return
        self._token_to_market.pop(meta.yes_token_id, None)
        self._token_to_market.pop(meta.no_token_id, None)
