from __future__ import annotations

from dataclasses import dataclass, field

from polymarket_arb.types import BookState, OrderBookLevel


@dataclass(slots=True)
class BookStore:
    books: dict[tuple[str, str], BookState] = field(default_factory=dict)

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
        self._validate(book)
        self.books[(market_id, token_id)] = book
        return book

    @staticmethod
    def _validate(book: BookState) -> None:
        if not isinstance(book.active, bool):
            raise ValueError("market active state must be bool")
        for level in book.bids + book.asks:
            if level.size < 0:
                raise ValueError("negative size in order book")
        if book.bids and book.asks and book.best_bid() is not None and book.best_ask() is not None:
            if book.best_bid() > book.best_ask():
                raise ValueError("crossed order book")
