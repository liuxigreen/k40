import httpx

from tasks.redpacket import _comment_quality_check, _complete_required_action, _extract_numbers, _generate_forum_comment_body, _generate_forum_post_payload, _solve_question_local, _solve_question_llm, run
from state import JsonStateStore


class _NotifyStub:
    def __init__(self):
        self.calls = []

    def __call__(self, settings, store, result):
        self.calls.append(result)
        return True


class _DummyClient:
    def __init__(self, overview, challenge=None, join_response=None, join_error=None, post_failures=None):
        self.overview = overview
        self.challenge = challenge or {'question': 'What is the sum of four and 4 keys?'}
        self.join_response = join_response or {'ok': True}
        self.join_error = join_error
        self.post_failures = post_failures or {}
        self.calls = []
        self.posts = []

    def get(self, path: str):
        self.calls.append(path)
        if path == '/red-packets':
            return self.overview
        if path.startswith('/red-packets/') and path.endswith('/challenge'):
            return self.challenge
        raise AssertionError(f'unexpected path: {path}')

    def post(self, path: str, json=None):
        self.posts.append((path, json))
        failure = self.post_failures.get(path)
        if failure is not None:
            raise failure
        if path.startswith('/red-packets/') and path.endswith('/join'):
            if self.join_error is not None:
                raise self.join_error
            return self.join_response
        if path.startswith('/forum/') and path.endswith('/comments'):
            return {'ok': True}
        if path.startswith('/forum/') and path.endswith('/vote'):
            return {'ok': True}
        if path == '/forum':
            return {'ok': True}
        raise AssertionError(f'unexpected post path: {path}')



def test_extract_numbers_supports_word_numbers():
    nums = _extract_numbers('What is the sum of four and 4 keys?')
    assert 4 in nums
    assert nums.count(4) >= 2


def test_local_solver_handles_sum_with_word_number():
    assert _solve_question_local('What is the sum of four and 4 keys?') == '8'


def test_local_solver_handles_double_plus_more():
    assert _solve_question_local('A parrot doubles its 5 stars and then finds 5 more. How many total?') == '15'


def test_local_solver_handles_even_split():
    assert _solve_question_local('28 tickets split evenly among 4 parrots') == '7'


def test_local_solver_handles_half():
    assert _solve_question_local('A astronaut collects nine badges in the morning and three in the afternoon, then shares half with a friend.') == '6'


def test_run_clears_stale_manual_state_when_no_active_packet(tmp_path, monkeypatch):
    notify = _NotifyStub()
    monkeypatch.setattr('tasks.redpacket.maybe_notify_redpacket', notify)
    monkeypatch.setattr('tasks.redpacket.load_settings', lambda: object())
    store = JsonStateStore(tmp_path)
    store.save(
        'redpacket_state',
        {
            'status': 'manual_required',
            'reason': 'could_not_safely_solve_question',
            'packet': {'id': 'old-packet', 'title': 'Old packet'},
            'challenge': {'question': 'What is the sum of four and 4 keys?'},
            'answer_preview': None,
        },
    )
    client = _DummyClient(
        {
            'active': [],
            'next_packet_at': '2026-04-16T15:27:30.067648+00:00',
            'next_packet_seconds': 4266,
        }
    )

    result = run(client, store, dry_run=True)
    saved = store.load('redpacket_state')

    assert result['joined'] is False
    assert saved['overview']['active'] == []
    assert 'packet' not in saved
    assert 'challenge' not in saved
    assert 'answer_preview' not in saved
    assert saved.get('status') != 'manual_required'
    assert saved.get('reason') != 'could_not_safely_solve_question'
    assert len(notify.calls) == 1


def test_run_notifies_after_successful_join(tmp_path, monkeypatch):
    notify = _NotifyStub()
    monkeypatch.setattr('tasks.redpacket.maybe_notify_redpacket', notify)
    monkeypatch.setattr('tasks.redpacket.load_settings', lambda: object())
    monkeypatch.setattr('tasks.redpacket._complete_required_action', lambda client, packet, packet_state, dry_run: {'status': 'completed', 'action': 'noop'})
    store = JsonStateStore(tmp_path)
    client = _DummyClient(
        {
            'active': [{'id': 'packet-1', 'title': 'Lucky Packet', 'challenge_description': 'math puzzle'}],
            'next_packet_at': '2026-04-16T18:27:33.335175+00:00',
            'next_packet_seconds': 5817,
        }
    )

    result = run(client, store, dry_run=False)
    saved = store.load('redpacket_state')

    assert result['status'] == 'joined'
    assert result['joined'] is True
    assert client.posts == [('/red-packets/packet-1/join', {'answer': '8'})]
    assert saved['last_joined_packet_id'] == 'packet-1'
    assert len(notify.calls) == 1
    assert notify.calls[0]['status'] == 'joined'


def test_run_handles_join_http_400_without_crashing(tmp_path, monkeypatch):
    notify = _NotifyStub()
    monkeypatch.setattr('tasks.redpacket.maybe_notify_redpacket', notify)
    monkeypatch.setattr('tasks.redpacket.load_settings', lambda: object())
    monkeypatch.setattr('tasks.redpacket._complete_required_action', lambda client, packet, packet_state, dry_run: {'status': 'completed', 'action': 'noop'})

    request = httpx.Request('POST', 'https://www.agenthansa.com/api/red-packets/packet-2/join')
    response = httpx.Response(400, request=request, text='{"message":"already joined or invalid answer"}')
    join_error = httpx.HTTPStatusError('join failed', request=request, response=response)

    store = JsonStateStore(tmp_path)
    client = _DummyClient(
        {
            'active': [{'id': 'packet-2', 'title': 'Problem Packet', 'challenge_description': 'math puzzle'}],
            'next_packet_at': '2026-04-16T21:27:33.335175+00:00',
            'next_packet_seconds': 5817,
        },
        join_error=join_error,
    )

    result = run(client, store, dry_run=False)
    saved = store.load('redpacket_state')

    assert result['status'] == 'manual_required'
    assert result['reason'] == 'join_request_rejected'
    assert result['join_error']['status_code'] == 400
    assert 'already joined or invalid answer' in result['join_error']['body']
    assert saved['status'] == 'manual_required'
    assert saved['reason'] == 'join_request_rejected'
    assert saved['join_error']['status_code'] == 400
    assert len(notify.calls) == 1
    assert notify.calls[0]['reason'] == 'join_request_rejected'


def test_complete_required_action_uses_vote_payload_for_upvote(tmp_path):
    store = JsonStateStore(tmp_path)
    client = _DummyClient(
        {
            'active': [{'id': 'packet-vote', 'title': 'Upvote a forum post', 'challenge_description': 'Upvote a forum post: POST /api/forum/{post_id}/vote with {"vote": "up"}. Get post IDs from GET /api/forum. Then join this red packet.'}],
            'next_packet_at': '2026-04-16T21:27:33.335175+00:00',
            'next_packet_seconds': 5817,
        }
    )
    original_get = client.get
    client.get = lambda path: {'posts': [{'id': 'post-123'}]} if path == '/forum?sort=recent&limit=5' else original_get(path)
    client.challenge = {'question': 'What is 2 + 2?'}

    result = run(client, store, dry_run=False)

    assert ('/forum/post-123/vote', {'vote': 'up'}) in client.posts
    assert result['status'] == 'joined'
    assert client.posts[-1] == ('/red-packets/packet-vote/join', {'answer': '4'})


def test_ambiguous_forum_task_prefers_comment_over_post(tmp_path, monkeypatch):
    monkeypatch.setattr('tasks.redpacket._generate_forum_comment_body', lambda _hint: '这是一条更自然的优质评论，先完成前置动作，再核对返回结果，能减少无效重试。')
    store = JsonStateStore(tmp_path)
    client = _DummyClient(
        {
            'active': [{
                'id': 'packet-comment',
                'title': 'Forum post comment task',
                'challenge_description': 'Comment on a forum post. You may see forum post wording, but the required action is to add a comment via POST /api/forum/{post_id}/comments before joining.'
            }],
            'next_packet_at': '2026-04-16T21:27:33.335175+00:00',
            'next_packet_seconds': 5817,
        }
    )
    original_get = client.get
    client.get = lambda path: {'posts': [{'id': 'post-456'}]} if path == '/forum?sort=recent&limit=5' else original_get(path)
    client.challenge = {'question': 'What is 3 + 2?'}

    result = run(client, store, dry_run=False)

    assert ('/forum/post-456/comments', {'body': '这是一条更自然的优质评论，先完成前置动作，再核对返回结果，能减少无效重试。'}) in client.posts
    assert all(path != '/forum' for path, _payload in client.posts)
    assert result['challenge_action']['action'] == 'comment'
    assert result['status'] == 'joined'
    assert client.posts[-1] == ('/red-packets/packet-comment/join', {'answer': '5'})


def test_complete_required_action_retries_next_post_after_vote_conflict():
    request = httpx.Request('POST', 'https://www.agenthansa.com/api/forum/post-1/vote')
    response = httpx.Response(409, request=request, text='{"detail":"Already voted"}')
    conflict = httpx.HTTPStatusError('already voted', request=request, response=response)
    client = _DummyClient(
        {'active': []},
        post_failures={'/forum/post-1/vote': conflict},
    )
    original_get = client.get
    client.get = lambda path: {'posts': [{'id': 'post-1'}, {'id': 'post-2'}, {'id': 'post-3'}]} if path == '/forum?sort=recent&limit=5' else original_get(path)
    packet_state = {}
    packet = {
        'id': 'packet-vote-live',
        'title': 'Upvote a forum post',
        'challenge_description': 'Upvote a forum post: POST /api/forum/{post_id}/vote with {"vote": "up"}.',
    }

    result = _complete_required_action(client, packet, packet_state, dry_run=False)

    assert result['status'] == 'completed'
    assert result['post_id'] == 'post-2'
    assert client.posts[:2] == [
        ('/forum/post-1/vote', {'vote': 'up'}),
        ('/forum/post-2/vote', {'vote': 'up'}),
    ]
    assert packet_state['action_completed_for_packet_id'] == 'packet-vote-live'
    assert packet_state['last_action_type'] == 'vote'


def test_complete_required_action_reports_no_voteable_post_after_all_conflicts():
    request1 = httpx.Request('POST', 'https://www.agenthansa.com/api/forum/post-1/vote')
    response1 = httpx.Response(409, request=request1, text='{"detail":"Already voted"}')
    request2 = httpx.Request('POST', 'https://www.agenthansa.com/api/forum/post-2/vote')
    response2 = httpx.Response(409, request=request2, text='{"detail":"Already voted"}')
    client = _DummyClient(
        {'active': []},
        post_failures={
            '/forum/post-1/vote': httpx.HTTPStatusError('already voted', request=request1, response=response1),
            '/forum/post-2/vote': httpx.HTTPStatusError('already voted', request=request2, response=response2),
        },
    )
    original_get = client.get
    client.get = lambda path: {'posts': [{'id': 'post-1'}, {'id': 'post-2'}]} if path == '/forum?sort=recent&limit=5' else original_get(path)

    try:
        _complete_required_action(
            client,
            {
                'id': 'packet-vote-none',
                'title': 'Upvote a forum post',
                'challenge_description': 'Upvote a forum post: POST /api/forum/{post_id}/vote with {"vote": "up"}.',
            },
            {},
            dry_run=False,
        )
    except RuntimeError as exc:
        assert str(exc) == 'no_voteable_post_found'
    else:
        raise AssertionError('expected no_voteable_post_found')


def test_dry_run_does_not_call_external_solver(monkeypatch, tmp_path):
    store = JsonStateStore(tmp_path)
    client = _DummyClient(
        {
            'active': [{'id': 'packet-dry', 'title': 'Upvote a forum post', 'challenge_description': 'Upvote a forum post: POST /api/forum/{post_id}/vote with {"vote": "up"}.'}],
            'next_packet_at': '2026-04-16T21:27:33.335175+00:00',
            'next_packet_seconds': 5817,
        },
        challenge={'question': 'What is 7 + 5?'},
    )
    original_get = client.get
    client.get = lambda path: {'posts': [{'id': 'post-777'}]} if path == '/forum?sort=recent&limit=5' else original_get(path)
    monkeypatch.setattr('tasks.redpacket._solve_question_local', lambda _question: '12')
    monkeypatch.setattr('tasks.redpacket._solve_question_llm', lambda _question: (_ for _ in ()).throw(AssertionError('llm should not be called in dry run when local solver succeeds')))

    result = run(client, store, dry_run=True)

    assert result['status'] == 'dry_run'
    assert client.posts == []
    assert result['challenge_action']['status'] == 'dry_run'
    assert result['challenge_action']['action'] == 'vote'


def test_external_solver_fallback_returns_none_without_config(monkeypatch):
    monkeypatch.setattr('tasks.redpacket._load_deepseek_config', lambda: None)
    assert _solve_question_llm('What is 1 + 1?') is None


def test_generate_forum_comment_body_uses_deepseek_when_available(monkeypatch):
    monkeypatch.setattr('tasks.redpacket._load_deepseek_config', lambda: {'url': 'https://example.invalid/v1/chat/completions', 'keys': ['k1'], 'model': 'DeepSeek-V3.2'})

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {'choices': [{'message': {'content': '这条评论聚焦真实执行经验，先完成前置动作，再核对返回结果，最后提交，能减少无效重试。'}}]}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json):
            assert json['model'] == 'DeepSeek-V3.2'
            assert 'forum reply' in json['messages'][1]['content'].lower()
            return _Resp()

    monkeypatch.setattr('tasks.redpacket.httpx.Client', lambda *args, **kwargs: _Client())

    body = _generate_forum_comment_body('How should I handle short-lived task windows?')

    assert '真实执行经验' in body
    assert len(body) >= 20


def test_comment_quality_check_rejects_generic_short_praise():
    ok, reason = _comment_quality_check('不错，支持一下，加油')
    assert ok is False
    assert reason == 'too_short'


def test_comment_quality_check_accepts_substantive_comment():
    ok, reason = _comment_quality_check('我更认同先完成前置动作再核对接口返回，这样能减少短窗口任务里的无效重试，也更容易定位失败原因。')
    assert ok is True
    assert reason is None


def test_generate_forum_comment_body_returns_none_without_deepseek(monkeypatch):
    monkeypatch.setattr('tasks.redpacket._load_deepseek_config', lambda: None)
    assert _generate_forum_comment_body('forum task') is None


def test_generate_forum_comment_body_rejects_low_quality_deepseek_output(monkeypatch):
    monkeypatch.setattr('tasks.redpacket._deepseek_text_completion', lambda *args, **kwargs: '不错，支持一下，加油')
    assert _generate_forum_comment_body('forum task') is None


def test_comment_action_requires_safe_generated_comment(tmp_path, monkeypatch):
    monkeypatch.setattr('tasks.redpacket._generate_forum_comment_body', lambda _hint: None)
    store = JsonStateStore(tmp_path)
    client = _DummyClient(
        {
            'active': [{
                'id': 'packet-comment-safe',
                'title': 'Forum post comment task',
                'challenge_description': 'Comment on a forum post before joining.'
            }],
            'next_packet_at': '2026-04-16T21:27:33.335175+00:00',
            'next_packet_seconds': 5817,
        }
    )
    original_get = client.get
    client.get = lambda path: {'posts': [{'id': 'post-456'}]} if path == '/forum?sort=recent&limit=5' else original_get(path)
    client.challenge = {'question': 'What is 3 + 2?'}

    result = run(client, store, dry_run=False)

    assert result['status'] == 'manual_required'
    assert result['reason'] == 'no_safe_comment_generator'
    assert client.posts == []


def test_generate_forum_post_payload_falls_back_when_deepseek_unavailable(monkeypatch):
    monkeypatch.setattr('tasks.redpacket._load_deepseek_config', lambda: None)

    payload = _generate_forum_post_payload('Why reliable sequencing matters')

    assert payload['category'] == 'strategy'
    assert 'Reliable task sequencing' in payload['title']
    assert 'complete the prerequisite action first' in payload['body']
