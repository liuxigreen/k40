from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from client import AgentHansaClient
from state import JsonStateStore
from tasks.quest_catalog_cache import load_quest_catalog
from utils.timezone import utc_now


def _safe_get_my(client: AgentHansaClient) -> tuple[str, Any]:
    try:
        data = client.get('/alliance-war/quests/my')
        return 'direct', data
    except httpx.HTTPStatusError as exc:
        # Docs/openapi say this exists, but live host has returned 422 with quest_id parsing.
        if exc.response is not None and exc.response.status_code == 422:
            return 'broken_422', None
        raise


def _normalize_direct_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        rows = data.get('submissions', data.get('rows', []))
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _fallback_from_journey(client: AgentHansaClient) -> list[dict[str, Any]]:
    journey = client.get('/agents/journey')
    timeline = journey.get('timeline', []) or []
    rows: list[dict[str, Any]] = []
    for item in timeline:
        if item.get('event') != 'quest_submission':
            continue
        rows.append({
            'event': item.get('event'),
            'type': item.get('type'),
            'detail': item.get('detail'),
            'amount': item.get('amount'),
            'timestamp': item.get('timestamp'),
            'status': 'observed_in_journey',
            'risk_flags': [],
        })
    return rows


def _quest_title_from_row(row: dict[str, Any]) -> str:
    for key in ('quest_title', 'title', 'detail', 'content'):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _quest_catalog(client: AgentHansaClient, store: JsonStateStore) -> list[dict[str, Any]]:
    data = load_quest_catalog(client, store)
    items = data.get('quests', data.get('rows', [])) if isinstance(data, dict) else []
    if isinstance(items, list):
        return [x for x in items if isinstance(x, dict)]
    return []


def _find_matching_quest(title: str, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    lowered = title.lower().strip()
    if not lowered:
        return None
    exact = None
    fuzzy = None
    for quest in catalog:
        quest_title = str(quest.get('title') or '').strip()
        if not quest_title:
            continue
        ql = quest_title.lower()
        if ql == lowered:
            exact = quest
            break
        if lowered in ql or ql in lowered:
            fuzzy = quest
    return exact or fuzzy


def _collect_detail(client: AgentHansaClient, quest_id: str | None) -> dict[str, Any] | None:
    if not quest_id:
        return None
    try:
        data = client.get(f'/alliance-war/quests/{quest_id}')
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _collect_detail_cached(
    client: AgentHansaClient,
    quest_id: str | None,
    cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    cache_key = str(quest_id or '')
    if cache_key not in cache:
        cache[cache_key] = _collect_detail(client, quest_id)
    return cache[cache_key]


def _load_agent_identity(client: AgentHansaClient) -> tuple[str | None, str | None]:
    try:
        data = client.get('/agents/me')
    except Exception:
        return None, None
    if not isinstance(data, dict):
        return None, None
    agent_name = str(data.get('name') or '').strip() or None
    agent_id = str(data.get('id') or '').strip() or None
    return agent_name, agent_id


def _collect_submission_rows(client: AgentHansaClient, quest_id: str | None) -> list[dict[str, Any]]:
    if not quest_id:
        return []
    try:
        data = client.get(f'/alliance-war/quests/{quest_id}/submissions')
    except Exception:
        return []
    if isinstance(data, dict):
        rows = data.get('submissions', data.get('rows', []))
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _match_my_submission(
    submission_rows: list[dict[str, Any]],
    *,
    agent_name: str | None,
    agent_id: str | None,
) -> dict[str, Any] | None:
    normalized_name = str(agent_name or '').strip().lower()
    normalized_id = str(agent_id or '').strip()
    for row in submission_rows:
        row_agent_id = str(row.get('agent_id') or '').strip()
        row_agent_name = str(row.get('agent_name') or '').strip().lower()
        if normalized_id and row_agent_id and row_agent_id == normalized_id:
            return row
        if normalized_name and row_agent_name and row_agent_name == normalized_name:
            return row
    return None


def _load_revision_limit_map(store: JsonStateStore) -> dict[str, dict[str, Any]]:
    raw = store.load('submission_revision_limits', default={})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for quest_id, value in raw.items():
        if isinstance(value, dict):
            result[str(quest_id)] = value
    return result


def _revision_exhausted(revision_limits: dict[str, dict[str, Any]], quest_id: str | None) -> bool:
    if not quest_id:
        return False
    item = revision_limits.get(str(quest_id)) or {}
    if not isinstance(item, dict):
        return False
    return bool(item.get('revision_exhausted'))


def _revision_note(revision_limits: dict[str, dict[str, Any]], quest_id: str | None) -> str | None:
    if not quest_id:
        return None
    item = revision_limits.get(str(quest_id)) or {}
    if not isinstance(item, dict):
        return None
    note = str(item.get('note') or '').strip()
    return note or None


def _infer_status(row: dict[str, Any], detail: dict[str, Any] | None) -> str:
    for key in ('status', 'phase'):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    if detail:
        for key in ('status', 'phase'):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    return 'unknown'


def _infer_amounts(row: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
    return {
        'submission_amount': row.get('amount') or row.get('earned') or row.get('payout'),
        'quest_reward': (detail or {}).get('reward') or (detail or {}).get('reward_amount'),
    }


def _build_risk_flags(row: dict[str, Any], detail: dict[str, Any] | None) -> list[str]:
    text_parts = [
        str(row.get('quest_title') or row.get('title') or row.get('detail') or ''),
        str(row.get('ai_summary') or ''),
        str(row.get('message') or ''),
    ]
    text = ' '.join(text_parts).lower()
    flags: list[str] = []
    if bool(row.get('spam_flagged') or row.get('is_spam')):
        flags.append('spam')
    for needle in ['reject', 'rejected', 'excluded', 'low quality', 'no payout', 'zero payout']:
        if needle in text:
            flags.append(needle.replace(' ', '_'))
    grade = str(row.get('ai_grade') or '').strip().upper()
    if grade in {'C', 'D', 'E', 'F'}:
        flags.append('low_grade')
    status = _infer_status(row, detail)
    if status in {'judging', 'voting'}:
        flags.append('pending_outcome')
    if status == 'settled' and not (row.get('amount') or row.get('earned') or row.get('payout')):
        flags.append('settled_without_visible_payout')
    title = _quest_title_from_row(row).lower()
    if any(token in title for token in ['tweet', 'twitter', 'x/', 'x post', 'youtube', 'video']) and not row.get('proof_url'):
        flags.append('proof_likely_needed')
    if bool(row.get('revision_exhausted')):
        flags.append('revision_exhausted')
    return sorted(set(flags))


def _recommended_action(flags: list[str], status: str, detail: dict[str, Any] | None) -> str:
    if 'proof_likely_needed' in flags:
        return 'manual_check_proof_url_requirement'
    if any(flag in flags for flag in ['spam', 'rejected', 'excluded', 'low_quality']):
        return 'review_failure_reason_and_do_not_resubmit_blindly'
    if status == 'open':
        return 'can_still_improve_submission_if_allowed'
    if status == 'voting':
        return 'monitor_alliance_voting_and_human_verified_status'
    if status == 'judging':
        return 'wait_for_merchant_decision'
    if status == 'settled':
        return 'record_final_outcome'
    return 'monitor'


def _enrich_rows(client: AgentHansaClient, store: JsonStateStore, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog = _quest_catalog(client, store)
    enriched: list[dict[str, Any]] = []
    detail_cache: dict[str, dict[str, Any] | None] = {}
    submissions_cache: dict[str, list[dict[str, Any]]] = {}
    revision_limits = _load_revision_limit_map(store)
    agent_name, agent_id = _load_agent_identity(client)
    for row in rows:
        title = _quest_title_from_row(row)
        quest = _find_matching_quest(title, catalog)
        quest_id = None
        if quest:
            quest_id = quest.get('id')
        elif isinstance(row.get('quest_id'), str):
            quest_id = row.get('quest_id')
        detail = _collect_detail_cached(client, quest_id, detail_cache)
        cache_key = str(quest_id or '')
        if cache_key not in submissions_cache:
            submissions_cache[cache_key] = _collect_submission_rows(client, quest_id)
        my_submission = _match_my_submission(
            submissions_cache.get(cache_key, []),
            agent_name=agent_name,
            agent_id=agent_id,
        ) or {}
        merged_row = {
            **row,
            'submission_id': my_submission.get('id') or my_submission.get('submission_id') or row.get('submission_id') or row.get('id'),
            'agent_name': my_submission.get('agent_name') or row.get('agent_name'),
            'proof_url': my_submission.get('proof_url') or row.get('proof_url'),
            'content': my_submission.get('content') or row.get('content'),
            'ai_grade': my_submission.get('ai_grade') if my_submission.get('ai_grade') is not None else row.get('ai_grade'),
            'ai_summary': my_submission.get('ai_summary') if my_submission.get('ai_summary') is not None else row.get('ai_summary'),
            'is_spam': bool(my_submission.get('is_spam')) if my_submission else bool(row.get('is_spam')),
            'spam_flagged': bool(my_submission.get('is_spam')) if my_submission else bool(row.get('spam_flagged') or row.get('is_spam')),
            'upvotes': my_submission.get('upvotes') if my_submission else row.get('upvotes'),
            'downvotes': my_submission.get('downvotes') if my_submission else row.get('downvotes'),
            'score': my_submission.get('score') if my_submission else row.get('score'),
            'human_verified': my_submission.get('human_verified') if my_submission else row.get('human_verified'),
            'revision_exhausted': _revision_exhausted(revision_limits, quest_id),
            'revision_note': _revision_note(revision_limits, quest_id),
        }
        status = _infer_status(merged_row, detail)
        risk_flags = _build_risk_flags(merged_row, detail)
        enriched_row = {
            **merged_row,
            'quest_title': title,
            'quest_id': quest_id,
            'status': status,
            'quest_detail': {
                'deadline': (detail or {}).get('deadline'),
                'reward': (detail or {}).get('reward') or (detail or {}).get('reward_amount'),
                'status': (detail or {}).get('status'),
                'require_proof': (detail or {}).get('require_proof'),
                'total_submissions': (detail or {}).get('total_submissions'),
            },
            'amounts': _infer_amounts(merged_row, detail),
            'risk_flags': risk_flags,
            'recommended_action': _recommended_action(risk_flags, status, detail),
        }
        enriched.append(enriched_row)
    return enriched


def _risk_scan(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get('risk_flags')]


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get('status') or 'unknown')
        by_status[status] = by_status.get(status, 0) + 1
        action = str(row.get('recommended_action') or 'monitor')
        action_counts[action] = action_counts.get(action, 0) + 1
    return {'by_status': by_status, 'recommended_actions': action_counts}


def run(client: AgentHansaClient, store: JsonStateStore) -> dict[str, Any]:
    log = logging.getLogger('tasks.my_submissions')
    mode, data = _safe_get_my(client)
    if mode == 'direct' and data is not None:
        rows = _normalize_direct_rows(data)
    else:
        rows = _fallback_from_journey(client)
        mode = 'fallback_journey'

    enriched = _enrich_rows(client, store, rows)
    risky = _risk_scan(enriched)
    result = {
        'checked_at': utc_now().isoformat(),
        'mode': mode,
        'count': len(enriched),
        'risky_count': len(risky),
        'summary': _summary(enriched),
        'submissions': enriched,
        'risky_submissions': risky,
    }
    store.save('my_submissions', result)
    log.info('submissions_refresh mode=%s count=%s risks=%s', mode, len(enriched), len(risky))
    return result
