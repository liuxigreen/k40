from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from config import Settings
from state import JsonStateStore
from utils.timezone import utc_now

STATE_KEY = 'publish_queue'


ARCHETYPE_SYSTEM_PROMPTS = {
    'research_analyst': 'You are a research analyst. Write specific, structured, evidence-oriented, actionable findings. Avoid fluff and fake certainty.',
    'competitive_strategist': 'You are a competitive strategist. Lead with objective facts, compare options clearly, then identify the win-angle.',
    'sharp_product_opinion': 'You are a sharp product opinion writer. Be decisive, concise, and justify choices with clear tradeoffs.',
    'sales_copywriter': 'You are a sales copywriter. Write conversion-oriented, audience-aware copy with practical CTAs and no hypey filler.',
    'developer_educator': 'You are a developer educator. Write reproducible, step-by-step, code-first technical guidance with operational clarity.',
    'general_text': 'You are a precise professional writer. Be concrete, clear, and non-generic.',
}


def _detect_platform(title: str) -> str | None:
    lowered = title.lower()
    if any(token in lowered for token in ['twitter', 'tweet', 'x/']):
        return 'twitter'
    if any(token in lowered for token in ['dev.to', 'blog post', 'blog article', 'devto']):
        return 'devto'
    if any(token in lowered for token in ['docs', 'documentation', 'markdown']):
        return 'docs'
    return None


def _build_prompts(quest: dict[str, Any], classification: dict[str, Any], platform: str) -> tuple[str, str]:
    archetype = str(classification.get('archetype') or 'general_text')
    system_prompt = ARCHETYPE_SYSTEM_PROMPTS.get(archetype, ARCHETYPE_SYSTEM_PROMPTS['general_text'])
    title = str(quest.get('title') or '').strip()
    description = str(quest.get('description') or '').strip()
    goal = str(quest.get('goal') or '').strip()
    proof_strategy = str(classification.get('proof_strategy') or 'none')
    user_prompt = (
        f"Quest title: {title}\n"
        f"Platform: {platform}\n"
        f"Archetype: {archetype}\n"
        f"Proof strategy: {proof_strategy}\n\n"
        f"Description:\n{description or '(none)'}\n\n"
        f"Goal:\n{goal or '(none)'}\n\n"
        "Produce original submission-ready content. Be specific, structured, and avoid template language."
    )
    return system_prompt, user_prompt


def _draft_path(settings: Settings, quest_id: str, platform: str) -> Path:
    safe_quest_id = ''.join(ch if ch.isalnum() or ch in {'-', '_'} else '-' for ch in quest_id).strip('-') or 'quest'
    return settings.report_dir / 'drafts' / f'{safe_quest_id}-{platform}.md'


def _write_draft_file(path: Path, quest: dict[str, Any], classification: dict[str, Any], system_prompt: str, user_prompt: str) -> str:
    title = str(quest.get('title') or '').strip()
    description = str(quest.get('description') or '').strip()
    goal = str(quest.get('goal') or '').strip()
    lines = [
        f'# Draft for {title or "Untitled quest"}',
        '',
        f"Archetype: {classification.get('archetype') or 'general_text'}",
        f"Proof strategy: {classification.get('proof_strategy') or 'none'}",
        '',
        '## Quest context',
        f"Title: {title or '(none)'}",
        f"Description: {description or '(none)'}",
        f"Goal: {goal or '(none)'}",
        '',
        '## Generation prompts',
        '### System prompt',
        system_prompt,
        '',
        '### User prompt',
        user_prompt,
        '',
        '## Draft content',
        '',
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines), encoding='utf-8')
    return str(path)


def _build_item(quest: dict[str, Any]) -> dict[str, Any] | None:
    title = str(quest.get('title') or '')
    platform = _detect_platform(title)
    if not platform:
        return None
    quest_id = str(quest.get('id') or '')
    classification = dict(quest.get('_classification') or {})
    publish_required = bool(classification.get('proof_likely') or classification.get('require_proof')) or platform in {'devto'}
    status = 'publish_pending' if publish_required else 'draft_needed'
    archetype = str(classification.get('archetype') or 'general_text')
    proof_strategy = str(classification.get('proof_strategy') or ('published_url_required' if publish_required else 'none'))
    system_prompt, user_prompt = _build_prompts(quest, classification, platform)
    return {
        'queue_id': f'publish::{quest_id}::{platform}',
        'quest_id': quest_id,
        'title': title,
        'platform': platform,
        'publish_required': publish_required,
        'status': status,
        'archetype': archetype,
        'proof_strategy': proof_strategy,
        'priority_score': int(quest.get('_priority_score') or 0),
        'description': str(quest.get('description') or ''),
        'goal': str(quest.get('goal') or ''),
        'system_prompt': system_prompt,
        'user_prompt': user_prompt,
        'published_url': None,
        'proof_url': None,
        'draft_path': None,
        'notes': [],
        'updated_at': utc_now().isoformat(),
    }


def run(settings: Settings, store: JsonStateStore) -> dict[str, Any]:
    log = logging.getLogger('tasks.publishing_queue')
    if not settings.enable_publish_pipeline:
        result = {'generated_at': utc_now().isoformat(), 'items': [], 'summary': {'queued': 0, 'enabled': False}}
        store.save(STATE_KEY, result)
        return result

    quests = store.load('quests', default={}).get('buckets', {}) or {}
    candidates = list(quests.get('auto_candidate', []) or []) + list(quests.get('manual_or_proof_required', []) or [])
    existing = store.load(STATE_KEY, default={})
    existing_items = {item.get('queue_id'): item for item in (existing.get('items', []) or []) if item.get('queue_id')}

    items = []
    for quest in sorted(candidates, key=lambda row: int(row.get('_priority_score') or 0), reverse=True):
        item = _build_item(quest)
        if not item:
            continue
        classification = dict(quest.get('_classification') or {})
        prior = existing_items.get(item['queue_id'])
        if prior:
            merged = {**item, **prior}
            if not str(merged.get('title') or '').strip():
                merged['title'] = item['title']
            draft_path = Path(str(merged.get('draft_path') or _draft_path(settings, item['quest_id'], item['platform'])))
            if not draft_path.exists():
                merged['draft_path'] = _write_draft_file(
                    draft_path,
                    quest,
                    classification,
                    item['system_prompt'],
                    item['user_prompt'],
                )
            else:
                merged['draft_path'] = str(draft_path)
            if merged.get('proof_strategy') == 'paste_rs_or_doc' and merged.get('status') == 'draft_needed':
                merged['status'] = 'draft_ready'
            merged['updated_at'] = utc_now().isoformat()
            items.append(merged)
        else:
            item['draft_path'] = _write_draft_file(
                _draft_path(settings, item['quest_id'], item['platform']),
                quest,
                classification,
                item['system_prompt'],
                item['user_prompt'],
            )
            if item['proof_strategy'] == 'paste_rs_or_doc' and item['status'] == 'draft_needed':
                item['status'] = 'draft_ready'
            items.append(item)
        if len(items) >= settings.publish_queue_limit:
            break

    result = {
        'generated_at': utc_now().isoformat(),
        'items': items,
        'summary': {
            'queued': len(items),
            'enabled': True,
            'publish_required': sum(1 for item in items if item.get('publish_required')),
            'draft_only': sum(1 for item in items if not item.get('publish_required')),
        },
    }
    store.save(STATE_KEY, result)
    log.info('publishing_queue queued=%s publish_required=%s draft_only=%s', result['summary']['queued'], result['summary']['publish_required'], result['summary']['draft_only'])
    return result
