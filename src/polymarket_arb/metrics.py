from __future__ import annotations

import statistics
from collections import defaultdict, deque


class Metrics:
    def __init__(self) -> None:
        self.latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=5000))
        self.counters: dict[str, int] = defaultdict(int)

    def observe_latency(self, key: str, value_ms: float) -> None:
        self.latencies[key].append(value_ms)

    def inc(self, key: str, n: int = 1) -> None:
        self.counters[key] += n

    def ratio(self, num_key: str, den_key: str) -> float:
        den = self.counters.get(den_key, 0)
        if den == 0:
            return 0.0
        return self.counters.get(num_key, 0) / den

    def summary(self) -> dict:
        out: dict[str, float] = {}
        for key, values in self.latencies.items():
            if not values:
                continue
            arr = sorted(values)
            out[f"{key}_p50"] = arr[int(0.50 * (len(arr) - 1))]
            out[f"{key}_p95"] = arr[int(0.95 * (len(arr) - 1))]
            out[f"{key}_p99"] = arr[int(0.99 * (len(arr) - 1))]
            out[f"{key}_mean"] = statistics.fmean(arr)
        for k, v in self.counters.items():
            out[k] = float(v)
        out["fill_cancel_ratio"] = self.ratio("fill", "cancel")
        out["fill_reject_ratio"] = self.ratio("fill", "reject")
        return out


class PickedOffDetector:
    """Flags fills that move against us immediately."""

    def __init__(self, adverse_move_bps: float = 30.0) -> None:
        self.adverse_move_bps = adverse_move_bps

    def is_picked_off(self, fill_price: float, post_fill_best: float, side: str) -> bool:
        if fill_price <= 0:
            return False
        if side == "buy":
            move = (fill_price - post_fill_best) / fill_price * 10000
        else:
            move = (post_fill_best - fill_price) / fill_price * 10000
        return move > self.adverse_move_bps
