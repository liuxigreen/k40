from __future__ import annotations

import logging
from typing import Any

from client import AgentHansaClient
from state import JsonStateStore
from utils.timezone import minutes_until_pst_midnight, utc_now


def run(client: AgentHansaClient, store: JsonStateStore) -> dict[str, Any]:
    log = logging.getLogger('tasks.daily_xp')
    data = client.get('/agents/my-daily-xp')
    result = {
        'checked_at': utc_now().isoformat(),
        'minutes_until_pst_midnight': minutes_until_pst_midnight(),
        'data': data,
    }
    store.save('daily_xp', result)
    log.info('daily_xp today_points=%s alliance_rank=%s prize=%s', data.get('today_points'), data.get('alliance_rank'), data.get('prize_eligible'))
    return result
