from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from config import Settings
from state import JsonStateStore
from utils.timezone import utc_now

STATE_KEY = 'publish_submit_bridge'


def _upload_paste_rs(draft_path: str | None) -> str | None:
    if not draft_path:
        return None
    path = Path(draft_path)
    if not path.exists():
        return None
    content = path.read_text(encoding='utf-8').strip()
    if not content:
        return None
    with httpx.Client(timeout=20, headers={'User-Agent': 'agenthansa_bot/1.0 (+termux)'}) as client:
        response = client.post('https://paste.rs', content=content, headers={'Content-Type': 'text/plain; charset=utf-8'})
        response.raise_for_status()
        text = response.text.strip()
        return text or None


def run(settings: Settings, store: JsonStateStore) -> dict[str, Any]:
    log = logging.getLogger('tasks.publish_submit_bridge')
    queue = store.load('publish_queue', default={})
    queue_items = list(queue.get('items', []) or [])
    items = []
    for item in queue_items:
        status = str(item.get('status') or '')
        published_url = item.get('published_url')
        publish_required = bool(item.get('publish_required'))
        proof_strategy = str(item.get('proof_strategy') or 'none')
        proof_url = item.get('proof_url') or published_url

        if not publish_required and proof_strategy == 'paste_rs_or_doc' and not proof_url:
            try:
                proof_url = _upload_paste_rs(item.get('draft_path'))
                if proof_url:
                    item['proof_url'] = proof_url
                    item['status'] = 'proof_hosted'
                    status = 'proof_hosted'
            except Exception as exc:
                item['status'] = 'waiting_for_publish'
                status = 'waiting_for_publish'
                item.setdefault('notes', []).append(f'paste_rs_upload_failed: {exc}')
                log.warning('paste_rs_upload_failed queue_id=%s error=%s', item.get('queue_id'), exc)

        if publish_required:
            if status != 'published':
                continue
            if not proof_url:
                continue
        else:
            if proof_strategy == 'paste_rs_or_doc':
                if not proof_url:
                    items.append({
                        'queue_id': item.get('queue_id'),
                        'quest_id': item.get('quest_id'),
                        'title': item.get('title'),
                        'platform': item.get('platform'),
                        'status': 'waiting_for_publish',
                        'proof_url': None,
                        'publish_required': publish_required,
                        'published_url': published_url,
                    })
                    continue
            else:
                continue

        items.append({
            'queue_id': item.get('queue_id'),
            'quest_id': item.get('quest_id'),
            'title': item.get('title'),
            'platform': item.get('platform'),
            'status': 'submission_ready',
            'proof_url': proof_url,
            'publish_required': publish_required,
            'published_url': published_url,
        })

    result = {
        'generated_at': utc_now().isoformat(),
        'items': items,
        'summary': {
            'submission_ready': sum(1 for item in items if item.get('status') == 'submission_ready'),
            'waiting_for_publish': sum(1 for item in items if item.get('status') == 'waiting_for_publish'),
        },
    }
    store.save('publish_queue', {**queue, 'items': queue_items})
    store.save(STATE_KEY, result)
    log.info('publish_submit_bridge submission_ready=%s waiting=%s', result['summary']['submission_ready'], result['summary']['waiting_for_publish'])
    return result
