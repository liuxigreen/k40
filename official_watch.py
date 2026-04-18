from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from config import Settings
from state import JsonStateStore
from utils.retry import is_transient_error, retry_call
from utils.timezone import utc_now

SOURCES = {
    'openapi': 'https://www.agenthansa.com/openapi.json',
    'docs': 'https://www.agenthansa.com/docs',
    'llms_full_local': None,
}


MODULE_HINTS = {
    '/api/red-packets': 'tasks.redpacket / redpacket_watch',
    '/api/alliance-war/quests': 'tasks.quests / tasks.my_submissions / tasks.alliance_voting',
    '/api/forum': 'tasks.forum_strategy',
    '/api/agents/my-daily-xp': 'tasks.daily_xp',
    '/api/agents/feed': 'tasks.feed',
    '/api/agents/notifications': 'notification_watch',
}


def _fetch_text(url: str) -> str:
    with httpx.Client(timeout=25, headers={'User-Agent': 'agenthansa_bot/1.0'}) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def _fetch_text_with_fallback(
    url: str,
    *,
    previous: dict[str, Any] | None = None,
    client: Any = None,
    attempts: int = 3,
    base_sleep: float = 1.0,
) -> tuple[str, dict[str, Any]]:
    attempt_count = 0

    def _call() -> str:
        nonlocal attempt_count
        attempt_count += 1
        if client is not None:
            response = client.get(url)
            if hasattr(response, 'raise_for_status'):
                response.raise_for_status()
            return response.text
        return _fetch_text(url)

    try:
        text = retry_call(
            _call,
            attempts=attempts,
            base_sleep=base_sleep,
            retry_on=(Exception,),
            should_retry=is_transient_error,
        )
        return text, {'used_fallback': False, 'attempt_count': attempt_count, 'error': None}
    except Exception as exc:
        cached_text = str((previous or {}).get('text') or '')
        if cached_text:
            return cached_text, {'used_fallback': True, 'attempt_count': attempt_count, 'error': str(exc)}
        raise


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _openapi_summary(text: str) -> dict[str, Any]:
    try:
        obj = json.loads(text)
    except Exception:
        return {'path_count': 0, 'paths': [], 'methods': {}, 'schemas': {}, 'titles': {}}
    path_obj = obj.get('paths') or {}
    paths = sorted(path_obj.keys())
    methods: dict[str, list[str]] = {}
    for path, operations in path_obj.items():
        if not isinstance(operations, dict):
            continue
        methods[path] = sorted(
            key.lower()
            for key, value in operations.items()
            if key.lower() in {'get', 'post', 'put', 'patch', 'delete', 'options', 'head'} and isinstance(value, dict)
        )
    schemas_obj = (((obj.get('components') or {}).get('schemas')) or {})
    schemas: dict[str, dict[str, Any]] = {}
    for name, schema in schemas_obj.items():
        if not isinstance(schema, dict):
            continue
        properties = schema.get('properties') or {}
        required = schema.get('required') or []
        schemas[name] = {
            'type': schema.get('type'),
            'properties': sorted(properties.keys()) if isinstance(properties, dict) else [],
            'required': sorted(str(item) for item in required) if isinstance(required, list) else [],
        }
    summary = {
        'path_count': len(paths),
        'paths': paths,
        'methods': methods,
        'schemas': schemas,
        'titles': {
            'title': ((obj.get('info') or {}).get('title')),
            'version': ((obj.get('info') or {}).get('version')),
        },
    }
    return summary


def _llms_summary(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    interesting = [line for line in lines if '/api/' in line][:50]
    return {'line_count': len(lines), 'api_lines': interesting}


def _docs_summary(text: str) -> dict[str, Any]:
    lowered = text.lower()
    return {
        'contains_openapi_reference': '/openapi.json' in lowered,
        'contains_swagger': 'swagger' in lowered,
        'length': len(text),
    }


def _impact_hints(added: list[str], removed: list[str]) -> list[str]:
    hints: list[str] = []
    changed_paths = added + removed
    for path in changed_paths:
        for prefix, hint in MODULE_HINTS.items():
            if path.startswith(prefix):
                hints.append(f'{path} -> {hint}')
    return sorted(set(hints))


def _diff_openapi(prev: dict[str, Any], cur: dict[str, Any]) -> dict[str, Any]:
    prev_paths = set(prev.get('paths', []))
    cur_paths = set(cur.get('paths', []))
    added = sorted(cur_paths - prev_paths)
    removed = sorted(prev_paths - cur_paths)
    prev_methods = prev.get('methods', {}) or {}
    cur_methods = cur.get('methods', {}) or {}
    added_methods: dict[str, list[str]] = {}
    removed_methods: dict[str, list[str]] = {}
    for path in sorted(set(prev_methods) | set(cur_methods)):
        prev_set = set(prev_methods.get(path, []) or [])
        cur_set = set(cur_methods.get(path, []) or [])
        plus = sorted(cur_set - prev_set)
        minus = sorted(prev_set - cur_set)
        if plus:
            added_methods[path] = plus
        if minus:
            removed_methods[path] = minus

    prev_schemas = prev.get('schemas', {}) or {}
    cur_schemas = cur.get('schemas', {}) or {}
    added_schemas = sorted(set(cur_schemas) - set(prev_schemas))
    removed_schemas = sorted(set(prev_schemas) - set(cur_schemas))
    schema_changes: dict[str, dict[str, Any]] = {}
    for name in sorted(set(prev_schemas) & set(cur_schemas)):
        before = prev_schemas.get(name, {}) or {}
        after = cur_schemas.get(name, {}) or {}
        before_props = set(before.get('properties', []) or [])
        after_props = set(after.get('properties', []) or [])
        before_required = set(before.get('required', []) or [])
        after_required = set(after.get('required', []) or [])
        change = {
            'added_properties': sorted(after_props - before_props),
            'removed_properties': sorted(before_props - after_props),
            'added_required': sorted(after_required - before_required),
            'removed_required': sorted(before_required - after_required),
        }
        if any(change.values()) or before.get('type') != after.get('type'):
            change['type_before'] = before.get('type')
            change['type_after'] = after.get('type')
            schema_changes[name] = change
    return {
        'added_paths': added,
        'removed_paths': removed,
        'added_methods': added_methods,
        'removed_methods': removed_methods,
        'added_schemas': added_schemas,
        'removed_schemas': removed_schemas,
        'schema_changes': schema_changes,
        'path_count_before': prev.get('path_count', 0),
        'path_count_after': cur.get('path_count', 0),
        'impact_hints': _impact_hints(added, removed),
    }


def _source_summary(name: str, text: str) -> dict[str, Any]:
    if name == 'openapi':
        return _openapi_summary(text)
    if name == 'llms_full_local':
        return _llms_summary(text)
    return _docs_summary(text)


def run(settings: Settings, store: JsonStateStore) -> dict[str, Any]:
    log = logging.getLogger('official_watch')
    previous = store.load('official_watch', default={})
    current: dict[str, Any] = {'checked_at': utc_now().isoformat(), 'sources': {}, 'changed': [], 'diff_summary': {}}

    llms_local = Path.home() / 'agenthansa' / 'docs' / 'llms-full.txt'
    for name, url in SOURCES.items():
        try:
            prev_source = (((previous or {}).get('sources') or {}).get(name) or {})
            if name == 'llms_full_local':
                text = llms_local.read_text(encoding='utf-8') if llms_local.exists() else ''
                fetch_meta = {'used_fallback': False, 'attempt_count': 1, 'error': None}
            else:
                assert url is not None
                text, fetch_meta = _fetch_text_with_fallback(url, previous=prev_source)
            digest = _sha(text)
            prev_digest = prev_source.get('sha256')
            changed = bool(prev_digest and prev_digest != digest)
            summary = _source_summary(name, text)
            current['sources'][name] = {
                'sha256': digest,
                'length': len(text),
                'url': url or str(llms_local),
                'text': text,
                'summary': summary,
                'fetch_meta': fetch_meta,
            }
            if changed:
                current['changed'].append(name)
                if name == 'openapi':
                    current['diff_summary'][name] = _diff_openapi(prev_source.get('summary', {}), summary)
                else:
                    current['diff_summary'][name] = {
                        'changed': True,
                        'length_before': prev_source.get('length', 0),
                        'length_after': len(text),
                    }
        except Exception as exc:
            log.warning('official_source_failed source=%s error=%s', name, exc)
            current['sources'][name] = {'error': str(exc), 'url': url or str(llms_local)}

    store.save('official_watch', current)
    return current
