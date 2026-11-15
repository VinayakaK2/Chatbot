"""Reliability subsystem: transient error classification, bounded exponential backoff with jitter, and idempotency protection."""

import asyncio
from enum import Enum
import logging
import random
import time
from typing import Any, Awaitable, Callable, Optional, TypeVar

logger = logging.getLogger("agent_runtime.core.retry")

T = TypeVar("T")


class FailureCategory(str, Enum):
    """Classification of errors encountered during external calls."""

    TRANSIENT_NETWORK = "transient_network"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    AUTHENTICATION_ERROR = "authentication_error"
    BAD_REQUEST = "bad_request"
    CONTEXT_OVERFLOW = "context_overflow"
    UNKNOWN = "unknown"


# Explicitly defined transient categories permitted for automated retry
TRANSIENT_CATEGORIES = {
    FailureCategory.TRANSIENT_NETWORK,
    FailureCategory.RATE_LIMIT,
    FailureCategory.SERVER_ERROR,
    FailureCategory.TIMEOUT,
}


def classify_error(error: Exception) -> FailureCategory:
    """Deterministically categorize an exception based on type, status code, and message patterns."""
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return FailureCategory.TIMEOUT

    # Check for HTTP status codes if present on exception
    status_code: Optional[int] = None
    if hasattr(error, "status_code"):
        status_code = getattr(error, "status_code")
    elif hasattr(error, "response") and hasattr(getattr(error, "response"), "status_code"):
        status_code = getattr(getattr(error, "response"), "status_code")

    if status_code is not None:
        if status_code == 429:
            return FailureCategory.RATE_LIMIT
        if status_code in (500, 502, 503, 504):
            return FailureCategory.SERVER_ERROR
        if status_code in (401, 403):
            return FailureCategory.AUTHENTICATION_ERROR
        if status_code in (400, 422):
            return FailureCategory.BAD_REQUEST
        if status_code == 413:
            return FailureCategory.CONTEXT_OVERFLOW

    # Check exception type name and string patterns
    err_cls = type(error).__name__
    err_msg = str(error).lower()

    if "timeout" in err_cls.lower() or "timeout" in err_msg or "timed out" in err_msg:
        return FailureCategory.TIMEOUT

    if any(k in err_cls for k in ("ConnectError", "NetworkError", "ConnectionRefusedError", "ConnectionResetError", "RemoteDisconnected")):
        return FailureCategory.TRANSIENT_NETWORK
    if isinstance(error, (ConnectionError, BrokenPipeError, ConnectionRefusedError, ConnectionResetError)):
        return FailureCategory.TRANSIENT_NETWORK

    if "rate limit" in err_msg or "too many requests" in err_msg or "429" in err_msg:
        return FailureCategory.RATE_LIMIT
    if "context window" in err_msg or "token limit" in err_msg or "maximum context length" in err_msg:
        return FailureCategory.CONTEXT_OVERFLOW
    if "unauthorized" in err_msg or "invalid api key" in err_msg or "authentication" in err_msg:
        return FailureCategory.AUTHENTICATION_ERROR
    if "bad request" in err_msg:
        return FailureCategory.BAD_REQUEST

    return FailureCategory.UNKNOWN


class RetryPolicy:
    """Bounded retry policy with exponential backoff, jitter, and idempotency protection."""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 10.0,
        jitter: bool = True,
    ) -> None:
        self.max_retries = max(0, max_retries)
        self.base_delay = max(0.01, base_delay)
        self.max_delay = max(self.base_delay, max_delay)
        self.jitter = jitter

    def is_transient(self, category: FailureCategory) -> bool:
        """Check if an error category is considered safe and transient for automated retry."""
        return category in TRANSIENT_CATEGORIES

    def compute_delay(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Compute backoff delay with optional upstream Retry-After and randomized full jitter."""
        if retry_after is not None and retry_after > 0:
            delay = min(retry_after, self.max_delay)
        else:
            delay = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))

        if self.jitter and delay > 0:
            # Full jitter: random uniform between base_delay / 2 and delay
            delay = random.uniform(self.base_delay * 0.5, delay)

        return max(0.01, delay)

    async def execute_with_retry(
        self,
        operation: Callable[[], Awaitable[T]],
        is_idempotent: bool = True,
        on_retry: Optional[Callable[[int, Exception, FailureCategory, float], None]] = None,
        cancellation_token: Optional[asyncio.Event] = None,
    ) -> T:
        """Execute an asynchronous operation with retry logic.

        Args:
            operation: Async callable taking no arguments and returning a value.
            is_idempotent: Whether repeated execution is safe and idempotent.
            on_retry: Optional callback invoked before sleeping on retry (attempt, error, category, delay).
            cancellation_token: Optional cancellation event that cancels waiting backoff immediately.

        Raises:
            Exception: The underlying exception if attempts are exhausted, error is non-transient, or call is non-idempotent.
            asyncio.CancelledError: If cancellation is signaled during backoff.
        """
        attempt = 1
        while True:
            if cancellation_token and cancellation_token.is_set():
                raise asyncio.CancelledError("Operation cancelled before execution attempt.")

            try:
                return await operation()
            except Exception as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise

                category = classify_error(exc)
                logger.debug(
                    "Operation failed on attempt %d: [%s] %s",
                    attempt,
                    category.value,
                    str(exc),
                )

                # Never retry non-idempotent operations blindly (fail-closed)
                if not is_idempotent:
                    logger.warning(
                        "Failing closed: non-idempotent operation failed with [%s], refusing automated retry: %s",
                        category.value,
                        str(exc),
                    )
                    raise

                # Non-transient errors terminate immediately
                if not self.is_transient(category):
                    logger.warning(
                        "Failing closed: error classified as permanent [%s], terminating retries: %s",
                        category.value,
                        str(exc),
                    )
                    raise

                # Check if retry budget is exhausted
                if attempt > self.max_retries:
                    logger.warning(
                        "Retry budget exhausted (%d/%d) for [%s]: %s",
                        attempt - 1,
                        self.max_retries,
                        category.value,
                        str(exc),
                    )
                    raise

                # Extract retry-after header if available and clamp to 60s
                retry_after: Optional[float] = None
                if hasattr(exc, "response") and hasattr(getattr(exc, "response"), "headers"):
                    h = getattr(getattr(exc, "response"), "headers")
                    if "retry-after" in h:
                        try:
                            parsed_after = float(h["retry-after"])
                            retry_after = min(parsed_after, 60.0)
                        except (ValueError, TypeError):
                            pass

                delay = self.compute_delay(attempt, retry_after=retry_after)
                if on_retry:
                    on_retry(attempt, exc, category, delay)

                logger.info(
                    "Retrying transient failure [%s] (attempt %d/%d) in %.2fs: %s",
                    category.value,
                    attempt,
                    self.max_retries,
                    delay,
                    str(exc),
                )

                if cancellation_token:
                    if cancellation_token.is_set():
                        raise asyncio.CancelledError("Operation cancelled before backoff sleep.")
                    try:
                        await asyncio.wait_for(cancellation_token.wait(), timeout=delay)
                        raise asyncio.CancelledError("Operation cancelled during backoff sleep.")
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(delay)

                attempt += 1

    def execute_sync_with_retry(
        self,
        operation: Callable[[], T],
        is_idempotent: bool = True,
        on_retry: Optional[Callable[[int, Exception, FailureCategory, float], None]] = None,
    ) -> T:
        """Synchronous variant of execute_with_retry."""
        attempt = 1
        while True:
            try:
                return operation()
            except Exception as exc:
                category = classify_error(exc)
                if not is_idempotent or not self.is_transient(category) or attempt > self.max_retries:
                    raise

                delay = self.compute_delay(attempt)
                if on_retry:
                    on_retry(attempt, exc, category, delay)
                time.sleep(delay)
                attempt += 1
