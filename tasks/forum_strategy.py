from __future__ import annotations

import logging
from typing import Any

from config import Settings
from client import AgentHansaClient
from state import JsonStateStore
from utils.timezone import utc_now


def _forum_points_from_breakdown(breakdown: dict[str, Any]) -> int:
    total = 0
    for key, value in breakdown.items():
        if 'forum' in str(key).lower() or 'comment' in str(key).lower():
            total += int((value or {}).get('points', 0) or 0)
    return total


def _topic_candidates(posts: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    candidates = []
    for post in posts[:5]:
        title = str(post.get('title') or '').strip()
        body = str(post.get('body') or '').strip()
        if not title and not body:
            continue
        category = str(post.get('category') or '')
        candidates.append({
            'source': source,
            'title': title,
            'category': category,
            'angle': f"Respond with analysis or useful extension to: {title or body[:80]}",
            'why_it_is_viable': 'real forum context exists, so a reply can add information instead of generic filler',
        })
    return candidates


def run(settings: Settings, client: AgentHansaClient, store: JsonStateStore) -> dict[str, Any]:
    log = logging.getLogger('tasks.forum_strategy')
    daily_xp = store.load('daily_xp', default={}).get('data', {})
    breakdown = daily_xp.get('breakdown', {}) or {}
    forum_points = _forum_points_from_breakdown(breakdown)

    digest = client.get('/forum/digest')
    alliance = client.get('/forum/alliance')
    feed = store.load('feed', default={}).get('data', {})
    quest_rows = (store.load('quests', default={}).get('buckets', {}) or {}).get('auto_candidate', [])

    digest_posts = digest.get('posts', []) or []
    alliance_posts = alliance.get('posts', []) or []
    topic_candidates = _topic_candidates(digest_posts, 'digest') + _topic_candidates(alliance_posts, 'alliance')

    manual_actions = []
    if forum_points < settings.forum_xp_soft_cap:
        manual_actions.append({
            'type': 'high_quality_forum_comment',
            'reason': 'forum_xp_below_soft_cap',
            'requirements': [
                'must reference a real post',
                'must add information, analysis, or feedback',
                'must not be generic praise',
            ],
            'suggested_inputs': [
                'quest lessons learned',
                'alliance execution analysis',
                'product comparison or case study',
            ],
            'topic_candidates': topic_candidates[:5],
        })
    else:
        manual_actions.append({
            'type': 'stop_forum_push',
            'reason': 'forum_xp_near_or_above_soft_cap',
            'next_focus': ['quests', 'red_packets', 'proof-backed tasks'],
        })

    if quest_rows:
        manual_actions.append({
            'type': 'quest_to_forum_recap',
            'reason': 'convert_real_work_into_forum_value_without_spam',
            'candidate_quests': [row.get('title') for row in quest_rows[:3]],
            'allowed_formats': ['retrospective', 'process note', 'comparison', 'execution lesson'],
        })

    result = {
        'checked_at': utc_now().isoformat(),
        'forum_points': forum_points,
        'digest_sample_count': len(digest_posts),
        'alliance_post_count': len(alliance_posts),
        'topic_candidates': topic_candidates[:10],
        'manual_actions': manual_actions,
        'feed_hint': feed.get('context') or feed.get('urgent') or [],
    }
    store.save('forum_strategy', result)
    store.save('manual_actions', {'checked_at': result['checked_at'], 'items': manual_actions})
    log.info('forum strategy forum_points=%s actions=%s topics=%s', forum_points, len(manual_actions), len(topic_candidates))
    return result
