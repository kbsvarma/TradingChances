from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

STRICT_YES_LABELS = {"yes"}
STRICT_NO_LABELS = {"no"}
PERMISSIVE_YES_LABELS = {"yes", "y", "true"}
PERMISSIVE_NO_LABELS = {"no", "n", "false"}


@dataclass(slots=True)
class MarketMeta:
    market_id: str
    yes_token_id: str
    no_token_id: str
    tick_size: float
    min_order_size: float
    fee_rate: float
    is_binary_yes_no: bool = True
    validation_error: str | None = None


class MarketRegistry:
    def __init__(self, markets: dict[str, MarketMeta], allow_nonstandard_yes_no_labels: bool = False) -> None:
        self._markets = markets
        self.allow_nonstandard_yes_no_labels = allow_nonstandard_yes_no_labels
        self._token_to_market: dict[str, str] = {}
        for market_id, meta in markets.items():
            if meta.yes_token_id:
                self._token_to_market[meta.yes_token_id] = market_id
            if meta.no_token_id:
                self._token_to_market[meta.no_token_id] = market_id

    @staticmethod
    def _normalize_label(label: str) -> str:
        return re.sub(r"[^a-z0-9]", "", label.strip().lower())

    @classmethod
    def from_config(cls, cfg: dict[str, Any], market_ids: list[str]) -> "MarketRegistry":
        raw = cfg.get("market_metadata", {})
        allow_nonstandard = bool(cfg.get("markets", {}).get("allow_nonstandard_yes_no_labels", False))
        markets: dict[str, MarketMeta] = {}
        for market_id in market_ids:
            details = raw.get(market_id)
            if not details:
                markets[market_id] = MarketMeta(
                    market_id=market_id,
                    yes_token_id="",
                    no_token_id="",
                    tick_size=0.001,
                    min_order_size=1.0,
                    fee_rate=0.002,
                    is_binary_yes_no=False,
                    validation_error="missing static metadata",
                )
                continue
            yes = str(details.get("yes_token_id", "")).strip()
            no = str(details.get("no_token_id", "")).strip()
            valid = bool(yes and no)
            markets[market_id] = MarketMeta(
                market_id=market_id,
                yes_token_id=yes,
                no_token_id=no,
                tick_size=float(details.get("tick_size", 0.001)),
                min_order_size=float(details.get("min_order_size", 1.0)),
                fee_rate=float(details.get("fee_rate", 0.002)),
                is_binary_yes_no=valid,
                validation_error=None if valid else "missing yes/no token ids",
            )
        return cls(markets, allow_nonstandard_yes_no_labels=allow_nonstandard)

    def _label_sets(self) -> tuple[set[str], set[str]]:
        if self.allow_nonstandard_yes_no_labels:
            return PERMISSIVE_YES_LABELS, PERMISSIVE_NO_LABELS
        return STRICT_YES_LABELS, STRICT_NO_LABELS

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
                        if meta.yes_token_id:
                            self._token_to_market[meta.yes_token_id] = market_id
                        if meta.no_token_id:
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
            parts = [str(x).strip() for x in token_ids if str(x).strip()]
        else:
            parts = []

        outcomes = data.get("outcomes") or data.get("outcomePrices") or []
        labels = [self._normalize_label(str(x)) for x in outcomes] if isinstance(outcomes, list) else []

        tick = float(data.get("tickSize") or data.get("tick_size") or 0.001)
        min_size = float(data.get("minOrderSize") or data.get("min_order_size") or 1.0)
        fee = float(data.get("feeRate") or data.get("fee_rate") or 0.002)

        if len(parts) != 2:
            return MarketMeta(
                market_id=market_id,
                yes_token_id="",
                no_token_id="",
                tick_size=tick,
                min_order_size=min_size,
                fee_rate=fee,
                is_binary_yes_no=False,
                validation_error=f"expected 2 token ids, got {len(parts)}",
            )
        if len(labels) != 2:
            return MarketMeta(
                market_id=market_id,
                yes_token_id="",
                no_token_id="",
                tick_size=tick,
                min_order_size=min_size,
                fee_rate=fee,
                is_binary_yes_no=False,
                validation_error=f"expected 2 outcomes, got {len(labels)}",
            )

        yes_labels, no_labels = self._label_sets()
        yes_idx = no_idx = -1
        for i, label in enumerate(labels):
            if label in yes_labels and yes_idx < 0:
                yes_idx = i
            if label in no_labels and no_idx < 0:
                no_idx = i

        if yes_idx < 0 or no_idx < 0 or yes_idx == no_idx:
            return MarketMeta(
                market_id=market_id,
                yes_token_id="",
                no_token_id="",
                tick_size=tick,
                min_order_size=min_size,
                fee_rate=fee,
                is_binary_yes_no=False,
                validation_error=f"ambiguous yes/no outcomes: {labels}",
            )

        return MarketMeta(
            market_id=market_id,
            yes_token_id=parts[yes_idx],
            no_token_id=parts[no_idx],
            tick_size=tick,
            min_order_size=min_size,
            fee_rate=fee,
            is_binary_yes_no=True,
            validation_error=None,
        )

    def get(self, market_id: str) -> MarketMeta | None:
        return self._markets.get(market_id)

    def get_binary_market(self, market_id: str) -> MarketMeta | None:
        meta = self._markets.get(market_id)
        if meta is None:
            return None
        if not meta.is_binary_yes_no:
            return None
        if not meta.yes_token_id or not meta.no_token_id:
            return None
        return meta

    def get_market_id_by_token(self, token_id: str) -> str | None:
        return self._token_to_market.get(token_id)

    def enabled_market_ids(self) -> set[str]:
        return set(mid for mid, meta in self._markets.items() if meta.is_binary_yes_no)

    def disable(self, market_id: str) -> None:
        meta = self._markets.pop(market_id, None)
        if meta is None:
            return
        self._token_to_market.pop(meta.yes_token_id, None)
        self._token_to_market.pop(meta.no_token_id, None)
