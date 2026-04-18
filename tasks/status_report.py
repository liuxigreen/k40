from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config import Settings
from event_notify import send_telegram_message
from state import JsonStateStore
from tasks import decision_engine
from utils.timezone import minutes_until_pst_midnight, utc_now


_ACTION_LABELS = {
    'submission_pause': '暂停提交',
    'submission_risk_review': '复查高风险提交',
    'publish_pipeline': '处理发布队列',
    'publish_submission_ready': '提交已发布任务',
    'quest_auto_execution': '可自动做任务',
    'quest_manual_review': '人工任务待处理',
    'forum_curate': '论坛补票',
    'forum_manual_action': '论坛人工动作',
    'red_packet_watch': '红包监控',
    'xp_push': '冲XP',
    'forum_hold': '暂停论坛刷分',
    'red_packet_manual_intervention': '红包人工处理',
    'official_watch_review': '检查官方变更',
}


def _load_or_build_decision_plan(settings: Settings, store: JsonStateStore) -> dict[str, Any]:
    existing = store.load('decision_plan', default={})
    if existing.get('actions') and existing.get('summary'):
        return existing
    return decision_engine.run(settings, store)


def _action_label(action_type: str | None) -> str:
    if not action_type:
        return '无'
    return _ACTION_LABELS.get(action_type, action_type)


def _completed_task_titles(report: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    decision_actions = ((report.get('decision_plan') or {}).get('actions') or [])
    for action in decision_actions:
        payload = action.get('payload') or {}
        title = str(payload.get('title') or '').strip()
        if title and title not in titles:
            titles.append(title)
        if len(titles) >= 3:
            return titles
    for item in ((report.get('candidate_quests') or [])[:3]):
        title = str(item or '').strip()
        if title and title not in titles:
            titles.append(title)
        if len(titles) >= 3:
            return titles
    for item in ((report.get('publish_queue') or {}).get('items') or [])[:3]:
        title = str(item.get('title') or item.get('quest_id') or '').strip()
        if title and title not in titles:
            titles.append(title)
        if len(titles) >= 3:
            return titles
    return titles


def _leader_gap(report: dict[str, Any]) -> int | None:
    top = (report.get('top_daily_leader') or {}).get('today_points')
    mine = report.get('today_points')
    if top is None or mine is None:
        return None
    try:
        return int(top) - int(mine)
    except Exception:
        return None


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _reason_to_zh(reason: str, flags: list[str] | None = None) -> str:
    text = str(reason or '').strip().lower()
    flag_set = {str(flag).lower() for flag in (flags or [])}
    if 'duplicate proof url' in text:
        return 'proof 链接重复，疑似重复提交'
    if 'too generic' in text or 'lacked evidence' in text:
        return '内容过于泛泛，缺少证据支撑'
    if 'more specifics' in text:
        return '内容不够具体，需要补充细节'
    if 'spam' in flag_set:
        return '疑似垃圾内容或重复提交'
    if 'rejected' in flag_set or 'reject' in flag_set:
        return '已被拒绝，需要人工复查原因'
    if 'low_quality' in flag_set or 'quality' in flag_set:
        return '内容质量偏低，需要重写优化'
    if 'proof_likely_needed' in flag_set:
        return '缺少可验证 proof 链接'
    return str(reason or '待人工复查').strip() or '待人工复查'


def _quest_link(row: dict[str, Any]) -> str | None:
    quest_id = str(row.get('quest_id') or '').strip()
    if not quest_id:
        return None
    return f'https://www.agenthansa.com/alliance-war/quests/{quest_id}'


def _risk_review_lines(report: dict[str, Any]) -> list[str]:
    submissions = (report.get('submissions') or {})
    risky_rows = list(submissions.get('risky_rows') or [])
    if not risky_rows:
        return []
    details: list[str] = []
    index = 0
    for row in risky_rows:
        flags = [str(flag) for flag in (row.get('risk_flags') or [])]
        flag_set = {flag.lower() for flag in flags}
        spam_flagged = bool(row.get('spam_flagged') or row.get('is_spam'))
        grade = str(row.get('ai_grade') or '').strip().upper()
        revision_exhausted = bool(row.get('revision_exhausted'))
        include = spam_flagged or grade in {'C', 'D', 'E', 'F'} or revision_exhausted
        if not include:
            continue
        index += 1
        title = str(row.get('quest_title') or row.get('detail') or 'unknown task').strip()
        reason = _reason_to_zh(str(row.get('ai_summary') or row.get('message') or row.get('revision_note') or ''), flags)
        if revision_exhausted and not spam_flagged and not grade:
            label = 'REVISION_LIMIT'
        else:
            label = 'SPAM' if spam_flagged else (grade or 'LOW')
        link = _quest_link(row) or '无'
        details.extend([
            f'{index}) 标题：{title}',
            f'评分：{label}',
            f'原因：{reason}',
            f'链接：{link}',
        ])
        if revision_exhausted:
            details.append('备注：该任务已用满 5 次修改，已自动过滤，不再重提。')
        if index >= 4:
            break
    return details


def _risk_count_label(report: dict[str, Any]) -> str:
    submissions = (report.get('submissions') or {})
    risky_rows = list(submissions.get('risky_rows') or [])
    spam_count = 0
    revision_limit_count = 0
    for row in risky_rows:
        if bool(row.get('spam_flagged') or row.get('is_spam')):
            spam_count += 1
        if bool(row.get('revision_exhausted')):
            revision_limit_count += 1
    risky_count = int(submissions.get('risky_count') or 0)
    if risky_count <= 0:
        return '0'
    notes = ['待复查']
    if spam_count > 0:
        notes.append('不全是spam')
    if revision_limit_count > 0:
        notes.append(f'{revision_limit_count}个已达5次修改')
    return f"{risky_count}（{'，'.join(notes)}）"


def _publish_blocker_lines(report: dict[str, Any]) -> list[str]:
    queue = (report.get('publish_queue') or {})
    items = list(queue.get('items') or [])
    blocked = [item for item in items if str(item.get('status') or '').strip().lower() in {'publish_error', 'waiting_for_publish'}]
    if not blocked:
        return []
    twitter_blocked = [item for item in blocked if str(item.get('platform') or '').strip().lower() == 'twitter']
    lines = [f"发布阻塞：{len(blocked)}（待人工）"]
    if twitter_blocked:
        lines.append('Twitter/X 发布鉴权失败或 proof 缺失')
    else:
        lines.append('外部发布未完成，需人工补 proof 或处理发布失败')
    return lines


def _telegram_summary_message(report: dict[str, Any], *, pre_snapshot: bool = False) -> str:
    top_action_type = (((report.get('decision_plan') or {}).get('summary') or {}).get('highest_priority_type') or 'none')
    top_action = _action_label(top_action_type)
    tasks = '、'.join(_completed_task_titles(report)) or '签到/刷新/策略检查'
    gap = _leader_gap(report)
    gap_text = '未知' if gap is None else (f'落后{gap}分' if gap > 0 else '已追平或领先')
    prefix = '快照前提醒｜' if pre_snapshot else ''
    lines = [
        f"{prefix}当前XP：{report.get('today_points')}，联盟排名：{report.get('alliance_rank')}，奖励：{report.get('prize_eligible')}，距离榜首：{gap_text}。",
        f"已做任务：{tasks}。",
        f"风险提交：{_risk_count_label(report)}，发布队列：{report.get('publish_queue', {}).get('queued')}，下一重点：{top_action}。",
    ]
    publish_lines = _publish_blocker_lines(report)
    if publish_lines:
        lines.extend(publish_lines)
    risk_lines = _risk_review_lines(report)
    if risk_lines:
        lines.append('## 低分 / Spam 明细')
        lines.extend(risk_lines)
    return '\n'.join(lines)


def _maybe_notify_status_report(settings: Settings, store: JsonStateStore, report: dict[str, Any]) -> bool:
    if not settings.notify_telegram:
        return False
    notify_state = store.load('status_report_notify_state', default={})
    current = {
        'today_points': report.get('today_points'),
        'alliance_rank': report.get('alliance_rank'),
        'prize_eligible': report.get('prize_eligible'),
        'action_summary': (((report.get('decision_plan') or {}).get('summary') or {}).get('highest_priority_type')),
    }
    minute_bucket = report.get('minutes_until_snapshot')
    pre_snapshot_window = minute_bucket is not None and int(minute_bucket) <= 90
    today_key = notify_state.get('pre_snapshot_sent_day') or (str(report.get('generated_at') or '').split('T', 1)[0] or None)
    last_sent_at = _parse_iso(notify_state.get('last_sent_at'))
    current_time = _parse_iso(report.get('generated_at'))
    cooldown_ok = True
    if last_sent_at is not None and current_time is not None:
        cooldown_ok = (current_time - last_sent_at).total_seconds() >= 3 * 60 * 60
    should_send_regular = cooldown_ok and (notify_state.get('last_sent') != current)
    should_send_pre_snapshot = bool(pre_snapshot_window and today_key and notify_state.get('pre_snapshot_sent_day') != today_key)
    if not should_send_regular and not should_send_pre_snapshot:
        return False
    pre_snapshot = bool(should_send_pre_snapshot)
    message = _telegram_summary_message(report, pre_snapshot=pre_snapshot)
    sent = send_telegram_message(settings, message)
    if sent:
        payload = dict(notify_state)
        payload['message'] = message
        payload['sent_at'] = report.get('generated_at')
        payload['last_sent_at'] = report.get('generated_at')
        if should_send_regular or 'last_sent' not in payload:
            payload['last_sent'] = current
        if should_send_pre_snapshot:
            payload['pre_snapshot_sent_day'] = today_key
        elif 'pre_snapshot_sent_day' not in payload:
            payload['pre_snapshot_sent_day'] = None
        store.save('status_report_notify_state', payload)
    return sent


def run(settings: Settings, store: JsonStateStore) -> dict[str, Any]:
    daily_xp = store.load('daily_xp', default={}).get('data', {})
    leaderboards = store.load('leaderboards', default={}).get('data', {})
    redpacket = store.load('redpacket_state', default={})
    submissions = store.load('my_submissions', default={})
    forum_strategy = store.load('forum_strategy', default={})
    official_watch = store.load('official_watch', default={})
    notifications = store.load('notifications', default={})
    publish_queue = store.load('publish_queue', default={})
    publish_bridge = store.load('publish_submit_bridge', default={})
    decision_plan = _load_or_build_decision_plan(settings, store)

    minutes_until_snapshot = decision_plan.get('minutes_until_snapshot')
    if minutes_until_snapshot is None:
        minutes_until_snapshot = minutes_until_pst_midnight()

    report = {
        'generated_at': utc_now().isoformat(),
        'minutes_until_snapshot': minutes_until_snapshot,
        'snapshot_guard_active': int(minutes_until_snapshot) <= settings.snapshot_guard_minutes,
        'agent': daily_xp.get('agent'),
        'alliance': daily_xp.get('alliance'),
        'today_points': daily_xp.get('today_points'),
        'alliance_rank': daily_xp.get('alliance_rank'),
        'prize_eligible': daily_xp.get('prize_eligible'),
        'red_packet': {
            'next_packet_at': (redpacket.get('overview') or {}).get('next_packet_at'),
            'last_joined_packet_id': redpacket.get('last_joined_packet_id') or redpacket.get('overview', {}).get('last_joined_packet_id'),
            'status': redpacket.get('status'),
        },
        'submissions': {
            'count': submissions.get('count', 0),
            'risky_count': submissions.get('risky_count', 0),
            'summary': submissions.get('summary', {}),
            'top_risks': [row.get('quest_title') or row.get('detail') for row in submissions.get('risky_submissions', [])[:5]],
            'risky_rows': [
                {
                    'quest_title': row.get('quest_title') or row.get('detail'),
                    'quest_id': row.get('quest_id'),
                    'ai_grade': row.get('ai_grade'),
                    'ai_summary': row.get('ai_summary'),
                    'message': row.get('message'),
                    'risk_flags': row.get('risk_flags', []),
                    'spam_flagged': bool(row.get('spam_flagged') or row.get('is_spam')),
                    'is_spam': bool(row.get('spam_flagged') or row.get('is_spam')),
                    'revision_exhausted': bool(row.get('revision_exhausted')),
                    'revision_note': row.get('revision_note'),
                }
                for row in submissions.get('risky_submissions', [])[:8]
            ],
        },
        'official_changes': official_watch.get('changed', []),
        'official_diff_summary': official_watch.get('diff_summary', {}),
        'unread_notifications': notifications.get('unread_count', 0),
        'manual_actions': forum_strategy.get('manual_actions', []),
        'publish_queue': {
            'queued': len(publish_queue.get('items', []) or []),
            'submission_ready': int((publish_bridge.get('summary') or {}).get('submission_ready', 0) or 0),
            'items': [
                {
                    'quest_id': item.get('quest_id'),
                    'platform': item.get('platform'),
                    'status': item.get('status'),
                    'published_url': item.get('published_url'),
                }
                for item in (publish_queue.get('items', []) or [])[:5]
            ],
        },
        'decision_plan': decision_plan,
        'candidate_quests': [
            str((action.get('payload') or {}).get('title') or '').strip()
            for action in (decision_plan.get('actions') or [])
            if str((action.get('payload') or {}).get('title') or '').strip()
        ],
        'top_daily_leader': (((leaderboards.get('daily_points') or {}).get('leaderboard') or [{}])[0]),
    }

    settings.report_dir.mkdir(parents=True, exist_ok=True)
    (settings.report_dir / 'latest_status.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    markdown = [
        '# AgentHansa Status Report',
        f"- Generated: {report['generated_at']}",
        f"- Agent: {report.get('agent')}",
        f"- Alliance: {report.get('alliance')}",
        f"- Today points: {report.get('today_points')}",
        f"- Alliance rank: {report.get('alliance_rank')}",
        f"- Prize eligible: {report.get('prize_eligible')}",
        f"- Minutes until snapshot: {report.get('minutes_until_snapshot')}",
        f"- Snapshot guard active: {report.get('snapshot_guard_active')}",
        f"- Next red packet: {report['red_packet'].get('next_packet_at')}",
        f"- Submission risk count: {report['submissions'].get('risky_count')}",
        f"- Submission status summary: {report['submissions'].get('summary')}",
        f"- Top risk titles: {report['submissions'].get('top_risks')}",
        f"- Publish queue: queued={report['publish_queue'].get('queued')} submission_ready={report['publish_queue'].get('submission_ready')}",
        f"- Official changes: {', '.join(report.get('official_changes', [])) or 'none'}",
        f"- Unread notifications: {report.get('unread_notifications')}",
        '',
        '## Prioritized action plan',
    ]
    for item in (decision_plan.get('actions') or [])[:8]:
        markdown.append(f"- P{item.get('priority')}: {item.get('type')} — {item.get('reason')}")
    markdown.extend([
        '',
        '## Manual actions',
    ])
    for item in report.get('manual_actions', []):
        markdown.append(f"- {item.get('type')}: {item.get('reason', item.get('next_focus', ''))}")
    (settings.report_dir / 'latest_status.md').write_text("\n".join(markdown), encoding='utf-8')
    _maybe_notify_status_report(settings, store, report)
    return report
