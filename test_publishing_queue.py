from pathlib import Path

import httpx

from config import load_settings
from state import JsonStateStore
import tasks.publish_submit_bridge as publish_submit_bridge
import tasks.publish_submission_execute as publish_submission_execute
from tasks.publishing_queue import run as queue_run
from tasks.publish_external import run as external_run
from tasks.publish_submit_bridge import run as bridge_run
from tasks.publish_submission_execute import run as submit_run


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
AGENTHANSA_ENABLE_PUBLISH_PIPELINE: true
AGENTHANSA_PUBLISH_QUEUE_LIMIT: 8
AGENTHANSA_DEVTO_API_KEY: devto-test-key
AGENTHANSA_X_AUTH_TOKEN: x-auth-token
AGENTHANSA_X_CT0: x-ct0-token
""".strip(),
        encoding='utf-8',
    )
    return load_settings(str(config_path))


def test_publishing_queue_creates_entries_for_external_publish_tasks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('quests', {
        'buckets': {
            'auto_candidate': [
                {
                    'id': 'q-twitter-draft',
                    'title': 'Write 5 high-quality X/Twitter post drafts for @futurmix account',
                    '_priority_score': 80,
                    '_classification': {'proof_likely': False, 'require_proof': False, 'risk_flags': []},
                }
            ],
            'manual_or_proof_required': [
                {
                    'id': 'q-devto',
                    'title': 'Write a blog post mentioning FuturMix AI gateway (futurmix.ai)',
                    '_priority_score': 120,
                    '_classification': {'proof_likely': True, 'require_proof': True, 'risk_flags': ['proof_required_or_likely']},
                }
            ],
            'review_manually': [],
        }
    })

    result = queue_run(settings, store)

    assert result['summary']['queued'] == 2
    by_quest = {item['quest_id']: item for item in result['items']}
    assert by_quest['q-twitter-draft']['platform'] == 'twitter'
    assert by_quest['q-twitter-draft']['publish_required'] is False
    assert by_quest['q-twitter-draft']['status'] == 'draft_needed'
    assert by_quest['q-devto']['platform'] == 'devto'
    assert by_quest['q-devto']['publish_required'] is True
    assert by_quest['q-devto']['status'] == 'publish_pending'


def test_publishing_queue_writes_archetype_prompt_and_local_draft_for_hostable_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('quests', {
        'buckets': {
            'auto_candidate': [
                {
                    'id': 'q-research',
                    'title': 'Find 10 AI-first companies that should be using Topify.ai but are not yet (docs)',
                    'description': 'Research live companies and explain why they fit.',
                    'goal': 'Return a structured research memo with actionable notes.',
                    '_priority_score': 95,
                    '_classification': {
                        'proof_likely': False,
                        'require_proof': False,
                        'proof_strategy': 'paste_rs_or_doc',
                        'proof_hostable_text': True,
                        'archetype': 'research_analyst',
                        'risk_flags': ['proof_hostable_text'],
                    },
                }
            ],
            'manual_or_proof_required': [],
            'review_manually': [],
        }
    })

    result = queue_run(settings, store)

    assert result['summary']['queued'] == 1
    item = result['items'][0]
    assert item['proof_strategy'] == 'paste_rs_or_doc'
    assert item['archetype'] == 'research_analyst'
    assert item['draft_path']
    assert item['system_prompt'].lower().startswith('you are a research analyst')
    assert 'Topify.ai' in item['user_prompt']
    draft_text = Path(item['draft_path']).read_text(encoding='utf-8')
    assert 'Archetype: research_analyst' in draft_text
    assert 'Goal: Return a structured research memo with actionable notes.' in draft_text


def test_publishing_queue_preserves_existing_item_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('publish_queue', {
        'items': [
            {
                'queue_id': 'publish::q-devto::devto',
                'quest_id': 'q-devto',
                'platform': 'devto',
                'status': 'published',
                'published_url': 'https://dev.to/example/post',
            }
        ]
    })
    store.save('quests', {
        'buckets': {
            'auto_candidate': [],
            'manual_or_proof_required': [
                {
                    'id': 'q-devto',
                    'title': 'Write a blog post mentioning FuturMix AI gateway (futurmix.ai)',
                    '_priority_score': 120,
                    '_classification': {'proof_likely': True, 'require_proof': True, 'risk_flags': ['proof_required_or_likely']},
                }
            ],
            'review_manually': [],
        }
    })

    result = queue_run(settings, store)

    assert result['summary']['queued'] == 1
    assert result['items'][0]['status'] == 'published'
    assert result['items'][0]['published_url'] == 'https://dev.to/example/post'


def test_publishing_queue_includes_title_for_existing_state_items(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('publish_queue', {
        'items': [
            {
                'queue_id': 'publish::q-devto::devto',
                'quest_id': 'q-devto',
                'platform': 'devto',
                'status': 'published',
                'published_url': 'https://dev.to/example/post',
            }
        ]
    })
    store.save('quests', {
        'buckets': {
            'auto_candidate': [],
            'manual_or_proof_required': [
                {
                    'id': 'q-devto',
                    'title': 'Write a blog post mentioning FuturMix AI gateway (futurmix.ai)',
                    '_priority_score': 120,
                    '_classification': {'proof_likely': True, 'require_proof': True, 'risk_flags': ['proof_required_or_likely']},
                }
            ],
            'review_manually': [],
        }
    })

    result = queue_run(settings, store)

    assert result['items'][0]['title'] == 'Write a blog post mentioning FuturMix AI gateway (futurmix.ai)'
    saved = store.load('publish_queue')
    assert saved['items'][0]['title'] == 'Write a blog post mentioning FuturMix AI gateway (futurmix.ai)'


def test_publish_submit_bridge_extracts_submission_ready_entries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    store.save('publish_queue', {
        'items': [
            {
                'queue_id': 'publish::q-devto::devto',
                'quest_id': 'q-devto',
                'title': 'Write a blog post mentioning FuturMix AI gateway (futurmix.ai)',
                'platform': 'devto',
                'status': 'published',
                'publish_required': True,
                'published_url': 'https://dev.to/example/post',
                'proof_url': None,
            },
            {
                'queue_id': 'publish::q-twitter::twitter',
                'quest_id': 'q-twitter',
                'title': 'Write 5 high-quality X/Twitter post drafts for @futurmix account',
                'platform': 'twitter',
                'status': 'draft_needed',
                'publish_required': False,
                'published_url': None,
                'proof_url': None,
            },
        ]
    })

    result = bridge_run(settings, store)

    assert result['summary']['submission_ready'] == 1
    ready = result['items'][0]
    assert ready['quest_id'] == 'q-devto'
    assert ready['proof_url'] == 'https://dev.to/example/post'
    saved = store.load('publish_submit_bridge')
    assert saved['summary']['submission_ready'] == 1


def test_publish_submit_bridge_uploads_hostable_text_draft_to_paste_rs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    draft_path = tmp_path / 'reports' / 'drafts' / 'q-research.md'
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text('# Research draft\n\nSpecific original content.', encoding='utf-8')
    store.save('publish_queue', {
        'items': [
            {
                'queue_id': 'publish::q-research::docs',
                'quest_id': 'q-research',
                'title': 'Find 10 AI-first companies that should be using Topify.ai but are not yet',
                'platform': 'docs',
                'status': 'draft_ready',
                'publish_required': False,
                'proof_strategy': 'paste_rs_or_doc',
                'draft_path': str(draft_path),
                'proof_url': None,
                'published_url': None,
            }
        ]
    })

    class _Resp:
        text = 'https://paste.rs/abc123\n'

        def raise_for_status(self):
            return None

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, content=None, headers=None):
            assert url == 'https://paste.rs'
            assert 'Specific original content.' in content
            assert headers['Content-Type'] == 'text/plain; charset=utf-8'
            return _Resp()

    monkeypatch.setattr(publish_submit_bridge.httpx, 'Client', lambda *args, **kwargs: _Client())

    result = bridge_run(settings, store)

    assert result['summary']['submission_ready'] == 1
    ready = result['items'][0]
    assert ready['quest_id'] == 'q-research'
    assert ready['proof_url'] == 'https://paste.rs/abc123'
    saved_queue = store.load('publish_queue')
    assert saved_queue['items'][0]['proof_url'] == 'https://paste.rs/abc123'


def test_publish_submit_bridge_keeps_hostable_text_waiting_when_paste_upload_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    draft_path = tmp_path / 'reports' / 'drafts' / 'q-research.md'
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text('# Research draft\n\nSpecific original content.', encoding='utf-8')
    store.save('publish_queue', {
        'items': [
            {
                'queue_id': 'publish::q-research::docs',
                'quest_id': 'q-research',
                'title': 'Find 10 AI-first companies that should be using Topify.ai but are not yet',
                'platform': 'docs',
                'status': 'draft_ready',
                'publish_required': False,
                'proof_strategy': 'paste_rs_or_doc',
                'draft_path': str(draft_path),
                'proof_url': None,
                'published_url': None,
            }
        ]
    })

    def _boom(self, url, content=None, headers=None):
        request = httpx.Request('POST', 'https://paste.rs')
        response = httpx.Response(503, request=request, text='temporary failure')
        raise httpx.HTTPStatusError('temporary failure', request=request, response=response)

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        post = _boom

    monkeypatch.setattr(publish_submit_bridge.httpx, 'Client', lambda *args, **kwargs: _Client())

    result = bridge_run(settings, store)

    assert result['summary']['submission_ready'] == 0
    assert result['summary']['waiting_for_publish'] == 1
    waiting = result['items'][0]
    assert waiting['status'] == 'waiting_for_publish'
    assert waiting['proof_url'] is None


def test_publish_external_publishes_devto_and_updates_queue(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    draft_path = tmp_path / 'reports' / 'drafts' / 'q-devto.md'
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text('# Draft for quest\n\n## Draft content\n\nHello devto body.', encoding='utf-8')
    store.save('publish_queue', {
        'items': [
            {
                'queue_id': 'publish::q-devto::devto',
                'quest_id': 'q-devto',
                'title': 'Write a blog post mentioning FuturMix AI gateway (futurmix.ai)',
                'platform': 'devto',
                'status': 'publish_pending',
                'publish_required': True,
                'draft_path': str(draft_path),
                'proof_url': None,
                'published_url': None,
                'notes': [],
            }
        ]
    })

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {'id': 123, 'url': 'https://dev.to/test/post'}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json=None):
            assert url == 'https://dev.to/api/articles'
            article = json['article']
            assert article['published'] is True
            assert 'Hello devto body.' in article['body_markdown']
            return _Resp()

    monkeypatch.setattr('tasks.publish_external.httpx.Client', lambda *args, **kwargs: _Client())

    result = external_run(settings, store)

    assert result['summary']['published'] == 1
    saved = store.load('publish_queue')
    item = saved['items'][0]
    assert item['status'] == 'published'
    assert item['published_url'] == 'https://dev.to/test/post'
    assert item['proof_url'] == 'https://dev.to/test/post'


def test_publish_external_publishes_twitter_with_length_limit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    draft_path = tmp_path / 'reports' / 'drafts' / 'q-twitter.md'
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text('# Draft for quest\n\n## Draft content\n\n' + ('A' * 400), encoding='utf-8')
    store.save('publish_queue', {
        'items': [
            {
                'queue_id': 'publish::q-twitter::twitter',
                'quest_id': 'q-twitter',
                'title': 'Write 5 high-quality X/Twitter post drafts for @futurmix account',
                'platform': 'twitter',
                'status': 'publish_pending',
                'publish_required': True,
                'draft_path': str(draft_path),
                'proof_url': None,
                'published_url': None,
                'notes': [],
            }
        ]
    })

    captured = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {'data': {'create_tweet': {'tweet_results': {'result': {'rest_id': '999', 'core': {'user_results': {'result': {'legacy': {'screen_name': 'futurmix'}}}}}}}}}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json=None, content=None):
            captured['url'] = url
            captured['json'] = json
            captured['content'] = content
            return _Resp()

    monkeypatch.setattr('tasks.publish_external.httpx.Client', lambda *args, **kwargs: _Client())

    result = external_run(settings, store)

    assert result['summary']['published'] == 1
    assert captured['url'].endswith('/CreateTweet')
    assert '/i/api/graphql/' in captured['url']
    assert captured['json']['queryId']
    assert len(captured['json']['variables']['tweet_text']) <= 280
    assert captured['json']['variables']['dark_request'] is False
    saved = store.load('publish_queue')
    item = saved['items'][0]
    assert item['status'] == 'published'
    assert item['published_url'] == 'https://x.com/futurmix/status/999'


def test_publish_submission_execute_submits_ready_item_with_proof_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    draft_path = tmp_path / 'reports' / 'drafts' / 'q-devto.md'
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text('# Draft for quest\n\n## Draft content\n\nFinal submission body.', encoding='utf-8')
    store.save('publish_queue', {
        'items': [
            {
                'queue_id': 'publish::q-devto::devto',
                'quest_id': 'q-devto',
                'title': 'Write a blog post mentioning FuturMix AI gateway (futurmix.ai)',
                'platform': 'devto',
                'status': 'published',
                'publish_required': True,
                'draft_path': str(draft_path),
                'published_url': 'https://dev.to/example/post',
                'proof_url': 'https://dev.to/example/post',
                'notes': [],
            }
        ]
    })
    store.save('publish_submit_bridge', {
        'items': [
            {
                'queue_id': 'publish::q-devto::devto',
                'quest_id': 'q-devto',
                'platform': 'devto',
                'status': 'submission_ready',
                'proof_url': 'https://dev.to/example/post',
            }
        ],
        'summary': {'submission_ready': 1, 'waiting_for_publish': 0},
    })

    calls = []

    class _Client:
        def post(self, path, json=None, **kwargs):
            calls.append((path, json, kwargs))
            return {
                'submission_id': 'sub-123',
                'updated': False,
                'revision': 1,
                'revisions_remaining': 4,
                'message': 'submitted ok',
            }

    result = submit_run(settings, _Client(), store)

    assert result['summary']['submitted'] == 1
    assert calls == [(
        '/alliance-war/quests/q-devto/submit',
        {'content': 'Final submission body.', 'proof_url': 'https://dev.to/example/post'},
        {},
    )]
    saved_queue = store.load('publish_queue')
    saved_item = saved_queue['items'][0]
    assert saved_item['status'] == 'submitted'
    assert saved_item['submission_id'] == 'sub-123'
    assert saved_item['revisions_remaining'] == 4


def test_publish_submission_execute_records_revision_limit_and_blocks_retry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    draft_path = tmp_path / 'reports' / 'drafts' / 'q-devto.md'
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text('# Draft for quest\n\n## Draft content\n\nFinal submission body.', encoding='utf-8')
    store.save('publish_queue', {
        'items': [
            {
                'queue_id': 'publish::q-devto::devto',
                'quest_id': 'q-devto',
                'title': 'Write a blog post mentioning FuturMix AI gateway (futurmix.ai)',
                'platform': 'devto',
                'status': 'published',
                'publish_required': True,
                'draft_path': str(draft_path),
                'proof_url': 'https://dev.to/example/post',
                'notes': [],
            }
        ]
    })
    bridge_state = {
        'items': [
            {
                'queue_id': 'publish::q-devto::devto',
                'quest_id': 'q-devto',
                'platform': 'devto',
                'status': 'submission_ready',
                'proof_url': 'https://dev.to/example/post',
            }
        ],
        'summary': {'submission_ready': 1, 'waiting_for_publish': 0},
    }
    store.save('publish_submit_bridge', bridge_state)

    def _raise_limit(path, json=None, **kwargs):
        request = httpx.Request('POST', f'https://example.invalid{path}')
        response = httpx.Response(429, request=request, text='Maximum 5 revisions per submission. Make each one count.')
        raise httpx.HTTPStatusError('revision limit', request=request, response=response)

    class _Client:
        post = staticmethod(_raise_limit)

    first = submit_run(settings, _Client(), store)
    assert first['summary']['submitted'] == 0
    assert first['summary']['revision_filtered'] == 1
    assert first['items'][0]['status'] == 'revision_limit'
    limits = store.load('submission_revision_limits')
    assert limits['q-devto']['revision_exhausted'] is True
    saved_queue = store.load('publish_queue')
    assert saved_queue['items'][0]['status'] == 'revision_exhausted'

    class _NeverCalledClient:
        def post(self, path, json=None, **kwargs):
            raise AssertionError('should not retry after revision limit is recorded')

    store.save('publish_submit_bridge', bridge_state)
    second = submit_run(settings, _NeverCalledClient(), store)
    assert second['summary']['revision_filtered'] == 1
    assert second['items'][0]['status'] == 'filtered_revision_limit'
