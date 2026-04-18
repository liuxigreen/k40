from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from config import Settings
from client import AgentHansaClient
from state import JsonStateStore
from utils.timezone import pst_date_key, utc_now

TARGET_UPVOTES = 5
TARGET_DOWNVOTES = 5
FORUM_FETCH_LIMIT = 100


def _count_breakdown_events(breakdown: dict[str, Any], key_fragment: str) -> int:
    total = 0
    for key, value in (breakdown or {}).items():
        if key_fragment in str(key).lower():
            total += int((value or {}).get('events', 0) or 0)
    return total


def _parse_daily_quests_progress(data: dict[str, Any]) -> tuple[int, int] | None:
    quests = list((data or {}).get('quests', []) or [])
    for quest in quests:
        if str(quest.get('id') or '').lower() != 'curate':
            continue
        if bool(quest.get('completed')):
            return TARGET_UPVOTES, TARGET_DOWNVOTES
        progress = str(quest.get('progress') or '').strip()
        if not progress:
            return None
        match = re.search(r'(\d+)\s*/\s*5\s*up\s*,\s*(\d+)\s*/\s*5\s*down', progress, re.IGNORECASE)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def _derive_vote_counts(client: AgentHansaClient, breakdown: dict[str, Any], state: dict[str, Any]) -> tuple[int, int]:
    try:
        daily_quests = client.get('/agents/daily-quests')
        parsed = _parse_daily_quests_progress(daily_quests if isinstance(daily_quests, dict) else {})
        if parsed is not None:
            return parsed
    except Exception:
        pass

    up_events = _count_breakdown_events(breakdown, 'forum upvote')
    down_events = _count_breakdown_events(breakdown, 'forum downvote')
    state_up = int(state.get('current_up') or 0)
    state_down = int(state.get('current_down') or 0)
    if down_events == 0 and up_events > TARGET_UPVOTES and state_up > state_down:
        return 0, up_events
    return up_events, down_events


def run(settings: Settings, client: AgentHansaClient, store: JsonStateStore, *, dry_run: bool = False) -> dict[str, Any]:
    log = logging.getLogger('tasks.forum_curation')
    day_key = pst_date_key()
    state = store.load('forum_curation', default={})
    if state.get('day_key') != day_key:
        state = {'day_key': day_key, 'voted_post_ids': []}

    voted_post_ids = list(state.get('voted_post_ids', []) or [])
    daily_xp = store.load('daily_xp', default={}).get('data', {})
    breakdown = daily_xp.get('breakdown', {}) or {}
    up_events, down_events = _derive_vote_counts(client, breakdown, state)
    needed_up = max(0, TARGET_UPVOTES - up_events)
    needed_down = max(0, TARGET_DOWNVOTES - down_events)

    needed_total = needed_up + needed_down
    if len(voted_post_ids) > FORUM_FETCH_LIMIT - needed_total:
        voted_post_ids = voted_post_ids[-(FORUM_FETCH_LIMIT - needed_total):] if FORUM_FETCH_LIMIT > needed_total else []
        state['voted_post_ids'] = voted_post_ids

    result = {
        'checked_at': utc_now().isoformat(),
        'day_key': day_key,
        'target_up': TARGET_UPVOTES,
        'target_down': TARGET_DOWNVOTES,
        'current_up': up_events,
        'current_down': down_events,
        'needed_up': needed_up,
        'needed_down': needed_down,
        'executed_up': [],
        'executed_down': [],
        'dry_run': dry_run,
        'status': 'complete' if needed_up == 0 and needed_down == 0 else 'pending',
    }

    if needed_up == 0 and needed_down == 0:
        store.save('forum_curation', {**state, **result})
        log.info('forum_curation already complete up=%s down=%s', up_events, down_events)
        return result

    result['skipped_conflicts'] = []

    def _vote(post_id: str, direction: str, bucket: str) -> bool:
        try:
            if not dry_run:
                client.post(f'/forum/{post_id}/vote', json={'direction': direction})
            result[bucket].append(post_id)
            voted_post_ids.append(post_id)
            return True
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 409:
                result['skipped_conflicts'].append(post_id)
                voted_post_ids.append(post_id)
                log.info('forum_curation vote_conflict post_id=%s direction=%s', post_id, direction)
                return False
            raise

    remaining_up = needed_up
    remaining_down = needed_down
    for page in range(1, 6):
        if remaining_up == 0 and remaining_down == 0:
            break
        forum = client.get(f'/forum?sort=recent&limit={FORUM_FETCH_LIMIT}&page={page}')
        posts = forum.get('posts', []) or []
        if not posts:
            break
        candidates = [post for post in posts if str(post.get('id') or '') and str(post.get('id')) not in voted_post_ids]
        if not candidates:
            continue
        for post in candidates:
            post_id = str(post['id'])
            if remaining_up > 0:
                if _vote(post_id, 'up', 'executed_up'):
                    remaining_up -= 1
                continue
            if remaining_down > 0:
                if _vote(post_id, 'down', 'executed_down'):
                    remaining_down -= 1
            if remaining_up == 0 and remaining_down == 0:
                break

    completed_up = len(result['executed_up'])
    completed_down = len(result['executed_down'])
    result['status'] = 'complete' if completed_up >= needed_up and completed_down >= needed_down else 'partial'
    result['voted_post_ids'] = voted_post_ids
    store.save('forum_curation', {**state, **result})
    log.info(
        'forum_curation status=%s current_up=%s current_down=%s executed_up=%s executed_down=%s',
        result['status'],
        up_events,
        down_events,
        len(result['executed_up']),
        len(result['executed_down']),
    )
    return result
