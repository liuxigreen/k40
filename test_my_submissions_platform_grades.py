from __future__ import annotations

from pathlib import Path

from state import JsonStateStore
from tasks import my_submissions


class _Client:
    def get(self, path: str):
        if path == '/alliance-war/quests/my':
            import httpx
            request = httpx.Request('GET', 'https://www.agenthansa.com/api/alliance-war/quests/my')
            response = httpx.Response(422, request=request, text='{"detail":"uuid parsing"}')
            raise httpx.HTTPStatusError('422', request=request, response=response)
        if path == '/agents/journey':
            return {
                'timeline': [
                    {
                        'event': 'quest_submission',
                        'type': 'alliance_war',
                        'detail': 'Submitted to: POLL: Which tagline best represents AgentHansa? Pick one, tell us why',
                        'timestamp': '2026-04-18T01:48:35+00:00',
                    },
                    {
                        'event': 'quest_submission',
                        'type': 'alliance_war',
                        'detail': 'Submitted to: Write 5 high-quality X/Twitter post drafts for @futurmix account',
                        'timestamp': '2026-04-18T01:55:11+00:00',
                    },
                ]
            }
        if path == '/agents/me':
            return {'id': 'agent-1', 'name': 'finance8006-agent'}
        if path == '/alliance-war/quests':
            return {
                'quests': [
                    {'id': 'q-poll', 'title': 'POLL: Which tagline best represents AgentHansa? Pick one, tell us why'},
                    {'id': 'q-x', 'title': 'Write 5 high-quality X/Twitter post drafts for @futurmix account'},
                ]
            }
        if path == '/alliance-war/quests/q-poll':
            return {'id': 'q-poll', 'status': 'open', 'require_proof': False, 'total_submissions': 80}
        if path == '/alliance-war/quests/q-x':
            return {'id': 'q-x', 'status': 'open', 'require_proof': False, 'total_submissions': 112}
        if path == '/alliance-war/quests/q-poll/submissions':
            return {
                'submissions': [
                    {
                        'id': 'sub-poll',
                        'agent_name': 'finance8006-agent',
                        'ai_grade': 'A',
                        'ai_summary': 'Correctly picks one option.',
                        'is_spam': False,
                        'upvotes': 1,
                        'downvotes': 0,
                        'score': 1,
                    }
                ]
            }
        if path == '/alliance-war/quests/q-x/submissions':
            return {
                'submissions': [
                    {
                        'id': 'sub-x',
                        'agent_name': 'finance8006-agent',
                        'ai_grade': 'A',
                        'ai_summary': 'Excellent submission with 5 compliant drafts.',
                        'is_spam': False,
                        'upvotes': 0,
                        'downvotes': 0,
                        'score': 0,
                    }
                ]
            }
        raise AssertionError(f'unexpected path: {path}')


def test_my_submissions_uses_platform_grade_and_not_journey_text_for_spam(tmp_path: Path):
    store = JsonStateStore(tmp_path)
    result = my_submissions.run(_Client(), store)

    assert result['count'] == 2
    by_title = {row['quest_title']: row for row in result['submissions']}

    poll = by_title['Submitted to: POLL: Which tagline best represents AgentHansa? Pick one, tell us why']
    xdrafts = by_title['Submitted to: Write 5 high-quality X/Twitter post drafts for @futurmix account']

    assert poll['ai_grade'] == 'A'
    assert poll['spam_flagged'] is False
    assert 'spam' not in poll['risk_flags']

    assert xdrafts['ai_grade'] == 'A'
    assert xdrafts['spam_flagged'] is False
    assert 'spam' not in xdrafts['risk_flags']


def test_my_submissions_marks_revision_exhausted_from_local_state(tmp_path: Path):
    store = JsonStateStore(tmp_path)
    store.save('submission_revision_limits', {
        'q-poll': {
            'revision_exhausted': True,
            'note': 'Maximum 5 revisions per submission. Make each one count.',
        }
    })
    result = my_submissions.run(_Client(), store)
    by_title = {row['quest_title']: row for row in result['submissions']}
    poll = by_title['Submitted to: POLL: Which tagline best represents AgentHansa? Pick one, tell us why']

    assert poll['revision_exhausted'] is True
    assert poll['revision_note'] == 'Maximum 5 revisions per submission. Make each one count.'
    assert 'revision_exhausted' in poll['risk_flags']
