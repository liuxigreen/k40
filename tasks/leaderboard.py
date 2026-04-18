from __future__ import annotations

import logging
from typing import Any

from client import AgentHansaClient
from state import JsonStateStore
from utils.timezone import utc_now


def run(client: AgentHansaClient, store: JsonStateStore) -> dict[str, Any]:
    log = logging.getLogger('tasks.leaderboard')
    data = {
        'daily_points': client.get('/agents/daily-points-leaderboard'),
        'alliance_daily': client.get('/agents/alliance-daily-leaderboard'),
        'alliance_total': client.get('/agents/alliance-leaderboard'),
        'weekly': client.get('/agents/leaderboard'),
    }
    result = {'checked_at': utc_now().isoformat(), 'data': data}
    store.save('leaderboards', result)
    leader = ((data['daily_points'] or {}).get('leaderboard') or [{}])[0]
    log.info('leaderboard refresh top_daily=%s top_points=%s', leader.get('name'), leader.get('today_points'))
    return result
