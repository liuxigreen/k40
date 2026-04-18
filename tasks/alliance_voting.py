from __future__ import annotations

import logging
from typing import Any

from client import AgentHansaClient
from state import JsonStateStore
from tasks.quest_catalog_cache import load_quest_catalog
from utils.timezone import utc_now


def _score_submission(item: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    proof_url = item.get('proof_url') or item.get('proof')
    if proof_url:
        score += 20
        reasons.append('has_proof_url')
    content = str(item.get('content', '') or item.get('body', ''))
    if len(content) >= 280:
        score += 15
        reasons.append('content_len>=280')
    if item.get('human_verified') or item.get('verified'):
        score += 25
        reasons.append('human_verified')
    if any(token in content.lower() for token in ['placeholder', 'todo', 'coming soon']):
        score -= 50
        reasons.append('placeholder_risk')
    return score, reasons


def run(client: AgentHansaClient, store: JsonStateStore) -> dict[str, Any]:
    log = logging.getLogger('tasks.alliance_voting')
    quests = load_quest_catalog(client, store)
    items = quests.get('quests', []) if isinstance(quests, dict) else []
    suggestions = []
    for quest in items:
        if str(quest.get('status', '')).lower() != 'voting':
            continue
        quest_id = quest.get('id')
        try:
            submissions = client.get(f'/alliance-war/quests/{quest_id}/submissions')
        except Exception as exc:
            log.warning('voting_fetch_failed quest_id=%s error=%s', quest_id, exc)
            continue
        rows = submissions.get('submissions', submissions) if isinstance(submissions, dict) else submissions
        ranked = []
        for row in rows or []:
            score, reasons = _score_submission(row)
            ranked.append({'score': score, 'reasons': reasons, 'submission': row})
        ranked.sort(key=lambda x: x['score'], reverse=True)
        suggestions.append({'quest_id': quest_id, 'quest_title': quest.get('title'), 'ranked': ranked[:10]})
    result = {'checked_at': utc_now().isoformat(), 'suggestions': suggestions}
    store.save('alliance_voting', result)
    log.info('alliance voting suggestions=%s', len(suggestions))
    return result
