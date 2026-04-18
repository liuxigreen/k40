from __future__ import annotations

import logging
from typing import Any

from client import AgentHansaClient
from state import JsonStateStore
from utils.timezone import utc_now


def run(client: AgentHansaClient, store: JsonStateStore) -> dict[str, Any]:
    log = logging.getLogger('tasks.feed')
    data = client.get('/agents/feed')
    result = {'fetched_at': utc_now().isoformat(), 'data': data}
    store.save('feed', result)
    quest_count = len(data.get('quests', []) or [])
    urgent_count = len(data.get('urgent', []) or [])
    log.info('feed refresh urgent=%s quests=%s', urgent_count, quest_count)
    return result
