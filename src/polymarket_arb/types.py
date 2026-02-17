from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


Side = Literal["buy", "sell"]
MakerTaker = Literal["maker", "taker"]


class EngineState(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    FLATTENING = "FLATTENING"
    SAFE = "SAFE"


class CommandType(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    FLATTEN = "flatten"
    SET_PARAMS = "set_params"
    MARKETS_ON = "markets_on"
    MARKETS_OFF = "markets_off"
    RELOAD_CONFIG = "reload_config"
    BACKTEST = "backtest"
    STOP = "stop"


class EventType(str, Enum):
    ORDER_BOOK_UPDATE = "OrderBookUpdate"
    ORDER_ACK = "OrderAck"
    FILL = "Fill"
    CANCEL = "Cancel"
    REJECT = "Reject"
    WS_HEALTH = "WSHealth"


@dataclass(slots=True)
class Command:
    type: CommandType
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OrderBookLevel:
    price: float
    size: float


@dataclass(slots=True)
class BookState:
    market_id: str
    token_id: str
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)
    exchange_ts: int | None = None
    recv_ts: float = 0.0
    active: bool = True

    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None


@dataclass(slots=True)
class Position:
    market_id: str
    token_id: str
    qty: float = 0.0
    avg_price: float = 0.0


@dataclass(slots=True)
class NormalizedEvent:
    event_type: EventType
    market_id: str
    token_id: str | None
    payload: dict[str, Any]
    recv_ts: float
    exchange_ts: int | None = None
    correlation_id: str | None = None


class IntentType(str, Enum):
    PLACE = "place"
    CANCEL = "cancel"
    NOOP = "noop"


@dataclass(slots=True)
class Intent:
    intent_type: IntentType
    market_id: str
    token_id: str
    side: Side | None = None
    price: float | None = None
    size: float | None = None
    ttl_ms: int | None = None
    maker_or_taker: MakerTaker | None = None
    order_id: str | None = None
    reason: str = ""


class OrderStatus(str, Enum):
    NEW = "NEW"
    SENT = "SENT"
    ACKED = "ACKED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CLOSED = "CLOSED"
    CANCEL_SENT = "CANCEL_SENT"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(slots=True)
class ManagedOrder:
    client_order_id: str
    market_id: str
    token_id: str
    side: Side
    price: float
    size: float
    remaining_size: float
    created_ts: float
    ttl_ms: int
    status: OrderStatus = OrderStatus.NEW
    venue_order_id: str | None = None
    last_update_ts: float = 0.0
    ack_ts: float | None = None
    first_fill_ts: float | None = None


@dataclass(slots=True)
class FillRecord:
    market_id: str
    token_id: str
    side: Side
    price: float
    size: float
    ts: float
    order_id: str | None = None
    client_order_id: str | None = None
    fee: float = 0.0
