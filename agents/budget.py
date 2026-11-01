"""Request limits for application dispatches, not observed usage or provider charges."""

from contextvars import ContextVar
import math
import threading
import time


current_budget = ContextVar("request_budget", default=None)
_KINDS = frozenset({"chat", "embedding", "rerank", "local_embedding", "local_rerank"})
_REASONS = frozenset({"cancelled", "deadline", "call_limit", "attempt_limit", "output_limit", "completed", "failed"})


class BudgetExceeded(RuntimeError):
    """Expose only a fixed reason, never exception or request text."""

    def __init__(self, reason):
        self.reason = reason if isinstance(reason, str) and reason in _REASONS else "cancelled"
        super().__init__(self.reason)


class RequestBudget:
    def __init__(self, max_calls=12, timeout_seconds=60, max_output_tokens=1024,
                 max_attempts_per_call=2, clock=time.monotonic):
        for name, value in (("max_calls", max_calls), ("max_output_tokens", max_output_tokens),
                            ("max_attempts_per_call", max_attempts_per_call)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("timeout_seconds must be finite and positive")
        self.max_calls = max_calls
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.max_attempts_per_call = max_attempts_per_call
        self._clock = clock
        self._deadline = clock() + timeout_seconds
        self._lock = threading.Lock()
        self._counts = dict.fromkeys(sorted(_KINDS), 0)
        self._total_calls = 0
        self._closed = False
        self._reason = None

    def _check_locked(self):
        if not self._closed and self._clock() >= self._deadline:
            self._closed = True
            self._reason = "deadline"
        if self._closed:
            raise BudgetExceeded(self._reason)

    def check(self):
        with self._lock:
            self._check_locked()

    def reserve(self, kind):
        """Count an application attempt immediately before its actual dispatch."""
        if not isinstance(kind, str) or kind not in _KINDS:
            raise ValueError("unknown budget kind")
        with self._lock:
            self._check_locked()
            if self._total_calls >= self.max_calls:
                self._closed = True
                self._reason = "call_limit"
                raise BudgetExceeded(self._reason)
            self._counts[kind] += 1
            self._total_calls += 1

    def cancel(self, reason="cancelled"):
        """Block future actions; this does not stop already running threads or calls."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._reason = reason if isinstance(reason, str) and reason in _REASONS else "cancelled"

    @property
    def remaining_seconds(self):
        with self._lock:
            return max(0.0, self._deadline - self._clock())

    def snapshot(self):
        with self._lock:
            return {
                "max_calls": self.max_calls,
                "timeout_seconds": self.timeout_seconds,
                "max_output_tokens": self.max_output_tokens,
                "max_attempts_per_call": self.max_attempts_per_call,
                "total_calls": self._total_calls,
                "counts": dict(self._counts),
                "closed": self._closed,
                "reason": self._reason,
                "remaining_seconds": max(0.0, self._deadline - self._clock()),
            }
