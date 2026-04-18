from pathlib import Path

from config import load_settings
from state import JsonStateStore
from tasks.decision_engine import normalize_prize_eligible, run


def _settings(tmp_path: Path):
    return load_settings(str(tmp_path / 'config.yaml'))


def test_decision_engine_prioritizes_live_redpacket_and_snapshot_guard(tmp_path, monkeypatch):
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(
        """
AGENTHANSA_API_KEY: <your-agenthansa-api-key>
AGENTHANSA_BOT_STATE_DIR: ./state
AGENTHANSA_BOT_LOG_DIR: ./logs
AGENTHANSA_BOT_DATA_DIR: ./data
AGENTHANSA_BOT_REPORT_DIR: ./reports
AGENTHANSA_BOT_LOCK_FILE: ./bot.lock
AGENTHANSA_SNAPSHOT_GUARD_MINUTES: 90
AGENTHANSA_FORUM_XP_SOFT_CAP: 140
""".strip(),
        encoding='utf-8',
    )
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)

    store.save('daily_xp', {'data': {'today_points': 80, 'alliance_rank': 12, 'prize_eligible': False, 'breakdown': {'forum': {'points': 20}}}})
    store.save('redpacket_state', {
        'status': 'manual_required',
        'overview': {
            'active': [{'id': 'rp1', 'title': 'Solve live packet'}],
            'next_packet_at': '2026-04-17T08:10:00+00:00'
        },
        'packet': {'id': 'rp1', 'title': 'Solve live packet'},
        'reason': 'unsupported_challenge_action',
    })
    store.save('quests', {
        'buckets': {
            'auto_candidate': [
                {'id': 'q1', 'title': 'Write analysis', '_priority_score': 95, '_classification': {'reward_value': 40, 'risk_flags': []}},
            ],
            'manual_or_proof_required': [
                {'id': 'q2', 'title': 'Tweet proof task', '_priority_score': 120, '_classification': {'reward_value': 150, 'risk_flags': ['proof_required_or_likely']}},
            ],
            'review_manually': [],
        },
        'summary': {'auto_candidate': 1, 'manual_or_proof_required': 1, 'review_manually': 0},
    })
    store.save('my_submissions', {
        'risky_count': 1,
        'risky_submissions': [{'quest_title': 'Tweet proof task', 'recommended_action': 'manual_check_proof_url_requirement', 'risk_flags': ['proof_likely_needed']}],
        'summary': {'by_status': {'judging': 1}},
    })
    store.save('forum_strategy', {
        'forum_points': 20,
        'manual_actions': [{'type': 'high_quality_forum_comment', 'reason': 'forum_xp_below_soft_cap'}],
    })
    store.save('notifications', {'unread_count': 3})
    store.save('official_watch', {'changed': ['openapi'], 'diff_summary': {'openapi': {'added_paths': ['/api/red-packets']}}})
    store.save('publish_queue', {'items': []})
    store.save('publish_submit_bridge', {'items': [], 'summary': {'submission_ready': 0}})

    plan = run(settings, store, minutes_until_snapshot=30)

    assert plan['snapshot_guard_active'] is True
    assert plan['actions'][0]['type'] == 'red_packet_manual_intervention'
    assert any(action['type'] == 'submission_risk_review' for action in plan['actions'])
    assert any(action['type'] == 'quest_auto_execution' for action in plan['actions'])
    assert any(action['type'] == 'forum_manual_action' for action in plan['actions'])
    assert any(action['type'] == 'official_watch_review' for action in plan['actions'])
    assert plan['summary']['highest_priority_type'] == 'red_packet_manual_intervention'


def test_decision_engine_deprioritizes_forum_when_soft_cap_reached(tmp_path, monkeypatch):
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(
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
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)

    store.save('daily_xp', {'data': {'today_points': 220, 'alliance_rank': 2, 'prize_eligible': True, 'breakdown': {'forum': {'points': 160}}}})
    store.save('redpacket_state', {'status': 'idle', 'overview': {'next_packet_at': '2026-04-17T10:00:00+00:00'}})
    store.save('quests', {'buckets': {'auto_candidate': [], 'manual_or_proof_required': [], 'review_manually': []}, 'summary': {}})
    store.save('my_submissions', {'risky_count': 0, 'risky_submissions': [], 'summary': {'by_status': {}}})
    store.save('forum_strategy', {'forum_points': 160, 'manual_actions': [{'type': 'stop_forum_push', 'reason': 'forum_xp_near_or_above_soft_cap'}]})
    store.save('notifications', {'unread_count': 0})
    store.save('official_watch', {'changed': []})
    store.save('publish_queue', {'items': []})
    store.save('publish_submit_bridge', {'items': [], 'summary': {'submission_ready': 0}})

    plan = run(settings, store, minutes_until_snapshot=240)

    forum_action = next(action for action in plan['actions'] if action['type'] == 'forum_hold')
    assert forum_action['priority'] < 40
    assert plan['summary']['action_count'] >= 1


def test_decision_engine_ignores_stale_packet_when_no_active_window(tmp_path, monkeypatch):
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(
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
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)

    store.save('daily_xp', {'data': {'today_points': 92, 'alliance_rank': 460, 'prize_eligible': False}})
    store.save('redpacket_state', {
        'status': 'manual_required',
        'overview': {'next_packet_at': '2020-04-16T15:27:30.067648+00:00'},
        'joined': False,
        'packet': {'id': 'stale-packet', 'title': 'Old packet'},
        'reason': 'unsupported_challenge_action',
    })
    store.save('quests', {'buckets': {'auto_candidate': [], 'manual_or_proof_required': [], 'review_manually': []}, 'summary': {}})
    store.save('my_submissions', {'risky_count': 0, 'risky_submissions': [], 'summary': {'by_status': {}}})
    store.save('forum_strategy', {'forum_points': 12, 'manual_actions': []})
    store.save('notifications', {'unread_count': 0})
    store.save('official_watch', {'changed': []})
    store.save('publish_queue', {'items': []})
    store.save('publish_submit_bridge', {'items': [], 'summary': {'submission_ready': 0}})

    plan = run(settings, store, minutes_until_snapshot=200)

    assert plan['summary']['highest_priority_type'] != 'red_packet_manual_intervention'
    assert all(action['type'] != 'red_packet_manual_intervention' for action in plan['actions'])


def test_normalize_prize_eligible_treats_zero_amount_strings_as_false():
    assert normalize_prize_eligible('$0.00') is False
    assert normalize_prize_eligible('0') is False
    assert normalize_prize_eligible('$1.00') is True


def test_decision_engine_treats_zero_prize_amount_as_not_eligible(tmp_path, monkeypatch):
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(
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
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)

    store.save('daily_xp', {'data': {'today_points': 220, 'alliance_rank': 2, 'prize_eligible': '$0.00'}})
    store.save('redpacket_state', {'status': 'idle', 'overview': {}})
    store.save('quests', {'buckets': {'auto_candidate': [], 'manual_or_proof_required': [], 'review_manually': []}, 'summary': {}})
    store.save('my_submissions', {'risky_count': 0, 'risky_submissions': [], 'summary': {'by_status': {}}})
    store.save('forum_strategy', {'forum_points': 20, 'manual_actions': []})
    store.save('notifications', {'unread_count': 0})
    store.save('official_watch', {'changed': []})
    store.save('publish_queue', {'items': []})
    store.save('publish_submit_bridge', {'items': [], 'summary': {'submission_ready': 0}})

    plan = run(settings, store, minutes_until_snapshot=240)

    assert any(action['type'] == 'xp_push' for action in plan['actions'])


def test_decision_engine_adds_forum_curate_when_vote_targets_incomplete(tmp_path, monkeypatch):
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(
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
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)

    store.save('daily_xp', {'data': {'today_points': 120, 'alliance_rank': 30, 'prize_eligible': False, 'breakdown': {
        'forum upvote': {'events': 0, 'points': 0},
        'forum downvote': {'events': 1, 'points': 1},
    }}})
    store.save('redpacket_state', {'status': 'idle', 'overview': {}})
    store.save('quests', {'buckets': {'auto_candidate': [], 'manual_or_proof_required': [], 'review_manually': []}, 'summary': {}})
    store.save('my_submissions', {'risky_count': 0, 'risky_submissions': [], 'summary': {'by_status': {}}})
    store.save('forum_strategy', {'forum_points': 10, 'manual_actions': []})
    store.save('notifications', {'unread_count': 0})
    store.save('official_watch', {'changed': []})
    store.save('publish_queue', {'items': []})
    store.save('publish_submit_bridge', {'items': [], 'summary': {'submission_ready': 0}})

    plan = run(settings, store, minutes_until_snapshot=80)

    forum_curate = next(action for action in plan['actions'] if action['type'] == 'forum_curate')
    assert forum_curate['payload']['remaining_up'] == 5
    assert forum_curate['payload']['remaining_down'] == 4
    assert forum_curate['priority'] >= 80
