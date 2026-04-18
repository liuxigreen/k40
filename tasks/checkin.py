from __future__ import annotations

import logging
from typing import Any

from client import AgentHansaClient
from state import JsonStateStore
from utils.timezone import pst_date_key, utc_now


def run(client: AgentHansaClient, store: JsonStateStore, dry_run: bool = False) -> dict[str, Any]:
    log = logging.getLogger('tasks.checkin')
    state = store.load('runtime_state', default={})
    today = pst_date_key()
    if state.get('last_checkin_pst_date') == today:
        result = {'status': 'skipped', 'reason': 'already_checked_in_today', 'pst_date': today}
        log.info('checkin skipped reason=already_checked_in_today')
        return result

    if dry_run:
        return {'status': 'dry_run', 'would_call': 'POST /agents/checkin'}

    data = client.post('/agents/checkin', json={})
    state['last_checkin_pst_date'] = today
    state['last_checkin_at'] = utc_now().isoformat()
    store.save('runtime_state', state)
    result = {'status': 'ok', 'response': data}
    log.info('checkin success')
    return result
