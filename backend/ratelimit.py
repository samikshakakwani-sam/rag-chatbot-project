"""
backend/ratelimit.py
--------------------
Lightweight in-memory, per-client sliding-window rate limiter (stdlib only).

Used to protect POST /api/chat from abuse. Suitable for a single-process
deployment; a multi-instance deployment would swap this for a shared store
(e.g. Redis) behind the same `allow()` interface.

Time is injected (default monotonic clock) so tests are deterministic.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Deque, Dict

from backend import config


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max = max_requests
        self.window = window_seconds
        self._clock = clock
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Record a hit for `key`; return False if it exceeds the window quota."""
        now = self._clock()
        cutoff = now - self.window
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            while dq and dq[0] < cutoff:      # drop timestamps outside the window
                dq.popleft()
            if len(dq) >= self.max:
                return False
            dq.append(now)
            return True

    def retry_after(self, key: str) -> int:
        """Seconds until the oldest in-window hit expires (for Retry-After)."""
        with self._lock:
            dq = self._hits.get(key)
            if not dq:
                return 0
            return max(1, int(self.window - (self._clock() - dq[0])))

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# Shared instance built from config.
limiter = RateLimiter(config.RATE_LIMIT_MAX, config.RATE_LIMIT_WINDOW)
