"""Tests for reliability subsystem: transient error classification, bounded exponential backoff, and idempotency protection."""

import asyncio
from typing import Optional
import pytest

from core.retry import (
    FailureCategory,
    RetryPolicy,
    TRANSIENT_CATEGORIES,
    classify_error,
)


class DummyHTTPError(Exception):
    def __init__(self, message: str, status_code: int, headers: Optional[dict] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = type("Resp", (), {"status_code": status_code, "headers": headers or {}})()


def test_classify_transient_errors() -> None:
    """Verify transient error classification matches design specification."""
    # Network / connection
    assert classify_error(ConnectionResetError("Connection reset by peer")) == FailureCategory.TRANSIENT_NETWORK
    assert classify_error(ConnectionRefusedError("Connection refused")) == FailureCategory.TRANSIENT_NETWORK
    assert classify_error(type("ConnectError", (Exception,), {})("Failed to connect")) == FailureCategory.TRANSIENT_NETWORK

    # Timeouts
    assert classify_error(asyncio.TimeoutError()) == FailureCategory.TIMEOUT
    assert classify_error(TimeoutError("Request timed out")) == FailureCategory.TIMEOUT
    assert classify_error(type("ReadTimeout", (Exception,), {})("Read timed out")) == FailureCategory.TIMEOUT

    # Rate limits
    assert classify_error(DummyHTTPError("Rate limit exceeded", status_code=429)) == FailureCategory.RATE_LIMIT

    # Server errors
    for code in (500, 502, 503, 504):
        assert classify_error(DummyHTTPError(f"Server error {code}", status_code=code)) == FailureCategory.SERVER_ERROR


def test_classify_permanent_errors_never_transient() -> None:
    """Verify permanent errors are classified and excluded from transient retry categories."""
    auth_err = DummyHTTPError("Unauthorized", status_code=401)
    forbidden_err = DummyHTTPError("Forbidden", status_code=403)
    bad_req_err = DummyHTTPError("Bad Request", status_code=400)
    unprocessable_err = DummyHTTPError("Unprocessable Entity", status_code=422)
    overflow_err = DummyHTTPError("Payload Too Large", status_code=413)

    assert classify_error(auth_err) == FailureCategory.AUTHENTICATION_ERROR
    assert classify_error(forbidden_err) == FailureCategory.AUTHENTICATION_ERROR
    assert classify_error(bad_req_err) == FailureCategory.BAD_REQUEST
    assert classify_error(unprocessable_err) == FailureCategory.BAD_REQUEST
    assert classify_error(overflow_err) == FailureCategory.CONTEXT_OVERFLOW
    assert classify_error(ValueError("Context window exceeded maximum length")) == FailureCategory.CONTEXT_OVERFLOW

    policy = RetryPolicy()
    for err in (auth_err, forbidden_err, bad_req_err, unprocessable_err, overflow_err):
        cat = classify_error(err)
        assert policy.is_transient(cat) is False, f"Category {cat} should NOT be transient"


def test_exponential_backoff_delay_calculation() -> None:
    """Verify exponential backoff progression and maximum ceiling."""
    policy = RetryPolicy(max_retries=3, base_delay=1.0, max_delay=10.0, jitter=False)

    # Attempt 1: 1.0s * (2^0) = 1.0s
    assert policy.compute_delay(attempt=1) == 1.0
    # Attempt 2: 1.0s * (2^1) = 2.0s
    assert policy.compute_delay(attempt=2) == 2.0
    # Attempt 3: 1.0s * (2^2) = 4.0s
    assert policy.compute_delay(attempt=3) == 4.0
    # Attempt 4: 1.0s * (2^3) = 8.0s
    assert policy.compute_delay(attempt=4) == 8.0
    # Attempt 5: 1.0s * (2^4) = 16.0s capped at max_delay=10.0s
    assert policy.compute_delay(attempt=5) == 10.0


def test_retry_after_header_handling() -> None:
    """Verify that an explicit upstream retry-after delay is respected within max bounds."""
    policy = RetryPolicy(max_retries=3, base_delay=0.5, max_delay=15.0, jitter=False)

    delay = policy.compute_delay(attempt=1, retry_after=7.5)
    assert delay == 7.5

    # Capped if exceeding max_delay
    delay_capped = policy.compute_delay(attempt=1, retry_after=25.0)
    assert delay_capped == 15.0


def test_backoff_jitter_bounds() -> None:
    """Verify that jitter randomizes delays within valid positive bounds."""
    policy = RetryPolicy(max_retries=3, base_delay=1.0, max_delay=10.0, jitter=True)
    delays = [policy.compute_delay(attempt=2) for _ in range(50)]

    # Attempt 2 has theoretical max of 2.0s and min of 0.5s
    assert all(0.5 <= d <= 2.0 for d in delays)
    # Ensure they are not all identical (properly random)
    assert len(set(delays)) > 1


@pytest.mark.asyncio
async def test_execute_with_retry_success_after_transient_failures() -> None:
    """Verify operation succeeds when a transient error resolves within budget."""
    policy = RetryPolicy(max_retries=3, base_delay=0.01, jitter=False)
    attempts = 0
    retry_log = []

    async def flaky_call():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionResetError("Temporary blip")
        return "SUCCESS"

    def on_retry(att, err, cat, delay):
        retry_log.append((att, cat))

    result = await policy.execute_with_retry(flaky_call, is_idempotent=True, on_retry=on_retry)
    assert result == "SUCCESS"
    assert attempts == 3
    assert len(retry_log) == 2
    assert retry_log[0][1] == FailureCategory.TRANSIENT_NETWORK


@pytest.mark.asyncio
async def test_execute_with_retry_budget_exhaustion() -> None:
    """Verify operation raises the underlying exception once retry budget is exhausted."""
    policy = RetryPolicy(max_retries=2, base_delay=0.01, jitter=False)
    attempts = 0

    async def always_failing():
        nonlocal attempts
        attempts += 1
        raise DummyHTTPError("Gateway Timeout", status_code=504)

    with pytest.raises(DummyHTTPError) as exc_info:
        await policy.execute_with_retry(always_failing, is_idempotent=True)

    assert exc_info.value.status_code == 504
    # 1 initial attempt + 2 retries = 3 attempts total
    assert attempts == 3


@pytest.mark.asyncio
async def test_fail_closed_non_idempotent_operation_never_retried() -> None:
    """Verify fail-closed requirement: non-idempotent operations must NEVER retry automatically."""
    policy = RetryPolicy(max_retries=3, base_delay=0.01, jitter=False)
    attempts = 0

    async def mutating_operation():
        nonlocal attempts
        attempts += 1
        raise DummyHTTPError("Internal Server Error", status_code=500)

    # is_idempotent=False must terminate immediately on attempt 1 without retry
    with pytest.raises(DummyHTTPError):
        await policy.execute_with_retry(mutating_operation, is_idempotent=False)

    assert attempts == 1, "Non-idempotent operation must NOT be retried"


@pytest.mark.asyncio
async def test_permanent_error_terminates_immediately_without_retry() -> None:
    """Verify permanent errors (e.g. 401 Unauthorized) terminate on first failure."""
    policy = RetryPolicy(max_retries=3, base_delay=0.01, jitter=False)
    attempts = 0

    async def unauthorized_call():
        nonlocal attempts
        attempts += 1
        raise DummyHTTPError("Unauthorized", status_code=401)

    with pytest.raises(DummyHTTPError):
        await policy.execute_with_retry(unauthorized_call, is_idempotent=True)

    assert attempts == 1, "Permanent error must NOT be retried"
