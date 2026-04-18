from __future__ import annotations

import logging
from typing import Any

import httpx

from config import Settings
from utils.retry import is_transient_error, retry_call


class AgentHansaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.log = logging.getLogger('client')
        self.client = httpx.Client(
            base_url=settings.base_url,
            timeout=settings.http_timeout_seconds,
            headers={
                'Authorization': f'Bearer {settings.api_key}',
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'User-Agent': 'agenthansa_bot/1.0 (+termux)',
            },
        )

    def close(self) -> None:
        self.client.close()

    def _decode_response(self, response: httpx.Response) -> Any:
        if not response.content:
            return None
        content_type = response.headers.get('content-type', '')
        if 'application/json' in content_type:
            return response.json()
        return response.text

    def _request_once(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return self._decode_response(response)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        def _call() -> Any:
            return self._request_once(method, path, **kwargs)

        return retry_call(
            _call,
            attempts=self.settings.max_http_retries,
            base_sleep=1.5,
            retry_on=(Exception,),
            should_retry=is_transient_error,
        )

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._request('GET', path, **kwargs)

    def post(self, path: str, json: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self._request('POST', path, json=json, **kwargs)

    def patch(self, path: str, json: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self._request('PATCH', path, json=json, **kwargs)

    def get_optional(self, path: str, default: Any = None) -> Any:
        try:
            return self.get(path)
        except Exception as exc:
            self.log.warning('optional_get_failed path=%s error=%s', path, exc)
            return default
