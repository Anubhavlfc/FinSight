"""Small in-memory sliding-window rate limiter (per process; enough for one API instance)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, bucket: str, key: str, limit: int, window_seconds: float) -> float | None:
        """Record a hit. Returns None if allowed, else the seconds until the next slot frees up."""
        now = self._clock()
        with self._lock:
            q = self._hits[(bucket, key)]
            while q and now - q[0] >= window_seconds:
                q.popleft()
            if len(q) >= limit:
                return max(1.0, window_seconds - (now - q[0]))
            q.append(now)
            if len(self._hits) > 20_000:  # drop idle keys so the table cannot grow without bound
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return None
