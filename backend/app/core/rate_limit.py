"""Small in-process sliding-window rate limiter.

Good enough for a single instance; for horizontal scaling swap the store for Redis
(the interface `hit(key, limit)` stays the same)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.core.errors import RateLimited


class SlidingWindowLimiter:
    def __init__(self, window_seconds: float = 60.0):
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_cleanup = 0.0

    def hit(self, key: str, limit: int) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_cleanup >= self.window:
                self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v and now - v[-1] <= self.window})
                self._last_cleanup = now
            if key not in self._hits and len(self._hits) >= 10000:
                raise RateLimited("Xidmət məşğuldur. Bir az sonra yenidən cəhd edin.")
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= limit:
                raise RateLimited("Çox sayda sorğu göndərildi. Bir az sonra yenidən cəhd edin.")
            q.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()
