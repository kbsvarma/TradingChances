from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass(slots=True)
class EdgePair:
    predicted: float
    realized: float


@dataclass(slots=True)
class EdgeQualityGuard:
    window_size: int = 30
    min_ratio: float = 0.5
    min_trades: int = 15
    pairs_by_market: dict[str, deque[EdgePair]] = field(default_factory=lambda: defaultdict(deque))

    def record(self, market_id: str, predicted_edge: float, realized_edge: float) -> None:
        if predicted_edge <= 0:
            return
        dq = self.pairs_by_market[market_id]
        dq.append(EdgePair(predicted=predicted_edge, realized=realized_edge))
        while len(dq) > self.window_size:
            dq.popleft()

    def quality_ratio(self, market_id: str) -> float | None:
        dq = self.pairs_by_market.get(market_id)
        if not dq or len(dq) < self.min_trades:
            return None
        pred_avg = sum(p.predicted for p in dq) / len(dq)
        if pred_avg <= 0:
            return None
        real_avg = sum(p.realized for p in dq) / len(dq)
        return real_avg / pred_avg

    def should_disable(self, market_id: str) -> bool:
        ratio = self.quality_ratio(market_id)
        if ratio is None:
            return False
        return ratio < self.min_ratio
