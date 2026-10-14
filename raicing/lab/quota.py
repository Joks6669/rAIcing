"""Interaction quota, counted at the source.

One interaction = one decision of one learner-controlled car (or one teacher decision used as a
demonstration). Opponent cars never count. The counter is exact: an environment asks for the
interactions it is about to use *before* stepping, and the request is refused as a whole if it
does not fit, so ``used`` never exceeds ``limit``.
"""
from __future__ import annotations

import threading


class QuotaExceeded(RuntimeError):
    """Raised when a step would use more interactions than the remaining quota."""


class QuotaCounter:
    """Exact, thread-safe interaction counter.

    >>> q = QuotaCounter(10)
    >>> q.consume(4); q.remaining
    6
    """

    def __init__(self, limit: int):
        if int(limit) < 0:
            raise ValueError("le quota doit être positif ou nul")
        self.limit = int(limit)
        self.used = 0
        self._lock = threading.Lock()

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    def can(self, n: int) -> bool:
        """True if ``n`` more interactions fit in the quota."""
        return self.used + int(n) <= self.limit

    def consume(self, n: int) -> int:
        """Count ``n`` interactions; raise ``QuotaExceeded`` (and count nothing) if they do not fit."""
        n = int(n)
        if n < 0:
            raise ValueError("nombre d'interactions négatif")
        with self._lock:
            if self.used + n > self.limit:
                raise QuotaExceeded(
                    f"quota d'interactions épuisé : {self.used} utilisées sur {self.limit}, {n} demandées")
            self.used += n
        return self.used

    def __repr__(self) -> str:
        return f"QuotaCounter(used={self.used}, limit={self.limit})"
