from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from config import Settings
from state import JsonStateStore
from tasks.submission_strategy import load_strategy_state
from utils.timezone import minutes_until_pst_midnight, utc_now


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, value))


def _add(actions: list[dict[str, Any]], *, priority: int, action_type: str, reason: str, payload: dict[str, Any] | None = None) -> None:
    actions.append({
        'priority': _clamp(priority),
        'type': action_type,
        'reason': reason,
        'payload': payload or {},
    })


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def normalize_prize_eligible(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value > 0

    text = str(value).strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered in {'false', 'no', 'off', 'none', 'null', 'n'}:
        return False
    if lowered in {'true', 'yes', 'on', 'y'}:
        return True
    match = re.search(r'-?\d+(?:\.\d+)?', text.replace(',', ''))
    if match:
        try:
            return float(match.group(0)) > 0
        except ValueError:
            return False
    return bool(text)


def run(settings: Settings, store: JsonStateStore, *, minutes_until_snapshot: int | None = None) -> dict[str, Any]:
    log = logging.getLogger('tasks.decision_engine')
    snapshot_minutes = minutes_until_pst_midnight() if minutes_until_snapshot is None else minutes_until_snapshot
    snapshot_guard_active = snapshot_minutes <= settings.snapshot_guard_minutes

    daily_xp = store.load('daily_xp', default={}).get('data', {})
    redpacket = store.load('redpacket_state', default={})
    quests = store.load('quests', default={})
    submissions = store.load('my_submissions', default={})
    submission_strategy = load_strategy_state(store)
    forum_strategy = store.load('forum_strategy', default={})
    official_watch = store.load('official_watch', default={})
    notifications = store.load('notifications', default={})

    today_points = int(daily_xp.get('today_points') or 0)
    alliance_rank = int(daily_xp.get('alliance_rank') or 9999)
    prize_eligible = normalize_prize_eligible(daily_xp.get('prize_eligible'))
    forum_points = int(forum_strategy.get('forum_points') or 0)
    unread_notifications = int(notifications.get('unread_count') or 0)
    breakdown = daily_xp.get('breakdown', {}) or {}
    forum_upvotes = sum(int((value or {}).get('events', 0) or 0) for key, value in breakdown.items() if 'forum upvote' in str(key).lower())
    forum_downvotes = sum(int((value or {}).get('events', 0) or 0) for key, value in breakdown.items() if 'forum downvote' in str(key).lower())

    auto_quests = (quests.get('buckets', {}) or {}).get('auto_candidate', [])
    manual_quests = (quests.get('buckets', {}) or {}).get('manual_or_proof_required', [])
    risky_submissions = submissions.get('risky_submissions', []) or []
    publish_queue = store.load('publish_queue', default={})
    publish_bridge = store.load('publish_submit_bridge', default={})

    actions: list[dict[str, Any]] = []

    redpacket_status = str(redpacket.get('status') or '').lower()
    packet = redpacket.get('packet') or {}
    overview = redpacket.get('overview') or {}
    active_packets = overview.get('active') or []
    next_packet_at = _parse_iso(overview.get('next_packet_at'))
    is_future_window = bool(next_packet_at and next_packet_at > utc_now())
    packet_is_live = bool(active_packets) or bool(packet and is_future_window and redpacket_status in {'joined', 'manual_required', 'unsupported', 'active', 'dry_run', 'already_joined'})
    if packet_is_live and redpacket_status in {'manual_required', 'unsupported', 'active'}:
        priority = 98 if snapshot_guard_active else 94
        _add(
            actions,
            priority=priority,
            action_type='red_packet_manual_intervention',
            reason=redpacket.get('reason') or redpacket_status or 'red_packet_window_open',
            payload={
                'packet_id': packet.get('id'),
                'title': packet.get('title'),
                'next_packet_at': overview.get('next_packet_at'),
            },
        )
    elif overview.get('next_packet_at'):
        _add(
            actions,
            priority=70 if snapshot_guard_active else 55,
            action_type='red_packet_watch',
            reason='next_red_packet_known',
            payload={'next_packet_at': overview.get('next_packet_at')},
        )

    pause_until = _parse_iso(submission_strategy.get('global_pause_until'))
    if risky_submissions:
        _add(
            actions,
            priority=90 if snapshot_guard_active else 82,
            action_type='submission_risk_review',
            reason='risky_submissions_present',
            payload={'count': len(risky_submissions), 'top': risky_submissions[:3]},
        )

    if pause_until and utc_now() < pause_until:
        _add(
            actions,
            priority=95,
            action_type='submission_pause',
            reason='spam_cooldown_active',
            payload={'pause_until': submission_strategy.get('global_pause_until')},
        )

    if auto_quests:
        top = auto_quests[0]
        score = int(top.get('_priority_score') or 0)
        _add(
            actions,
            priority=65 + min(20, score // 10),
            action_type='quest_auto_execution',
            reason='high_value_auto_candidate_available',
            payload={'quest_id': top.get('id'), 'title': top.get('title'), 'score': score},
        )

    queued_publish_items = list(publish_queue.get('items', []) or [])
    submission_ready_items = list(publish_bridge.get('items', []) or [])
    if queued_publish_items:
        top_publish = queued_publish_items[0]
        _add(
            actions,
            priority=74 if top_publish.get('publish_required') else 62,
            action_type='publish_pipeline',
            reason='publishable_external_content_available',
            payload={
                'queue_id': top_publish.get('queue_id'),
                'quest_id': top_publish.get('quest_id'),
                'platform': top_publish.get('platform'),
                'status': top_publish.get('status'),
            },
        )
    if submission_ready_items:
        top_ready = submission_ready_items[0]
        _add(
            actions,
            priority=78,
            action_type='publish_submission_ready',
            reason='published_content_ready_for_agenthansa_submission',
            payload={
                'queue_id': top_ready.get('queue_id'),
                'quest_id': top_ready.get('quest_id'),
                'platform': top_ready.get('platform'),
                'proof_url': top_ready.get('proof_url'),
            },
        )

    if manual_quests:
        top = manual_quests[0]
        score = int(top.get('_priority_score') or 0)
        _add(
            actions,
            priority=60 + min(20, score // 10),
            action_type='quest_manual_review',
            reason='proof_or_manual_quest_available',
            payload={'quest_id': top.get('id'), 'title': top.get('title'), 'score': score},
        )

    if official_watch.get('changed'):
        _add(
            actions,
            priority=76,
            action_type='official_watch_review',
            reason='official_sources_changed',
            payload={'changed_sources': official_watch.get('changed', []), 'diff_summary': official_watch.get('diff_summary', {})},
        )

    if unread_notifications:
        _add(
            actions,
            priority=58 if unread_notifications < 5 else 72,
            action_type='notification_review',
            reason='unread_notifications_present',
            payload={'unread_count': unread_notifications},
        )

    if forum_upvotes < 5 or forum_downvotes < 5:
        _add(
            actions,
            priority=84 if snapshot_guard_active else 66,
            action_type='forum_curate',
            reason='daily_forum_vote_target_incomplete',
            payload={
                'forum_upvotes': forum_upvotes,
                'forum_downvotes': forum_downvotes,
                'remaining_up': max(0, 5 - forum_upvotes),
                'remaining_down': max(0, 5 - forum_downvotes),
            },
        )

    for item in forum_strategy.get('manual_actions', []) or []:
        action_type = str(item.get('type') or '')
        if action_type == 'stop_forum_push' or forum_points >= settings.forum_xp_soft_cap:
            _add(
                actions,
                priority=25,
                action_type='forum_hold',
                reason=item.get('reason') or 'forum_xp_near_or_above_soft_cap',
                payload={'forum_points': forum_points},
            )
            continue
        _add(
            actions,
            priority=52 if forum_points < settings.forum_xp_soft_cap else 35,
            action_type='forum_manual_action',
            reason=item.get('reason') or action_type or 'forum_action',
            payload=item,
        )

    if snapshot_guard_active:
        _add(
            actions,
            priority=88,
            action_type='snapshot_guard_focus',
            reason='approaching_pst_snapshot',
            payload={'minutes_until_snapshot': snapshot_minutes},
        )

    if not prize_eligible or alliance_rank > 10 or today_points < 150:
        _add(
            actions,
            priority=68 if snapshot_guard_active else 50,
            action_type='xp_push',
            reason='daily_xp_or_rank_below_target',
            payload={
                'today_points': today_points,
                'alliance_rank': alliance_rank,
                'prize_eligible': prize_eligible,
            },
        )

    actions.sort(key=lambda item: (-int(item.get('priority') or 0), str(item.get('type') or '')))
    plan = {
        'generated_at': utc_now().isoformat(),
        'minutes_until_snapshot': snapshot_minutes,
        'snapshot_guard_active': snapshot_guard_active,
        'inputs': {
            'today_points': today_points,
            'alliance_rank': alliance_rank,
            'prize_eligible': prize_eligible,
            'forum_points': forum_points,
            'unread_notifications': unread_notifications,
            'auto_quest_count': len(auto_quests),
            'manual_quest_count': len(manual_quests),
            'risky_submission_count': len(risky_submissions),
            'official_changes': official_watch.get('changed', []),
        },
        'actions': actions,
        'summary': {
            'action_count': len(actions),
            'highest_priority_type': actions[0]['type'] if actions else None,
        },
    }
    store.save('decision_plan', plan)
    log.info('decision plan actions=%s top=%s snapshot_guard=%s', len(actions), plan['summary']['highest_priority_type'], snapshot_guard_active)
    return plan