from tasks.quests import _classify_quest


def test_classify_quest_marks_proof_heavy_twitter_task_manual():
    score, meta = _classify_quest({
        'title': 'Tweet about the AgentHansa skill on ClawHub that lets AI agents earn money',
        'reward': '$150.00',
        'require_proof': True,
        'status': 'not_submitted',
        'urgency': 'closing_soon',
    })
    assert score > 0
    assert meta['bucket'] == 'manual_or_proof_required'
    assert meta['proof_strategy'] == 'published_url_required'
    assert 'proof_required_or_likely' in meta['risk_flags']


def test_classify_quest_marks_text_draft_task_manual_publish_pipeline():
    score, meta = _classify_quest({
        'title': 'Write 5 high-quality X/Twitter post drafts for @futurmix account',
        'reward': '$40.00',
        'require_proof': False,
        'status': 'open',
        'urgency': 'closing_soon',
        'goal': 'Write drafts for a Twitter account with a live URL if published later',
    })
    assert score > 0
    assert meta['bucket'] == 'manual_or_proof_required'
    assert meta['proof_strategy'] == 'published_url_required'
    assert meta['archetype'] == 'general_text'


def test_classify_quest_marks_company_research_as_hostable_text_with_research_archetype():
    score, meta = _classify_quest({
        'title': "Find 10 AI-first companies that should be using Topify.ai but aren't",
        'reward': '$40.00',
        'require_proof': False,
        'status': 'open',
        'urgency': 'closing_soon',
        'description': 'Research companies and provide actionable leads, not a generic list.',
    })
    assert score > 0
    assert meta['proof_strategy'] == 'paste_rs_or_doc'
    assert meta['proof_hostable_text'] is True
    assert meta['archetype'] == 'research_analyst'


def test_classify_quest_marks_tutorial_as_hostable_text_with_devrel_archetype():
    score, meta = _classify_quest({
        'title': 'Write a technical tutorial: Build your first AI agent on AgentHansa in 10 minutes',
        'reward': '$50.00',
        'require_proof': False,
        'status': 'open',
        'goal': 'Write a step-by-step tutorial with working code and practical instructions',
    })
    assert score > 0
    assert meta['proof_strategy'] == 'paste_rs_or_doc'
    assert meta['archetype'] == 'developer_educator'
