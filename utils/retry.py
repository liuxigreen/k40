from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar('T')


def retry_call(
    fn: Callable[[], T],
    attempts: int = 4,
    base_sleep: float = 1.2,
    retry_on: tuple[type[Exception], ...] = (Exception,),
    should_retry: Callable[[Exception], bool] | None = None,
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:  # type: ignore[misc]
            last_error = exc
            if should_retry is not None and not should_retry(exc):
                raise
            if attempt >= attempts:
                raise
            time.sleep(base_sleep * attempt)
    assert last_error is not None
    raise last_error


def retryable_text(error: Exception) -> str:
    return str(error).strip().lower()


def is_transient_error(error: Exception) -> bool:
    text = retryable_text(error)
    markers = (
        'connection reset',
        'timed out',
        'timeout',
        'temporary failure',
        'temporarily unavailable',
        'server disconnected',
        'no address associated with hostname',
        'name or service not known',
        'temporary failure in name resolution',
        'nodename nor servname provided, or not known',
        '502',
        '503',
        '504',
        '429',
        'try again',
    )
    if any(marker in text for marker in markers):
        return True

    status_code = None
    response = getattr(error, 'response', None)
    if response is not None:
        status_code = getattr(response, 'status_code', None)

    # Only rate limiting and 5xx-style upstream failures should be retried.
    # Permanent 4xx responses like 400/404/409 must surface immediately so the
    # caller can try a different post, mark manual_required, or stop cleanly.
    return status_code in {429, 502, 503, 504}
