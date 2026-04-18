from pathlib import Path

from config import load_settings
from state import JsonStateStore
from tasks import decision_engine
from tasks.submission_strategy import (
    can_submit_now,
    default_strategy_state,
    normalize_submission_feedback,
    record_submission_feedback,
)


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
""".strip(),
        encoding='utf-8',
    )
    return load_settings(str(cfg))


def test_can_submit_now_blocks_when_daily_spam_threshold_hit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    state = default_strategy_state()
    state['daily']['2026-04-16'] = {'submission_count': 1, 'spam_count': 1}
    store.save('submission_strategy', state)

    allowed, reason = can_submit_now(store, now_iso='2026-04-16T10:00:00+00:00')

    assert allowed is False
    assert reason == 'daily_spam_threshold_hit'


def test_record_submission_feedback_sets_cooldown_after_duplicate_spam(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    record_submission_feedback(
        store,
        {
            'quest_id': 'q1',
            'quest_title': 'Topify lead list',
            'quest_type': 'company_research',
            'created_at': '2026-04-16T15:05:23.896562+00:00',
            'spam_flagged': True,
            'message': 'duplicate proof url',
        },
        now_iso='2026-04-16T15:05:23.896562+00:00',
    )
    state = store.load('submission_strategy', default={})

    assert state['daily']['2026-04-16']['spam_count'] == 1
    assert state['global_pause_until'].startswith('2026-04-17T')
    assert state['quest_type_pause_until']['company_research'].startswith('2026-04-19T')


def test_normalize_submission_feedback_extracts_grade_and_spam():
    row = normalize_submission_feedback(
        {
            'quest_id': 'q1',
            'title': 'Example',
            'ai_grade': 'B',
            'ai_summary': 'solid',
            'is_spam': False,
            'created_at': '2026-04-16T00:00:00+00:00',
        },
        quest_type='company_research',
    )
    assert row['ai_grade'] == 'B'
    assert row['spam_flagged'] is False


def test_decision_engine_surfaces_submission_pause(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('daily_xp', {'data': {'today_points': 92, 'alliance_rank': 488, 'prize_eligible': True}})
    store.save('redpacket_state', {'status': 'idle', 'overview': {}})
    store.save('quests', {'buckets': {'auto_candidate': [], 'manual_or_proof_required': [], 'review_manually': []}, 'summary': {}})
    store.save('my_submissions', {'risky_count': 0, 'risky_submissions': [], 'summary': {'by_status': {}}})
    store.save('forum_strategy', {'forum_points': 12, 'manual_actions': []})
    store.save('official_watch', {'changed': []})
    store.save('notifications', {'unread_count': 0})
    store.save('submission_strategy', {
        'global_pause_until': '2099-04-17T15:05:23+00:00',
        'daily': {},
        'history': [],
        'quest_type_pause_until': {},
    })

    plan = decision_engine.run(settings, store, minutes_until_snapshot=200)

    assert any(action['type'] == 'submission_pause' for action in plan['actions'])
