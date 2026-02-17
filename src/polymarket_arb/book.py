from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from polymarket_arb.types import BookState, OrderBookLevel


@dataclass(slots=True)
class BookStore:
    books: dict[tuple[str, str], BookState] = field(default_factory=dict)
    history: dict[tuple[str, str], deque[BookState]] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=3000)))

    def get(self, market_id: str, token_id: str) -> BookState | None:
        return self.books.get((market_id, token_id))

    def upsert(
        self,
        market_id: str,
        token_id: str,
        bids: list[dict],
        asks: list[dict],
        recv_ts: float,
        exchange_ts: int | None,
        active: bool = True,
        require_nonempty_if_active: bool = True,
    ) -> BookState:
        book = BookState(
            market_id=market_id,
            token_id=token_id,
            bids=[OrderBookLevel(float(x["price"]), float(x["size"])) for x in bids],
            asks=[OrderBookLevel(float(x["price"]), float(x["size"])) for x in asks],
            recv_ts=recv_ts,
            exchange_ts=exchange_ts,
            active=active,
        )
        self._validate(book, require_nonempty_if_active=require_nonempty_if_active)
        key = (market_id, token_id)
        self.books[key] = book
        self.history[key].append(book)
        return book

    def closest_snapshot(self, market_id: str, token_id: str, ts: float, max_age_ms: int) -> BookState | None:
        key = (market_id, token_id)
        hist = self.history.get(key)
        if not hist:
            return None
        best: BookState | None = None
        best_dt = float("inf")
        for snap in reversed(hist):
            dt = abs((snap.recv_ts - ts) * 1000)
            if dt < best_dt:
                best = snap
                best_dt = dt
            if snap.recv_ts < ts and dt > max_age_ms:
                break
        if best is None or best_dt > max_age_ms:
            return None
        return best

    @staticmethod
    def _validate(book: BookState, require_nonempty_if_active: bool) -> None:
        if not isinstance(book.active, bool):
            raise ValueError("market active state must be bool")
        for level in book.bids + book.asks:
            if level.size < 0:
                raise ValueError("negative size in order book")
        if book.bids and book.asks and book.best_bid() is not None and book.best_ask() is not None:
            if book.best_bid() >= book.best_ask():
                raise ValueError("crossed order book")
        if require_nonempty_if_active and book.active and (not book.bids and not book.asks):
            raise ValueError("empty active book")

    def mark_stale(self, market_id: str, token_id: str) -> None:
        book = self.books.get((market_id, token_id))
        if not book:
            return
        book.active = False
        book.recv_ts = time.time()
