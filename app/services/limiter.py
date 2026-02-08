"""
AetherGate — In-Memory Sliding-Window Rate Limiter

Storage: per-process dict keyed by API key hash.
Format:  "count/period"  →  10/m  |  100/h  |  1000/d
"""

import time
from dataclasses import dataclass, field


_PERIOD_MAP: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


@dataclass
class _Window:
    """Tracks request count within a fixed time window."""
    window_start: float = 0.0
    count: int = 0


def _parse_limit(limit_model: str) -> tuple[int, int]:
    """
    Parse a limit string like '60/m' into (max_count, period_seconds).
    Raises ValueError on bad format.
    """
    try:
        raw_count, raw_period = limit_model.strip().split("/", maxsplit=1)
        max_count = int(raw_count)
        period_seconds = _PERIOD_MAP[raw_period.lower()]
        return max_count, period_seconds
    except (ValueError, KeyError) as exc:
        raise ValueError(
            f"Invalid rate limit format '{limit_model}'. "
            f"Expected 'count/period' where period is one of: {', '.join(_PERIOD_MAP)}"
        ) from exc


class InMemoryRateLimiter:
    """
    Fixed-window rate limiter backed by a plain dict.

    Thread-safety note: CPython's GIL makes dict operations atomic for
    single-threaded async (uvicorn default). For multi-worker deploys,
    swap this for Redis.
    """

    DEFAULT_LIMIT: str = "60/m"

    def __init__(self) -> None:
        # {key_hash: _Window}
        self._windows: dict[str, _Window] = {}

    def check_limit(self, key_hash: str, limit_model: str | None = None) -> bool:
        """
        Returns True if the request is allowed, False if rate-limited.

        Parameters
        ----------
        key_hash : str
            The SHA-256 hash of the API key (unique per key).
        limit_model : str | None
            Rate limit specification, e.g. "60/m". Falls back to DEFAULT_LIMIT.
        """
        spec = limit_model or self.DEFAULT_LIMIT
        max_count, period_seconds = _parse_limit(spec)

        now = time.monotonic()
        window = self._windows.get(key_hash)

        # First request or window expired → reset
        if window is None or (now - window.window_start) >= period_seconds:
            self._windows[key_hash] = _Window(window_start=now, count=1)
            return True

        # Window still active — check count
        if window.count >= max_count:
            return False

        window.count += 1
        return True

    def get_remaining(self, key_hash: str, limit_model: str | None = None) -> tuple[int, float]:
        """
        Returns (remaining_requests, seconds_until_reset).
        Useful for X-RateLimit-* response headers.
        """
        spec = limit_model or self.DEFAULT_LIMIT
        max_count, period_seconds = _parse_limit(spec)

        now = time.monotonic()
        window = self._windows.get(key_hash)

        if window is None or (now - window.window_start) >= period_seconds:
            return max_count, 0.0

        remaining = max(0, max_count - window.count)
        reset_in = max(0.0, period_seconds - (now - window.window_start))
        return remaining, reset_in
