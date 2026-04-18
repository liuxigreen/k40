from __future__ import annotations

import logging
import re
from typing import Any

from client import AgentHansaClient
from state import JsonStateStore
from tasks.quest_catalog_cache import load_quest_catalog
from utils.timezone import utc_now


def _money_value(text: str) -> float:
    match = re.search(r'(\d+(?:\.\d+)?)', text.replace(',', ''))
    return float(match.group(1)) if match else 0.0


def _classify_quest(quest: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    score = 0
    reward_text = str(quest.get('reward') or quest.get('reward_amount') or '')
    reward_value = _money_value(reward_text)
    title = str(quest.get('title') or '').lower()
    description = str(quest.get('description') or '').lower()
    goal = str(quest.get('goal') or '').lower()
    full_text = ' '.join(part for part in [title, description, goal] if part)
    urgency = str(quest.get('urgency', '')).lower()
    status = str(quest.get('status', '')).lower()
    require_proof = bool(quest.get('require_proof'))
    requires_human = bool(quest.get('requires_human'))

    if reward_value >= 100:
        score += 50
    elif reward_value >= 40:
        score += 35
    elif reward_value >= 10:
        score += 20

    if 'closing_soon' in urgency:
        score += 25
    if status in {'open', 'not_submitted'}:
        score += 20
    if require_proof:
        score += 10

    publish_required = require_proof or any(token in full_text for token in [
        'publish on', 'published on', 'blog post', 'medium', 'dev.to', 'substack',
        'linkedin', 'twitter', 'tweet', 'x/twitter', 'reddit', 'youtube', 'video',
        'proof url', 'live url', 'real platform'
    ])
    proof_hostable_text = (not publish_required) and any(token in full_text for token in [
        'analysis', 'research', 'competitor', 'compare', 'comparison', 'find ', 'list ',
        'pricing', 'feature', 'tutorial', 'guide', 'template', 'draft', 'tagline', 'poll',
        'reasoning', 'feedback', 'report back', 'step-by-step'
    ])
    manual_needed = publish_required or requires_human or any(token in full_text for token in [
        'design', 'logo', 'outreach', 'wallet', 'contact info', 'phone number', 'verify you',
        'sign up then create', 'create a poll', 'register an account'
    ])
    auto_candidate = (not manual_needed) and any(token in full_text for token in [
        'poll', 'draft', 'write', 'tagline', 'analysis', 'find', 'pricing', 'feature', 'tutorial'
    ])

    if publish_required:
        score -= 15
    if requires_human:
        score -= 20

    archetype = 'general_text'
    if any(token in full_text for token in ['competitor', 'pricing', 'feature analysis', 'complaint', 'g2', 'capterra']):
        archetype = 'competitive_strategist'
    elif any(token in full_text for token in ['company', 'lead', 'research', 'find 10 ai-first companies', 'find and list 20 real businesses']):
        archetype = 'research_analyst'
    elif any(token in full_text for token in ['tagline', 'pick:', 'reason:', 'poll:']):
        archetype = 'sharp_product_opinion'
    elif any(token in full_text for token in ['email template', 'outreach email', 'cold outreach']):
        archetype = 'sales_copywriter'
    elif any(token in full_text for token in ['tutorial', 'step-by-step', 'build your first ai agent', 'working code']):
        archetype = 'developer_educator'

    proof_strategy = 'none'
    if publish_required:
        proof_strategy = 'published_url_required'
    elif proof_hostable_text:
        proof_strategy = 'paste_rs_or_doc'

    risk_flags = []
    if publish_required:
        risk_flags.append('proof_required_or_likely')
    if requires_human:
        risk_flags.append('requires_human')
    if manual_needed:
        risk_flags.append('manual_execution_needed')
    if proof_hostable_text:
        risk_flags.append('proof_hostable_text')
    if reward_value >= 100 and publish_required:
        risk_flags.append('high_value_high_risk')

    if auto_candidate:
        bucket = 'auto_candidate'
    elif manual_needed or publish_required:
        bucket = 'manual_or_proof_required'
    else:
        bucket = 'review_manually'

    return score, {
        'bucket': bucket,
        'reward_value': reward_value,
        'require_proof': require_proof,
        'requires_human': requires_human,
        'proof_likely': publish_required,
        'proof_hostable_text': proof_hostable_text,
        'proof_strategy': proof_strategy,
        'manual_needed': manual_needed,
        'auto_candidate': auto_candidate,
        'archetype': archetype,
        'risk_flags': risk_flags,
    }


def run(client: AgentHansaClient, store: JsonStateStore) -> dict[str, Any]:
    log = logging.getLogger('tasks.quests')
    feed = client.get('/agents/feed')
    feed_quests = feed.get('quests', []) or []
    direct = load_quest_catalog(client, store)
    catalog = direct.get('quests', []) if isinstance(direct, dict) else []

    prioritized = []
    buckets = {'auto_candidate': [], 'manual_or_proof_required': [], 'review_manually': []}
    for quest in feed_quests:
        score, meta = _classify_quest(quest)
        row = {**quest, '_priority_score': score, '_classification': meta}
        prioritized.append(row)
        buckets[meta['bucket']].append(row)

    prioritized.sort(key=lambda x: x['_priority_score'], reverse=True)
    for key in buckets:
        buckets[key].sort(key=lambda x: x['_priority_score'], reverse=True)

    result = {
        'checked_at': utc_now().isoformat(),
        'feed_quests': prioritized,
        'quest_catalog': direct,
        'buckets': buckets,
        'summary': {k: len(v) for k, v in buckets.items()},
    }
    store.save('quests', result)
    log.info('quests refresh feed_quests=%s auto=%s manual=%s', len(feed_quests), len(buckets['auto_candidate']), len(buckets['manual_or_proof_required']))
    return result
