from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class UserWSWatchdog:
    timeout_sec: float
    last_event_ts: float = 0.0

    def __post_init__(self) -> None:
        if self.last_event_ts <= 0:
            self.last_event_ts = time.time()

    def touch(self, ts: float | None = None) -> None:
        self.last_event_ts = ts or time.time()

    def is_timed_out(self, now_ts: float | None = None) -> bool:
        now = now_ts or time.time()
        return (now - self.last_event_ts) > self.timeout_sec
