"""Auto-recovering quarantine for keyed flaky resources.

After N consecutive failures on the same key, sideline that key for a
recovery window. On window expiry, retry once. If still failing,
re-quarantine immediately (count persists across the window). On success,
reset everything.

Extracted from a production trading bot that needed to back off from
intermittently-wedged exchange/symbol pairs without giving up permanently.
See https://github.com/CR8C0NT1NUM/ccxt-auto-recovering-quarantine.
"""
from __future__ import annotations

import time
from typing import Callable, Generic, Hashable, TypeVar

K = TypeVar("K", bound=Hashable)


class AutoRecoveringQuarantine(Generic[K]):
    """Quarantine flaky keys after N consecutive failures with auto-recovery.

    Args:
        threshold: Number of consecutive failures before quarantining.
        recovery_seconds: How long a key stays quarantined before allowing retry.
        clock: Monotonic time source in seconds. Default `time.monotonic`.
            Override for testing.
    """

    def __init__(
        self,
        *,
        threshold: int,
        recovery_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        if recovery_seconds < 0:
            raise ValueError("recovery_seconds must be >= 0")
        self._threshold = threshold
        self._recovery_seconds = recovery_seconds
        self._clock = clock
        self._failure_count: dict[K, int] = {}
        self._skip_until: dict[K, float] = {}

    def is_quarantined(self, key: K) -> bool:
        """Return True if `key` is currently sidelined.

        Idempotent. If the recovery window has expired, clears the deadline
        (but preserves the failure count) so the next failure re-quarantines
        immediately rather than after another full threshold-count of failures.
        """
        until = self._skip_until.get(key)
        if until is None:
            return False
        if self._clock() < until:
            return True
        self._skip_until.pop(key, None)
        return False

    def record_success(self, key: K) -> None:
        """Clear failure count and any active quarantine for `key`."""
        self._failure_count.pop(key, None)
        self._skip_until.pop(key, None)

    def record_failure(self, key: K) -> int:
        """Increment failure count for `key`. Quarantine if threshold reached.

        Returns the new failure count.
        """
        count = self._failure_count.get(key, 0) + 1
        self._failure_count[key] = count
        if count >= self._threshold:
            self._skip_until[key] = self._clock() + self._recovery_seconds
        return count
