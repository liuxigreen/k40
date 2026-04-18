from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from state import JsonStateStore
from utils.timezone import pst_date_key, utc_now

STATE_KEY = 'submission_strategy'
DAILY_SPAM_THRESHOLD = 1
GLOBAL_PAUSE_HOURS_AFTER_SPAM = 24
QUEST_TYPE_PAUSE_HOURS_AFTER_SPAM = 72
ACTIVE_BAN_EXTRA_COOLDOWN_HOURS = 12


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=utc_now().tzinfo)
    return parsed


def _now(now_iso: str | None = None) -> datetime:
    parsed = _parse_iso(now_iso)
    return parsed or utc_now()


def default_strategy_state() -> dict[str, Any]:
    return {
        'global_pause_until': None,
        'quest_type_pause_until': {},
        'daily': {},
        'history': [],
    }


def load_strategy_state(store: JsonStateStore) -> dict[str, Any]:
    state = store.load(STATE_KEY, default={})
    if not isinstance(state, dict):
        state = {}
    merged = default_strategy_state()
    merged.update(state)
    merged['quest_type_pause_until'] = dict(merged.get('quest_type_pause_until') or {})
    merged['daily'] = dict(merged.get('daily') or {})
    merged['history'] = list(merged.get('history') or [])
    return merged


def save_strategy_state(store: JsonStateStore, state: dict[str, Any]) -> None:
    store.save(STATE_KEY, state)


def can_submit_now(
    store: JsonStateStore,
    *,
    quest_type: str | None = None,
    now_iso: str | None = None,
) -> tuple[bool, str | None]:
    state = load_strategy_state(store)
    now = _now(now_iso)
    global_pause_until = _parse_iso(state.get('global_pause_until'))
    if global_pause_until and now < global_pause_until:
        return False, 'global_pause_active'
    if quest_type:
        quest_pause_until = _parse_iso((state.get('quest_type_pause_until') or {}).get(quest_type))
        if quest_pause_until and now < quest_pause_until:
            return False, 'quest_type_pause_active'
    day_key = pst_date_key(now)
    daily = (state.get('daily') or {}).get(day_key, {}) or {}
    if int(daily.get('spam_count') or 0) >= DAILY_SPAM_THRESHOLD:
        return False, 'daily_spam_threshold_hit'
    return True, None


def normalize_submission_feedback(row: dict[str, Any], *, quest_type: str) -> dict[str, Any]:
    return {
        'quest_id': row.get('quest_id') or row.get('id'),
        'quest_title': row.get('quest_title') or row.get('title'),
        'quest_type': quest_type,
        'submission_id': row.get('submission_id') or row.get('id'),
        'created_at': row.get('created_at') or utc_now().isoformat(),
        'ai_grade': row.get('ai_grade'),
        'ai_summary': row.get('ai_summary'),
        'spam_flagged': bool(row.get('spam_flagged') or row.get('is_spam')),
        'message': row.get('message'),
    }


def record_submission_feedback(
    store: JsonStateStore,
    feedback: dict[str, Any],
    *,
    now_iso: str | None = None,
) -> dict[str, Any]:
    state = load_strategy_state(store)
    now = _now(now_iso)
    day_key = pst_date_key(now)
    daily = dict((state.get('daily') or {}).get(day_key) or {'submission_count': 0, 'spam_count': 0})
    daily['submission_count'] = int(daily.get('submission_count') or 0) + 1
    if feedback.get('spam_flagged'):
        daily['spam_count'] = int(daily.get('spam_count') or 0) + 1
        global_pause_until = now + timedelta(hours=GLOBAL_PAUSE_HOURS_AFTER_SPAM)
        state['global_pause_until'] = global_pause_until.isoformat()
        quest_type = str(feedback.get('quest_type') or '').strip()
        if quest_type:
            quest_pause = now + timedelta(hours=QUEST_TYPE_PAUSE_HOURS_AFTER_SPAM)
            quest_pauses = dict(state.get('quest_type_pause_until') or {})
            quest_pauses[quest_type] = quest_pause.isoformat()
            state['quest_type_pause_until'] = quest_pauses
    state['daily'][day_key] = daily
    history = list(state.get('history') or [])
    history.append(feedback)
    state['history'] = history[-100:]
    save_strategy_state(store, state)
    store.append_jsonl('submission_feedback', feedback)
    return state


def apply_active_ban_cooldown(store: JsonStateStore, metadata: dict[str, Any], *, now_iso: str | None = None) -> dict[str, Any]:
    state = load_strategy_state(store)
    now = _now(now_iso)
    ban_level = int((metadata or {}).get('spam_ban_level') or 0)
    ban_minutes = int((metadata or {}).get('spam_ban_minutes') or 0)
    ban_date = _parse_iso(str((metadata or {}).get('spam_ban_date') or ''))
    if ban_level > 0 and ban_minutes > 0 and ban_date is not None:
        unban_at = ban_date + timedelta(minutes=ban_minutes)
        if now < unban_at:
            state['global_pause_until'] = (unban_at + timedelta(hours=ACTIVE_BAN_EXTRA_COOLDOWN_HOURS)).isoformat()
            save_strategy_state(store, state)
    return state
