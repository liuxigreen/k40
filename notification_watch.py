from __future__ import annotations

import logging
from typing import Any

from client import AgentHansaClient
from state import JsonStateStore
from utils.timezone import utc_now


def run(client: AgentHansaClient, store: JsonStateStore, mark_read: bool = False) -> dict[str, Any]:
    log = logging.getLogger('notification_watch')
    data = client.get('/agents/notifications')
    result = {
        'checked_at': utc_now().isoformat(),
        'unread_count': data.get('unread_count', 0),
        'notifications': data.get('notifications', []),
    }
    store.save('notifications', result)
    if mark_read and result['unread_count']:
        try:
            client.post('/agents/notifications/read', json={})
            log.info('notifications_marked_read count=%s', result['unread_count'])
        except Exception as exc:
            log.warning('notifications_mark_read_failed error=%s', exc)
    return result
