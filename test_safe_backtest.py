import json
import sys
from pathlib import Path

from config import load_settings
from state import JsonStateStore

import safe_backtest
from safe_backtest import run_safe_backtest


class RecordingClient:
    def __init__(self):
        self.calls = []

    def get(self, path: str, **kwargs):
        self.calls.append(("GET", path, kwargs))
        if path == '/agents/feed':
            return {
                'quests': [
                    {'id': 'q1', 'title': 'Create Polls', 'reward': '$15', 'urgency': 'closing_soon', 'status': 'open', 'require_proof': False},
                    {'id': 'q2', 'title': 'Write a Twitter thread', 'reward': '$120', 'urgency': 'open', 'status': 'open', 'require_proof': True},
                ],
                'urgent': [{'id': 'u1', 'title': 'Urgent quest'}],
            }
        if path == '/alliance-war/quests':
            return {
                'quests': [
                    {'id': 'q1', 'title': 'Create Polls', 'reward': '$15', 'urgency': 'closing_soon', 'status': 'open', 'require_proof': False},
                    {'id': 'q2', 'title': 'Write a Twitter thread', 'reward': '$120', 'urgency': 'open', 'status': 'voting', 'require_proof': True},
                ]
            }
        if path == '/agents/my-daily-xp':
            return {
                'agent': 'finance8006-agent',
                'alliance': 'blue',
                'today_points': 315,
                'alliance_rank': 23,
                'prize_eligible': '$0.00',
                'breakdown': {
                    'forum upvote': {'events': 1, 'points': 1},
                    'forum downvote': {'events': 0, 'points': 0},
                },
            }
        if path == '/agents/daily-points-leaderboard':
            return {'leaderboard': [{'name': 'Jarvis', 'today_points': 736}]}
        if path == '/agents/alliance-daily-leaderboard':
            return {'leaderboard': [{'name': 'Blue', 'today_points': 1200}]}
        if path == '/agents/alliance-leaderboard':
            return {'leaderboard': [{'name': 'Blue', 'points': 5000}]}
        if path == '/agents/leaderboard':
            return {'leaderboard': [{'name': 'Jarvis', 'points': 9999}]}
        if path == '/red-packets':
            return {
                'active': [
                    {
                        'id': 'packet-safe',
                        'title': 'Upvote a forum post',
                        'challenge_description': 'Upvote a forum post: POST /api/forum/{post_id}/vote with {"vote": "up"}.',
                    }
                ],
                'next_packet_at': '2026-04-17T06:28:31+00:00',
                'next_packet_seconds': 600,
            }
        if path == '/red-packets/packet-safe/challenge':
            return {'question': 'What is 2 + 2?'}
        if path == '/alliance-war/quests/my':
            return {'submissions': [{'id': 'sub1', 'quest_id': 'q1', 'quest_title': 'Create Polls', 'status': 'submitted'}]}
        if path == '/alliance-war/quests/q1':
            return {'id': 'q1', 'reward': '$15', 'title': 'Create Polls'}
        if path == '/alliance-war/quests/q2':
            return {'id': 'q2', 'reward': '$120', 'title': 'Write a Twitter thread'}
        if path == '/alliance-war/quests/q2/submissions':
            return {'submissions': [{'id': 'sv1', 'proof_url': 'https://example.com/proof', 'content': 'x' * 300, 'verified': True}]}
        if path == '/forum?sort=recent&limit=5':
            return {'posts': [{'id': 'forum-safe-1'}]}
        if path in {
            '/forum?sort=recent&limit=30',
            '/forum?sort=recent&limit=100',
            '/forum?sort=recent&limit=100&page=1',
            '/forum?sort=recent&limit=100&page=2',
            '/forum?sort=recent&limit=100&page=3',
            '/forum?sort=recent&limit=100&page=4',
            '/forum?sort=recent&limit=100&page=5',
        }:
            return {'posts': [{'id': 'forum-safe-1'}, {'id': 'forum-safe-2'}, {'id': 'forum-safe-3'}]}
        if path == '/forum/digest':
            return {'posts': [{'id': 'fp1', 'title': 'Digest post', 'body': 'Useful body', 'category': 'strategy'}]}
        if path == '/forum/alliance':
            return {'posts': [{'id': 'fa1', 'title': 'Alliance post', 'body': 'Alliance body', 'category': 'ops'}]}
        if path == '/agents/notifications':
            return {'unread_count': 2, 'notifications': [{'id': 'n1', 'title': 'Ping'}]}
        raise AssertionError(f'unexpected GET path: {path}')

    def post(self, path: str, json=None, **kwargs):
        self.calls.append(("POST", path, {'json': json, **kwargs}))
        raise AssertionError(f'safe backtest should not POST {path}')

    def patch(self, path: str, json=None, **kwargs):
        self.calls.append(("PATCH", path, {'json': json, **kwargs}))
        raise AssertionError(f'safe backtest should not PATCH {path}')


def _settings(tmp_path: Path):
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(
        """
AGENTHANSA_API_KEY: <your-agenthansa-api-key>
AGENTHANSA_BOT_STATE_DIR: ./state
AGENTHANSA_BOT_LOG_DIR: ./logs
AGENTHANSA_BOT_DATA_DIR: ./data
AGENTHANSA_BOT_REPORT_DIR: ./reports
AGENTHANSA_BOT_LOCK_FILE: ./bot.lock
AGENTHANSA_ENABLE_CHECKIN: true
AGENTHANSA_ENABLE_RED_PACKET: true
AGENTHANSA_USE_REDPACKET_WATCHER: true
AGENTHANSA_ENABLE_OFFICIAL_WATCH: false
AGENTHANSA_ENABLE_NOTIFICATIONS: true
AGENTHANSA_ENABLE_VOTING_SUGGESTIONS: true
AGENTHANSA_ENABLE_FORUM_AUTOMATION: true
AGENTHANSA_ENABLE_PUBLISH_PIPELINE: true
AGENTHANSA_NOTIFY_TELEGRAM: false
""".strip(),
        encoding='utf-8',
    )
    return load_settings(str(config_path))


def test_run_safe_backtest_runs_all_tasks_without_writes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    client = RecordingClient()

    result = run_safe_backtest(settings, store, client=client)

    assert result['summary']['task_count'] >= 10
    assert result['summary']['error_count'] == 0, result['summary']['errors']
    assert result['summary']['post_call_count'] == 0
    assert result['summary']['patch_call_count'] == 0
    assert result['tasks']['checkin']['result']['status'] == 'dry_run'
    assert result['tasks']['redpacket']['result']['status'] == 'dry_run_skipped_by_watcher_mode'
    assert result['tasks']['forum_curation']['result']['dry_run'] is True
    assert result['tasks']['publishing_queue']['result']['summary']['queued'] >= 1
    assert result['tasks']['publish_external']['result']['summary']['published'] == 0
    assert result['tasks']['publish_external']['result']['summary']['dry_run'] is True
    assert result['tasks']['publish_submit_bridge']['result']['summary']['submission_ready'] == 0
    assert result['tasks']['publish_submission_execute']['result']['summary']['submitted'] == 0
    assert result['tasks']['publish_submission_execute']['result']['summary']['dry_run'] is True
    assert result['tasks']['status_report']['result']['today_points'] == 315
    assert all(call[0] == 'GET' for call in client.calls)


def test_run_safe_backtest_can_force_redpacket_dry_run_even_in_watcher_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    client = RecordingClient()

    result = run_safe_backtest(settings, store, client=client, include_redpacket=True)

    assert result['tasks']['redpacket']['result']['status'] == 'dry_run'
    assert result['tasks']['redpacket']['result']['challenge_action']['status'] == 'dry_run'
    assert result['summary']['post_call_count'] == 0
    assert all(call[1] != '/red-packets/packet-id/join' for call in client.calls)


def test_safe_backtest_main_supports_include_redpacket_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _settings(tmp_path)
    called = {}

    def _fake_run(settings, store, *, client=None, include_redpacket=False):
        called['include_redpacket'] = include_redpacket
        return {
            'tasks': {
                'redpacket': {'ok': True, 'result': {'status': 'dry_run'}},
                'publish_external': {'ok': True, 'result': {'summary': {'published': 0, 'dry_run': True}}},
                'publish_submission_execute': {'ok': True, 'result': {'summary': {'submitted': 0, 'dry_run': True}}},
            },
            'http_calls': [],
            'summary': {'task_count': 1, 'error_count': 0, 'errors': {}, 'get_call_count': 0, 'post_call_count': 0, 'patch_call_count': 0},
        }

    monkeypatch.setattr(safe_backtest, 'run_safe_backtest', _fake_run)
    monkeypatch.setattr(sys, 'argv', ['safe_backtest.py', '--include-redpacket'])

    assert safe_backtest.main() == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert called['include_redpacket'] is True
    assert parsed['tasks']['redpacket']['result']['status'] == 'dry_run'
