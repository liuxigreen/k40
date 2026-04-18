from __future__ import annotations

import logging
from typing import Any

import httpx

from client import AgentHansaClient
from config import Settings
from state import JsonStateStore
from utils.timezone import utc_now

STATE_KEY = 'publish_submission_execute'
REVISION_LIMIT_TEXT = 'Maximum 5 revisions per submission'


def _load_revision_limit_map(store: JsonStateStore) -> dict[str, dict[str, Any]]:
    raw = store.load('submission_revision_limits', default={})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for quest_id, value in raw.items():
        if isinstance(value, dict):
            result[str(quest_id)] = value
    return result


def _save_revision_limit(store: JsonStateStore, quest_id: str, note: str) -> None:
    data = _load_revision_limit_map(store)
    data[str(quest_id)] = {
        'revision_exhausted': True,
        'note': note,
        'updated_at': utc_now().isoformat(),
    }
    store.save('submission_revision_limits', data)


def _extract_draft_content(item: dict[str, Any]) -> str | None:
    draft_path = item.get('draft_path')
    if not draft_path:
        return None
    try:
        text = open(draft_path, 'r', encoding='utf-8').read()
    except Exception:
        return None
    marker = '## Draft content'
    if marker in text:
        _, _, tail = text.partition(marker)
        content = tail.strip()
        if content:
            return content
    stripped = text.strip()
    return stripped or None


def _submit_item(client: AgentHansaClient, item: dict[str, Any]) -> dict[str, Any]:
    quest_id = str(item.get('quest_id') or '').strip()
    if not quest_id:
        return {'status': 'skipped', 'reason': 'missing_quest_id', 'quest_id': quest_id}
    content = str(item.get('submission_content') or '').strip() or (_extract_draft_content(item) or '')
    if not content:
        return {'status': 'skipped', 'reason': 'missing_submission_content', 'quest_id': quest_id}
    payload: dict[str, Any] = {'content': content}
    proof_url = str(item.get('proof_url') or '').strip()
    if proof_url:
        payload['proof_url'] = proof_url
    response = client.post(f'/alliance-war/quests/{quest_id}/submit', json=payload)
    if isinstance(response, dict):
        return {
            'status': 'submitted',
            'quest_id': quest_id,
            'submission_id': response.get('submission_id'),
            'updated': bool(response.get('updated')),
            'revision': response.get('revision'),
            'revisions_remaining': response.get('revisions_remaining'),
            'message': response.get('message'),
            'proof_url': proof_url or None,
        }
    return {
        'status': 'submitted',
        'quest_id': quest_id,
        'response': response,
        'proof_url': proof_url or None,
    }


def run(settings: Settings, client: AgentHansaClient, store: JsonStateStore, *, dry_run: bool = False) -> dict[str, Any]:
    log = logging.getLogger('tasks.publish_submission_execute')
    bridge = store.load('publish_submit_bridge', default={})
    queue = store.load('publish_queue', default={})
    queue_items = list(queue.get('items', []) or [])
    queue_by_id = {str(item.get('queue_id') or ''): item for item in queue_items if item.get('queue_id')}
    revision_limits = _load_revision_limit_map(store)

    results: list[dict[str, Any]] = []
    submitted_count = 0
    skipped_revision_limit = 0

    for ready in list(bridge.get('items', []) or []):
        if str(ready.get('status') or '') != 'submission_ready':
            continue
        queue_id = str(ready.get('queue_id') or '')
        queue_item = queue_by_id.get(queue_id)
        quest_id = str((queue_item or {}).get('quest_id') or ready.get('quest_id') or '').strip()
        if quest_id and bool((revision_limits.get(quest_id) or {}).get('revision_exhausted')):
            result = {
                'status': 'filtered_revision_limit',
                'quest_id': quest_id,
                'queue_id': queue_id,
                'note': (revision_limits.get(quest_id) or {}).get('note'),
            }
            results.append(result)
            skipped_revision_limit += 1
            if queue_item is not None:
                queue_item['status'] = 'revision_exhausted'
                queue_item.setdefault('notes', []).append(str(result.get('note') or 'revision limit reached'))
            continue

        merged = {**ready, **(queue_item or {})}
        if dry_run:
            results.append({
                'status': 'dry_run',
                'quest_id': quest_id,
                'queue_id': queue_id,
                'proof_url': merged.get('proof_url'),
            })
            continue
        try:
            result = _submit_item(client, merged)
            results.append(result)
            if result.get('status') == 'submitted':
                submitted_count += 1
                if queue_item is not None:
                    queue_item['status'] = 'submitted'
                    queue_item['submission_id'] = result.get('submission_id')
                    queue_item['updated'] = result.get('updated')
                    queue_item['revision'] = result.get('revision')
                    queue_item['revisions_remaining'] = result.get('revisions_remaining')
                    queue_item['submitted_at'] = utc_now().isoformat()
                    queue_item.setdefault('notes', []).append(str(result.get('message') or 'submitted'))
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            body = exc.response.text if exc.response is not None else str(exc)
            if status_code == 429 and REVISION_LIMIT_TEXT.lower() in body.lower() and quest_id:
                _save_revision_limit(store, quest_id, body.strip())
                result = {
                    'status': 'revision_limit',
                    'quest_id': quest_id,
                    'queue_id': queue_id,
                    'status_code': status_code,
                    'note': body.strip(),
                }
                results.append(result)
                skipped_revision_limit += 1
                if queue_item is not None:
                    queue_item['status'] = 'revision_exhausted'
                    queue_item.setdefault('notes', []).append(body.strip())
                continue
            result = {
                'status': 'error',
                'quest_id': quest_id,
                'queue_id': queue_id,
                'status_code': status_code,
                'error': body.strip(),
            }
            results.append(result)
            if queue_item is not None:
                queue_item['status'] = 'submit_error'
                queue_item.setdefault('notes', []).append(body.strip())
            log.warning('publish_submission_execute_failed quest_id=%s status=%s error=%s', quest_id, status_code, body.strip())

    queue['items'] = queue_items
    store.save('publish_queue', queue)
    result = {
        'generated_at': utc_now().isoformat(),
        'items': results,
        'summary': {
            'submitted': submitted_count,
            'revision_filtered': skipped_revision_limit,
            'dry_run': dry_run,
        },
    }
    store.save(STATE_KEY, result)
    log.info('publish_submission_execute submitted=%s revision_filtered=%s dry_run=%s', submitted_count, skipped_revision_limit, dry_run)
    return result
