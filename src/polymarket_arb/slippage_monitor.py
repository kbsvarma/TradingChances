from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass(slots=True)
class SlippageMonitor:
    multiplier: float = 1.5
    window_size: int = 50
    baseline_buffer: float = 0.0
    expected_by_client_id: dict[str, tuple[str, float]] = field(default_factory=dict)
    slippage_by_market: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def record_expected(self, client_order_id: str, market_id: str, expected_price: float) -> None:
        self.expected_by_client_id[client_order_id] = (market_id, expected_price)

    def clear_expected(self, client_order_id: str) -> None:
        self.expected_by_client_id.pop(client_order_id, None)

    def record_fill(self, client_order_id: str, fill_price: float) -> float | None:
        expected = self.expected_by_client_id.get(client_order_id)
        if expected is None:
            return None
        market_id, expected_price = expected
        slip = abs(fill_price - expected_price)
        dq = self.slippage_by_market[market_id]
        dq.append(slip)
        while len(dq) > self.window_size:
            dq.popleft()
        return slip

    def rolling_p95(self, market_id: str) -> float:
        dq = self.slippage_by_market.get(market_id)
        if not dq:
            return 0.0
        arr = sorted(dq)
        idx = int(0.95 * (len(arr) - 1))
        return float(arr[idx])

    def adaptive_buffer(self, market_id: str) -> float:
        return max(self.baseline_buffer, self.rolling_p95(market_id) * self.multiplier)
