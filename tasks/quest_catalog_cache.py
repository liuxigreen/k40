from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from client import AgentHansaClient
from state import JsonStateStore
from utils.timezone import utc_now

CACHE_KEY = 'quest_catalog_cache'
DEFAULT_TTL_SECONDS = 180


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def _is_fresh(cached: dict[str, Any], ttl_seconds: int) -> bool:
    checked_at = _parse_iso(cached.get('checked_at'))
    if checked_at is None:
        return False
    return checked_at + timedelta(seconds=ttl_seconds) > utc_now()


def save_quest_catalog(store: JsonStateStore, data: dict[str, Any]) -> dict[str, Any]:
    cached = {
        'checked_at': utc_now().isoformat(),
        'data': data,
    }
    store.save(CACHE_KEY, cached)
    return data


def load_quest_catalog(
    client: AgentHansaClient,
    store: JsonStateStore,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    cached = store.load(CACHE_KEY, default={})
    if isinstance(cached, dict) and _is_fresh(cached, ttl_seconds):
        data = cached.get('data')
        if isinstance(data, dict):
            return data
    data = client.get('/alliance-war/quests')
    if isinstance(data, dict):
        save_quest_catalog(store, data)
        return data
    wrapped = {'quests': data if isinstance(data, list) else []}
    save_quest_catalog(store, wrapped)
    return wrapped
