from pathlib import Path

from config import load_settings
from official_watch import _fetch_text_with_fallback
from state import JsonStateStore
from tasks import status_report
from tasks.my_submissions import _collect_detail_cached
from event_notify import build_redpacket_notification


class DummyResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class DummyHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url: str):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return DummyResponse(item)


class DummyQuestClient:
    def __init__(self):
        self.calls = []

    def get(self, path: str):
        self.calls.append(path)
        return {'id': path.rsplit('/', 1)[-1], 'reward': '$10'}


def _settings(tmp_path: Path):
    cfg = tmp_path / 'config.yaml'
    cfg.write_text(
        """
AGENTHANSA_API_KEY: <your-agenthansa-api-key>
AGENTHANSA_BOT_STATE_DIR: ./state
AGENTHANSA_BOT_LOG_DIR: ./logs
AGENTHANSA_BOT_DATA_DIR: ./data
AGENTHANSA_BOT_REPORT_DIR: ./reports
AGENTHANSA_BOT_LOCK_FILE: ./bot.lock
AGENTHANSA_FORUM_XP_SOFT_CAP: 140
""".strip(),
        encoding='utf-8',
    )
    return load_settings(str(cfg))


def test_fetch_text_with_fallback_uses_cached_text_after_retry_failure(tmp_path):
    previous = {'text': 'cached-openapi-text'}
    client = DummyHttpClient([RuntimeError('connection reset by peer'), RuntimeError('connection reset by peer')])

    text, meta = _fetch_text_with_fallback('https://example.com/openapi.json', previous=previous, client=client, attempts=2, base_sleep=0)

    assert text == 'cached-openapi-text'
    assert meta['used_fallback'] is True
    assert meta['attempt_count'] == 2
    assert client.calls == 2


def test_collect_detail_cached_avoids_duplicate_network_calls():
    client = DummyQuestClient()
    cache = {}

    first = _collect_detail_cached(client, 'quest-1', cache)
    second = _collect_detail_cached(client, 'quest-1', cache)

    assert first == second
    assert client.calls == ['/alliance-war/quests/quest-1']


def test_status_report_reuses_existing_decision_plan(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    plan = {
        'generated_at': '2026-04-16T00:00:00+00:00',
        'minutes_until_snapshot': 120,
        'snapshot_guard_active': False,
        'actions': [{'priority': 80, 'type': 'submission_risk_review', 'reason': 'cached'}],
        'summary': {'action_count': 1, 'highest_priority_type': 'submission_risk_review'},
    }
    store.save('decision_plan', plan)
    store.save('daily_xp', {'data': {}})
    store.save('leaderboards', {'data': {}})
    store.save('redpacket_state', {})
    store.save('my_submissions', {'summary': {}, 'risky_submissions': [], 'risky_count': 0})
    store.save('forum_strategy', {'manual_actions': []})
    store.save('official_watch', {'changed': []})
    store.save('notifications', {'unread_count': 0})

    called = {'count': 0}

    def fail_if_called(*args, **kwargs):
        called['count'] += 1
        raise AssertionError('decision_engine.run should not be called when fresh plan exists')

    monkeypatch.setattr(status_report.decision_engine, 'run', fail_if_called)
    report = status_report.run(settings, store)

    assert called['count'] == 0
    assert report['decision_plan']['summary']['highest_priority_type'] == 'submission_risk_review'


def test_status_report_sends_chinese_telegram_summary_only_every_3_hours_and_includes_low_grade_details(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    settings.notify_telegram = True
    settings.telegram_bot_token = '123:abc'
    settings.telegram_chat_id = '@agenthas11'
    settings.notify_prefix = 'K40'
    store = JsonStateStore(settings.state_dir)
    store.save('decision_plan', {
        'generated_at': '2026-04-16T00:00:00+00:00',
        'minutes_until_snapshot': 120,
        'snapshot_guard_active': False,
        'actions': [
            {'priority': 70, 'type': 'quest_auto_execution', 'reason': 'high_value_auto_candidate_available', 'payload': {'title': 'Create Polls '}},
            {'priority': 50, 'type': 'xp_push', 'reason': 'daily_xp_or_rank_below_target'},
        ],
        'summary': {'action_count': 2, 'highest_priority_type': 'quest_auto_execution'},
    })
    store.save('leaderboards', {'data': {'daily_points': {'leaderboard': [{'name': 'Jarvis', 'today_points': 736}]}}})
    store.save('redpacket_state', {'overview': {'next_packet_at': '2026-04-17T06:28:31+00:00'}})
    store.save('my_submissions', {
        'summary': {},
        'risky_submissions': [
            {
                'quest_title': 'Bad research task',
                'quest_id': 'q-bad-research',
                'ai_grade': 'C',
                'ai_summary': 'too generic and lacked evidence',
                'message': 'merchant asked for more specifics',
                'risk_flags': ['low_quality'],
            },
            {
                'quest_title': 'Spammy task',
                'quest_id': 'q-spammy',
                'ai_grade': 'D',
                'ai_summary': 'duplicate proof url',
                'message': 'duplicate proof url',
                'is_spam': True,
                'risk_flags': ['spam'],
            },
        ],
        'risky_count': 2,
        'submissions': [
            {
                'quest_title': 'Bad research task',
                'quest_id': 'q-bad-research',
                'ai_grade': 'C',
                'ai_summary': 'too generic and lacked evidence',
                'message': 'merchant asked for more specifics',
                'risk_flags': ['low_quality'],
            },
            {
                'quest_title': 'Spammy task',
                'quest_id': 'q-spammy',
                'ai_grade': 'D',
                'ai_summary': 'duplicate proof url',
                'message': 'duplicate proof url',
                'is_spam': True,
                'risk_flags': ['spam'],
            },
        ],
    })
    store.save('forum_strategy', {'manual_actions': []})
    store.save('official_watch', {'changed': []})
    store.save('notifications', {'unread_count': 0})
    sent = []
    monkeypatch.setattr(status_report, 'send_telegram_message', lambda settings, message, client=None: sent.append(message) or True)

    timestamps = iter([
        '2026-04-17T00:00:00+00:00',
        '2026-04-17T01:00:00+00:00',
        '2026-04-17T03:05:00+00:00',
    ])

    class _FakeNow:
        def __init__(self, value):
            self.value = value
        def isoformat(self):
            return self.value

    monkeypatch.setattr(status_report, 'utc_now', lambda: _FakeNow(next(timestamps)))

    store.save('daily_xp', {'data': {'agent': 'finance8006-agent', 'alliance': 'blue', 'today_points': 315, 'alliance_rank': 23, 'prize_eligible': '$0.00'}})
    status_report.run(settings, store)
    store.save('daily_xp', {'data': {'agent': 'finance8006-agent', 'alliance': 'blue', 'today_points': 325, 'alliance_rank': 20, 'prize_eligible': '$0.00'}})
    status_report.run(settings, store)
    store.save('daily_xp', {'data': {'agent': 'finance8006-agent', 'alliance': 'blue', 'today_points': 330, 'alliance_rank': 19, 'prize_eligible': '$0.00'}})
    status_report.run(settings, store)

    assert len(sent) == 2
    assert '当前XP：315' in sent[0]
    assert '当前XP：330' in sent[1]
    assert '风险提交：2（待复查，不全是spam）' in sent[0]
    assert '## 低分 / Spam 明细' in sent[0]
    assert '1) 标题：Bad research task' in sent[0]
    assert '评分：C' in sent[0]
    assert '原因：内容过于泛泛，缺少证据支撑' in sent[0]
    assert '链接：https://www.agenthansa.com/alliance-war/quests/q-bad-research' in sent[0]
    assert '2) 标题：Spammy task' in sent[0]
    assert '评分：SPAM' in sent[0]
    assert '原因：proof 链接重复，疑似重复提交' in sent[0]
    assert '链接：https://www.agenthansa.com/alliance-war/quests/q-spammy' in sent[0]
    notify_state = store.load('status_report_notify_state', default={})
    assert notify_state['last_sent']['today_points'] == 330
    assert notify_state['last_sent']['alliance_rank'] == 19
    assert notify_state['last_sent']['prize_eligible'] == '$0.00'
    assert notify_state['last_sent']['action_summary'] == 'quest_auto_execution'
    assert notify_state['pre_snapshot_sent_day'] is None
    assert notify_state['last_sent_at'] == '2026-04-17T03:05:00+00:00'


def test_status_report_sends_one_pre_snapshot_notice_even_without_state_change(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    settings.notify_telegram = True
    settings.telegram_bot_token = '123:abc'
    settings.telegram_chat_id = '@agenthas11'
    settings.notify_prefix = 'K40'
    store = JsonStateStore(settings.state_dir)
    store.save('decision_plan', {
        'generated_at': '2026-04-16T00:00:00+00:00',
        'minutes_until_snapshot': 70,
        'snapshot_guard_active': True,
        'actions': [{'priority': 50, 'type': 'xp_push', 'reason': 'daily_xp_or_rank_below_target'}],
        'summary': {'action_count': 1, 'highest_priority_type': 'xp_push'},
    })
    store.save('daily_xp', {'data': {'agent': 'finance8006-agent', 'alliance': 'blue', 'today_points': 315, 'alliance_rank': 23, 'prize_eligible': '$0.00'}})
    store.save('leaderboards', {'data': {'daily_points': {'leaderboard': [{'name': 'Jarvis', 'today_points': 736}]}}})
    store.save('redpacket_state', {})
    store.save('my_submissions', {'summary': {}, 'risky_submissions': [], 'risky_count': 0})
    store.save('forum_strategy', {'manual_actions': []})
    store.save('official_watch', {'changed': []})
    store.save('notifications', {'unread_count': 0})
    sent = []
    monkeypatch.setattr(status_report, 'send_telegram_message', lambda settings, message, client=None: sent.append(message) or True)
    monkeypatch.setattr(status_report, 'minutes_until_pst_midnight', lambda: 70)

    status_report.run(settings, store)
    status_report.run(settings, store)

    assert len(sent) == 1
    assert '快照前提醒' in sent[0]
    notify_state = store.load('status_report_notify_state', default={})
    assert notify_state['pre_snapshot_sent_day'] is not None
    assert notify_state['last_sent']['today_points'] == 315
    assert notify_state['last_sent']['action_summary'] == 'xp_push'


def test_status_report_does_not_repeat_pre_snapshot_notice_same_day(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    settings.notify_telegram = True
    settings.telegram_bot_token = '123:abc'
    settings.telegram_chat_id = '@agenthas11'
    settings.notify_prefix = 'K40'
    store = JsonStateStore(settings.state_dir)
    store.save('decision_plan', {
        'generated_at': '2026-04-16T00:00:00+00:00',
        'minutes_until_snapshot': 60,
        'snapshot_guard_active': True,
        'actions': [{'priority': 50, 'type': 'xp_push', 'reason': 'daily_xp_or_rank_below_target'}],
        'summary': {'action_count': 1, 'highest_priority_type': 'xp_push'},
    })
    store.save('daily_xp', {'data': {'agent': 'finance8006-agent', 'alliance': 'blue', 'today_points': 315, 'alliance_rank': 23, 'prize_eligible': '$0.00'}})
    store.save('leaderboards', {'data': {'daily_points': {'leaderboard': [{'name': 'Jarvis', 'today_points': 736}]}}})
    store.save('redpacket_state', {})
    store.save('my_submissions', {'summary': {}, 'risky_submissions': [], 'risky_count': 0})
    store.save('forum_strategy', {'manual_actions': []})
    store.save('official_watch', {'changed': []})
    store.save('notifications', {'unread_count': 0})
    store.save('status_report_notify_state', {
        'last_sent': {'today_points': 315, 'alliance_rank': 23, 'prize_eligible': '$0.00', 'action_summary': 'xp_push'},
        'pre_snapshot_sent_day': '2026-04-17',
    })
    monkeypatch.setattr(status_report, 'send_telegram_message', lambda settings, message, client=None: (_ for _ in ()).throw(AssertionError('should not send')))
    monkeypatch.setattr(status_report, 'minutes_until_pst_midnight', lambda: 60)

    status_report.run(settings, store)
    notify_state = store.load('status_report_notify_state', default={})
    assert notify_state['pre_snapshot_sent_day'] == '2026-04-17'


def test_status_report_skips_telegram_summary_when_disabled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('decision_plan', {
        'generated_at': '2026-04-16T00:00:00+00:00',
        'minutes_until_snapshot': 120,
        'snapshot_guard_active': False,
        'actions': [],
        'summary': {'action_count': 0, 'highest_priority_type': None},
    })
    store.save('daily_xp', {'data': {'today_points': 315, 'alliance_rank': 23, 'prize_eligible': '$0.00'}})
    store.save('leaderboards', {'data': {}})
    store.save('redpacket_state', {})
    store.save('my_submissions', {'summary': {}, 'risky_submissions': [], 'risky_count': 0})
    store.save('forum_strategy', {'manual_actions': []})
    store.save('official_watch', {'changed': []})
    store.save('notifications', {'unread_count': 0})

    monkeypatch.setattr(status_report, 'send_telegram_message', lambda settings, message, client=None: (_ for _ in ()).throw(AssertionError('should not send')))
    status_report.run(settings, store)
    assert store.load('status_report_notify_state', default={}) == {}


def test_status_report_message_calls_out_publish_pipeline_blockers():
    report = {
        'today_points': 315,
        'alliance_rank': 23,
        'prize_eligible': '$0.00',
        'submissions': {'risky_count': 0, 'risky_rows': []},
        'publish_queue': {
            'queued': 2,
            'submission_ready': 0,
            'items': [
                {'quest_id': 'q1', 'platform': 'twitter', 'status': 'publish_error', 'published_url': None},
                {'quest_id': 'q2', 'platform': 'twitter', 'status': 'publish_error', 'published_url': None},
            ],
        },
        'decision_plan': {
            'summary': {'highest_priority_type': 'publish_pipeline'},
            'actions': [],
        },
        'candidate_quests': [],
        'top_daily_leader': {'today_points': 736},
        'generated_at': '2026-04-18T07:00:00+00:00',
        'minutes_until_snapshot': 40,
    }

    message = status_report._telegram_summary_message(report, pre_snapshot=True)

    assert '发布阻塞：2（待人工）' in message
    assert 'Twitter/X 发布鉴权失败或 proof 缺失' in message


def test_build_redpacket_notification_formats_manual_required_message():
    message = build_redpacket_notification({
        'status': 'manual_required',
        'reason': 'required_action_needs_manual_intervention',
        'packet': {'id': 'rp-live-1', 'title': 'Forum vote needed'},
        'overview': {'next_packet_at': '2026-04-18T08:00:00+00:00'},
    })

    assert '红包需人工处理' in message
    assert '原因=前置动作需要人工处理' not in message
    assert '原因：前置动作需要人工处理' in message
    assert '下次=2026-04-18T08:00:00+00:00' in message
