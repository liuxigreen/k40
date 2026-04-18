from __future__ import annotations

from pathlib import Path

from config import load_settings
from state import JsonStateStore
from tasks import status_report


def test_status_report_includes_revision_limit_without_calling_it_spam(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings(None)
    settings.notify_telegram = False
    store = JsonStateStore(settings.state_dir)

    store.save('daily_xp', {'data': {'agent': 'finance8006-agent', 'alliance': 'blue', 'today_points': 330, 'alliance_rank': 19, 'prize_eligible': '$0.00'}})
    store.save('leaderboards', {'data': {'daily_points': {'leaderboard': [{'name': 'Jarvis', 'today_points': 736}]}}})
    store.save('redpacket_state', {'overview': {'next_packet_at': '2026-04-17T06:28:31+00:00'}})
    store.save('my_submissions', {
        'summary': {},
        'risky_submissions': [
            {
                'quest_title': 'Translate the AgentHansa landing page into your strongest non-English language',
                'quest_id': 'q-translate',
                'ai_grade': None,
                'ai_summary': None,
                'message': None,
                'risk_flags': ['revision_exhausted'],
                'revision_exhausted': True,
                'revision_note': 'Maximum 5 revisions per submission. Make each one count.',
                'is_spam': False,
            },
            {
                'quest_title': 'Spammy task',
                'quest_id': 'q-spammy',
                'ai_grade': 'D',
                'ai_summary': 'duplicate proof url',
                'message': 'duplicate proof url',
                'risk_flags': ['spam'],
                'is_spam': True,
            },
        ],
        'risky_count': 2,
        'submissions': [],
    })
    store.save('forum_strategy', {'manual_actions': []})
    store.save('official_watch', {'changed': []})
    store.save('notifications', {'unread_count': 0})
    store.save('publish_queue', {'items': []})
    store.save('publish_submit_bridge', {'summary': {'submission_ready': 0}, 'items': []})
    store.save('decision_plan', {'summary': {'highest_priority_type': 'submission_risk_review'}, 'actions': []})

    report = status_report.run(settings, store)
    message = status_report._telegram_summary_message(report)

    assert '风险提交：2（待复查，不全是spam，1个已达5次修改）' in message
    assert '评分：REVISION_LIMIT' in message
    assert '备注：该任务已用满 5 次修改，已自动过滤，不再重提。' in message
    assert '评分：SPAM' in message
