from pathlib import Path

import httpx

from config import load_settings
from state import JsonStateStore
import tasks.forum_curation as forum_curation


def _daily_quests(curate_progress=None, curate_completed=False):
    return {
        'quests': [
            {'id': 'checkin', 'name': 'Check In', 'completed': True},
            {'id': 'create', 'name': 'Create Content', 'completed': True},
            {
                'id': 'curate',
                'name': 'Curate',
                'description': 'Vote on 10 posts (5 up + 5 down)',
                'completed': curate_completed,
                'progress': curate_progress,
            },
        ]
    }


class DummyClient:
    def __init__(self, posts, failures=None):
        self.posts = posts
        self.failures = failures or {}
        self.calls = []
        self.votes = []

    def get(self, path: str):
        self.calls.append(("get", path))
        if path in {
            '/forum?sort=recent&limit=30',
            '/forum?sort=recent&limit=100',
            '/forum?sort=recent&limit=100&page=1',
            '/forum?sort=recent&limit=100&page=2',
            '/forum?sort=recent&limit=100&page=3',
            '/forum?sort=recent&limit=100&page=4',
            '/forum?sort=recent&limit=100&page=5',
        }:
            return {'posts': self.posts}
        if path == '/agents/daily-quests':
            return _daily_quests()
        raise AssertionError(f'unexpected path: {path}')

    def post(self, path: str, json=None, **kwargs):
        self.calls.append(("post", path, json))
        self.votes.append((path, json))
        failure = self.failures.get(path)
        if failure:
            raise failure
        return {'ok': True}


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
AGENTHANSA_ENABLE_FORUM_AUTOMATION: true
""".strip(),
        encoding='utf-8',
    )
    return load_settings(str(config_path))


def test_forum_curation_votes_remaining_up_and_down_targets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('daily_xp', {
        'data': {
            'breakdown': {
                'forum upvote': {'events': 2, 'points': 2},
                'forum downvote': {'events': 1, 'points': 1},
            }
        }
    })
    client = DummyClient([
        {'id': 'p1'}, {'id': 'p2'}, {'id': 'p3'}, {'id': 'p4'}, {'id': 'p5'},
        {'id': 'p6'}, {'id': 'p7'}, {'id': 'p8'}, {'id': 'p9'}, {'id': 'p10'},
    ])

    result = forum_curation.run(settings, client, store, dry_run=False)

    assert result['target_up'] == 5
    assert result['target_down'] == 5
    assert result['needed_up'] == 3
    assert result['needed_down'] == 4
    assert len(result['executed_up']) == 3
    assert len(result['executed_down']) == 4
    assert client.votes[:3] == [
        ('/forum/p1/vote', {'direction': 'up'}),
        ('/forum/p2/vote', {'direction': 'up'}),
        ('/forum/p3/vote', {'direction': 'up'}),
    ]
    assert client.votes[3:] == [
        ('/forum/p4/vote', {'direction': 'down'}),
        ('/forum/p5/vote', {'direction': 'down'}),
        ('/forum/p6/vote', {'direction': 'down'}),
        ('/forum/p7/vote', {'direction': 'down'}),
    ]


def test_forum_curation_skips_when_daily_target_already_met(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('daily_xp', {
        'data': {
            'breakdown': {
                'forum upvote': {'events': 5, 'points': 5},
                'forum downvote': {'events': 5, 'points': 5},
            }
        }
    })
    client = DummyClient([{'id': 'p1'}])

    result = forum_curation.run(settings, client, store, dry_run=False)

    assert result['status'] == 'complete'
    assert result['executed_up'] == []
    assert result['executed_down'] == []
    assert client.votes == []


def test_forum_curation_avoids_duplicate_votes_in_same_snapshot_day(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('daily_xp', {
        'data': {
            'breakdown': {
                'forum upvote': {'events': 0, 'points': 0},
                'forum downvote': {'events': 0, 'points': 0},
            }
        }
    })
    store.save('forum_curation', {
        'day_key': '2026-04-17',
        'voted_post_ids': ['p1', 'p2', 'p3'],
    })
    monkeypatch.setattr('tasks.forum_curation.pst_date_key', lambda now=None: '2026-04-17')
    client = DummyClient([{'id': 'p1'}, {'id': 'p2'}, {'id': 'p3'}, {'id': 'p4'}, {'id': 'p5'}, {'id': 'p6'}])

    result = forum_curation.run(settings, client, store, dry_run=False)

    assert ('/forum/p1/vote', {'direction': 'up'}) not in client.votes
    assert ('/forum/p2/vote', {'direction': 'up'}) not in client.votes
    assert ('/forum/p3/vote', {'direction': 'up'}) not in client.votes
    assert result['executed_up'][0] == 'p4'


def test_forum_curation_handles_409_already_voted_without_crashing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('daily_xp', {
        'data': {
            'breakdown': {
                'forum upvote': {'events': 0, 'points': 0},
                'forum downvote': {'events': 0, 'points': 0},
            }
        }
    })
    request = httpx.Request('POST', 'https://www.agenthansa.com/api/forum/p2/vote')
    response = httpx.Response(409, request=request, text='{"detail":"Already voted"}')
    err = httpx.HTTPStatusError('already voted', request=request, response=response)
    client = DummyClient(
        [{'id': 'p1'}, {'id': 'p2'}, {'id': 'p3'}, {'id': 'p4'}, {'id': 'p5'}, {'id': 'p6'}, {'id': 'p7'}, {'id': 'p8'}, {'id': 'p9'}, {'id': 'p10'}],
        failures={'/forum/p2/vote': err},
    )

    result = forum_curation.run(settings, client, store, dry_run=False)

    assert result['status'] in {'partial', 'complete'}
    assert 'p1' in result['executed_up']
    assert 'p2' in result['skipped_conflicts']
    assert '/forum/p3/vote' in [path for path, _json in client.votes]
    assert len(result['executed_up']) >= 4
    saved = store.load('forum_curation')
    assert 'p2' in saved['voted_post_ids']


def test_forum_curation_fetches_more_candidates_when_recent_posts_are_already_voted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('daily_xp', {
        'data': {
            'breakdown': {
                'forum upvote': {'events': 5, 'points': 5},
                'forum downvote': {'events': 0, 'points': 0},
            }
        }
    })
    store.save('forum_curation', {
        'day_key': '2026-04-17',
        'voted_post_ids': [f'old{i}' for i in range(1, 31)],
    })
    monkeypatch.setattr('tasks.forum_curation.pst_date_key', lambda now=None: '2026-04-17')
    posts = [{'id': f'old{i}'} for i in range(1, 31)] + [{'id': f'fresh{i}'} for i in range(1, 6)]
    client = DummyClient(posts)

    result = forum_curation.run(settings, client, store, dry_run=False)

    assert ('get', '/forum?sort=recent&limit=100&page=1') in client.calls
    assert result['needed_up'] == 0
    assert result['needed_down'] == 5
    assert result['executed_down'] == ['fresh1', 'fresh2', 'fresh3', 'fresh4', 'fresh5']
    assert result['status'] == 'complete'


def test_forum_curation_uses_daily_quests_progress_over_xp_breakdown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('daily_xp', {
        'data': {
            'breakdown': {
                'forum upvote': {'events': 10, 'points': 10},
            }
        }
    })
    store.save('forum_curation', {
        'day_key': '2026-04-17',
        'current_up': 10,
        'current_down': 0,
        'voted_post_ids': [],
    })
    monkeypatch.setattr('tasks.forum_curation.pst_date_key', lambda now=None: '2026-04-17')
    client = DummyClient([{'id': f'p{i}'} for i in range(1, 6)])
    client.get = lambda path: _daily_quests('2/5 up, 5/5 down', False) if path == '/agents/daily-quests' else ({'posts': [{'id': f'p{i}'} for i in range(1, 6)]} if path in {'/forum?sort=recent&limit=30', '/forum?sort=recent&limit=100', '/forum?sort=recent&limit=100&page=1'} else (_ for _ in ()).throw(AssertionError(f'unexpected path: {path}')))

    result = forum_curation.run(settings, client, store, dry_run=False)

    assert result['current_up'] == 2
    assert result['current_down'] == 5
    assert result['needed_up'] == 3
    assert result['needed_down'] == 0
    assert result['executed_up'] == ['p1', 'p2', 'p3']
    assert result['executed_down'] == []
    assert result['status'] == 'complete'


def test_forum_curation_falls_back_to_xp_breakdown_when_daily_quests_unavailable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('daily_xp', {
        'data': {
            'breakdown': {
                'forum upvote': {'events': 2, 'points': 2},
                'forum downvote': {'events': 4, 'points': 4},
            }
        }
    })
    monkeypatch.setattr('tasks.forum_curation.pst_date_key', lambda now=None: '2026-04-17')
    client = DummyClient([{'id': f'p{i}'} for i in range(1, 5)])
    original_get = client.get
    def _get(path):
        if path == '/agents/daily-quests':
            raise AssertionError('daily quests unavailable')
        return original_get(path)
    client.get = _get

    result = forum_curation.run(settings, client, store, dry_run=False)

    assert result['current_up'] == 2
    assert result['current_down'] == 4
    assert result['needed_up'] == 3
    assert result['needed_down'] == 1
    assert result['status'] in {'partial', 'complete'}
