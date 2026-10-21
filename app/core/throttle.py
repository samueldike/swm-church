"""Small in-process failure throttle (login brute-force protection).

Counts recent failures per key inside a sliding window. State lives in this process, so with N
workers an attacker gets up to N x the limit; that still turns unlimited guessing into a slow crawl.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

_failures: dict[str, deque[float]] = defaultdict(deque)


def _prune(key: str, window: float, now: float) -> deque[float]:
    q = _failures[key]
    while q and now - q[0] > window:
        q.popleft()
    if not q:
        _failures.pop(key, None)
        return deque()
    return q


def is_blocked(key: str, limit: int = 5, window: float = 900.0) -> int:
    """Seconds until ``key`` may try again, or 0 when it is not blocked."""
    now = time.monotonic()
    q = _prune(key, window, now)
    if len(q) >= limit:
        return max(1, int(window - (now - q[0])))
    return 0


def record_failure(key: str, window: float = 900.0) -> None:
    now = time.monotonic()
    _prune(key, window, now)
    _failures[key].append(now)


def clear(key: str) -> None:
    _failures.pop(key, None)


def reset_all() -> None:
    _failures.clear()
