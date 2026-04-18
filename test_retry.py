import httpx

from utils.retry import is_transient_error


def test_is_transient_error_treats_dns_resolution_failure_as_retryable():
    err = Exception('[Errno 7] No address associated with hostname')
    assert is_transient_error(err) is True


def test_is_transient_error_treats_http_429_as_retryable():
    request = httpx.Request('POST', 'https://www.agenthansa.com/api/red-packets/x/join')
    response = httpx.Response(429, request=request, text='{"detail":"Too many requests. Slow down."}')
    err = httpx.HTTPStatusError('rate limited', request=request, response=response)
    assert is_transient_error(err) is True


def test_is_transient_error_does_not_retry_http_409_conflict():
    request = httpx.Request('POST', 'https://www.agenthansa.com/api/forum/post-1/vote')
    response = httpx.Response(409, request=request, text='{"detail":"Already voted"}')
    err = httpx.HTTPStatusError('already voted', request=request, response=response)
    assert is_transient_error(err) is False


def test_is_transient_error_does_not_retry_http_400_bad_request():
    request = httpx.Request('POST', 'https://www.agenthansa.com/api/red-packets/x/join')
    response = httpx.Response(400, request=request, text='{"detail":"Challenge not completed"}')
    err = httpx.HTTPStatusError('bad request', request=request, response=response)
    assert is_transient_error(err) is False
