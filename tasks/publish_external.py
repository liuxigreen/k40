from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
import json

import httpx

from config import Settings
from state import JsonStateStore
from utils.timezone import utc_now

STATE_KEY = 'publish_external'
X_WEB_BEARER_TOKEN = '<x-web-bearer-token>'
X_CREATE_TWEET_QUERY_ID = 'c50A_puUoQGK_4SXseYz3A'


def _extract_draft_content(path_str: str | None) -> str:
    if not path_str:
        return ''
    path = Path(path_str)
    if not path.exists():
        return ''
    text = path.read_text(encoding='utf-8')
    marker = '## Draft content'
    if marker in text:
        _, _, tail = text.partition(marker)
        return tail.strip()
    return text.strip()


def _write_draft_content(path_str: str, content: str) -> None:
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding='utf-8') if path.exists() else ''
    marker = '## Draft content'
    if marker in existing:
        head, _, _ = existing.partition(marker)
        new_text = f"{head}{marker}\n\n{content.strip()}\n"
    else:
        new_text = content.strip() + '\n'
    path.write_text(new_text, encoding='utf-8')


def _slugify(text: str) -> str:
    value = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return value or 'post'


def _fallback_devto_body(item: dict[str, Any]) -> str:
    title = str(item.get('title') or 'Useful AI workflow notes').strip()
    product = 'FuturMix AI gateway' if 'futurmix' in title.lower() else 'this workflow'
    return (
        f"# {title}\n\n"
        f"{product} is easiest to evaluate when you focus on real operator pain instead of generic AI hype. "
        "Most teams do not need more raw model choice — they need predictable routing, cleaner fallbacks, and faster debugging when one provider slows down.\n\n"
        "## What actually matters\n\n"
        "1. Stable request routing across providers\n"
        "2. Clear cost and latency tradeoffs\n"
        "3. Fast failover when one route degrades\n"
        "4. Proof that the integration is easier to operate than a pile of custom glue\n\n"
        "## A practical evaluation checklist\n\n"
        "- Start with one real workload instead of a vague benchmark\n"
        "- Compare response quality, latency, and retry behavior\n"
        "- Measure what happens during provider errors, not only the happy path\n"
        "- Keep the integration surface small enough that your team can maintain it\n\n"
        "## Why this is relevant now\n\n"
        "Teams shipping AI features are under pressure to move quickly without creating operational debt. "
        "A gateway layer becomes useful when it reduces complexity for product teams and gives operators better control over routing and resilience.\n\n"
        "## Bottom line\n\n"
        f"If {product} helps a team ship reliable AI features faster, that is the story worth proving. "
        "Concrete workflow wins beat generic model-marketing every time.\n"
    ).strip()


def _fallback_twitter_text(item: dict[str, Any]) -> str:
    title = str(item.get('title') or '').strip()
    if 'futurmix' in title.lower() or 'api' in title.lower() or 'routing' in title.lower():
        text = (
            "AI teams don’t need more dashboard clutter. They need cleaner model routing, sane fallbacks, "
            "and fast visibility when a provider slows down. Reliable orchestration beats chasing every new model. "
            "#AI #LLM"
        )
    else:
        text = (
            "Quality beats volume in AI workflows. Clear proof, concrete outcomes, and short feedback loops usually "
            "win over generic content and noisy automation. #AI #BuildInPublic"
        )
    return text[:280].strip()


def _ensure_content(item: dict[str, Any]) -> str:
    draft_path = str(item.get('draft_path') or '').strip()
    content = _extract_draft_content(draft_path)
    if content:
        return content
    platform = str(item.get('platform') or '').strip().lower()
    generated = _fallback_devto_body(item) if platform == 'devto' else _fallback_twitter_text(item)
    if draft_path:
        _write_draft_content(draft_path, generated)
    return generated


def _publish_devto(api_key: str, item: dict[str, Any], markdown: str) -> dict[str, Any]:
    title = str(item.get('title') or 'AgentHansa post').strip()
    tags = ['ai', 'llm', 'automation']
    with httpx.Client(timeout=30, headers={'api-key': api_key, 'User-Agent': 'agenthansa_bot/1.0 (+termux)'}) as client:
        response = client.post(
            'https://dev.to/api/articles',
            json={
                'article': {
                    'title': title[:120],
                    'published': True,
                    'body_markdown': markdown,
                    'tags': tags,
                }
            },
        )
        response.raise_for_status()
        data = response.json()
    return {
        'published_url': data.get('url') or data.get('canonical_url') or data.get('path'),
        'external_id': data.get('id'),
        'external_meta': data,
    }


def _build_x_headers(ct0: str) -> dict[str, str]:
    return {
        'authorization': f'Bearer {X_WEB_BEARER_TOKEN}',
        'content-type': 'application/json',
        'origin': 'https://x.com',
        'referer': 'https://x.com/compose/post',
        'user-agent': 'Mozilla/5.0 (Android 13; Mobile; rv:124.0) Gecko/124.0 Firefox/124.0',
        'x-csrf-token': ct0,
        'x-twitter-active-user': 'yes',
        'x-twitter-auth-type': 'OAuth2Session',
        'x-twitter-client-language': 'en',
    }


def _extract_x_create_result(data: dict[str, Any]) -> tuple[str | None, str | None]:
    result = ((((data.get('data') or {}).get('create_tweet') or {}).get('tweet_results') or {}).get('result') or {})
    status_id = str(result.get('rest_id') or result.get('id') or '').strip() or None
    legacy_user = (((((result.get('core') or {}).get('user_results') or {}).get('result') or {}).get('legacy') or {}))
    screen_name = str(legacy_user.get('screen_name') or '').strip() or None
    return status_id, screen_name


def _publish_x(auth_token: str, ct0: str, item: dict[str, Any], text: str) -> dict[str, Any]:
    tweet_text = text.strip()[:280]
    cookies = {'auth_token': auth_token, 'ct0': ct0}
    variables = {
        'tweet_text': tweet_text,
        'dark_request': False,
        'media': {
            'media_entities': [],
            'possibly_sensitive': False,
        },
        'semantic_annotation_ids': [],
        'disallowed_reply_options': None,
    }
    features = {
        'premium_content_api_read_enabled': False,
        'communities_web_enable_tweet_community_results_fetch': True,
        'c9s_tweet_anatomy_moderator_badge_enabled': True,
        'responsive_web_edit_tweet_api_enabled': True,
        'graphql_is_translatable_rweb_tweet_is_translatable_enabled': True,
        'view_counts_everywhere_api_enabled': True,
        'longform_notetweets_consumption_enabled': True,
        'responsive_web_twitter_article_tweet_consumption_enabled': True,
        'tweet_awards_web_tipping_enabled': False,
        'responsive_web_grok_analyze_button_fetch_trends_enabled': False,
        'responsive_web_grok_analyze_post_followups_enabled': False,
        'responsive_web_jetfuel_frame': False,
        'responsive_web_grok_share_attachment_enabled': False,
        'responsive_web_grok_annotations_enabled': False,
        'responsive_web_graphql_exclude_directive_enabled': True,
        'verified_phone_label_enabled': False,
        'freedom_of_speech_not_reach_fetch_enabled': True,
        'standardized_nudges_misinfo': True,
        'tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled': True,
        'responsive_web_graphql_skip_user_profile_image_extensions_enabled': False,
        'responsive_web_graphql_timeline_navigation_enabled': True,
        'responsive_web_enhance_cards_enabled': False,
    }
    field_toggles = {'withArticleRichContentState': False}
    payload = {
        'variables': variables,
        'features': features,
        'fieldToggles': field_toggles,
        'queryId': X_CREATE_TWEET_QUERY_ID,
    }
    endpoint = f'https://x.com/i/api/graphql/{X_CREATE_TWEET_QUERY_ID}/CreateTweet'
    with httpx.Client(timeout=30, headers=_build_x_headers(ct0), cookies=cookies, follow_redirects=True) as client:
        response = client.post(endpoint, json=payload)
        response.raise_for_status()
        data = response.json()
    status_id, screen_name = _extract_x_create_result(data)
    if screen_name and status_id:
        url = f'https://x.com/{screen_name}/status/{status_id}'
    elif status_id:
        url = f'https://x.com/i/web/status/{status_id}'
    else:
        url = None
    return {
        'published_url': url,
        'external_id': status_id,
        'external_meta': data,
        'published_text': tweet_text,
    }


def run(settings: Settings, store: JsonStateStore, *, dry_run: bool = False) -> dict[str, Any]:
    log = logging.getLogger('tasks.publish_external')
    queue = store.load('publish_queue', default={})
    queue_items = list(queue.get('items', []) or [])
    results: list[dict[str, Any]] = []
    published = 0

    for item in queue_items:
        platform = str(item.get('platform') or '').strip().lower()
        status = str(item.get('status') or '').strip().lower()
        if platform not in {'devto', 'twitter'}:
            continue
        if item.get('published_url'):
            continue
        if status not in {'publish_pending', 'draft_needed', 'draft_ready', 'publish_error'}:
            continue

        content = _ensure_content(item)
        if not content:
            item['status'] = 'publish_error'
            item.setdefault('notes', []).append('missing_publish_content')
            results.append({'queue_id': item.get('queue_id'), 'platform': platform, 'status': 'missing_content'})
            continue

        if dry_run:
            results.append({'queue_id': item.get('queue_id'), 'platform': platform, 'status': 'dry_run', 'content_preview': content[:120]})
            continue

        try:
            if platform == 'devto':
                if not settings.devto_api_key:
                    raise RuntimeError('missing_devto_api_key')
                pub = _publish_devto(settings.devto_api_key, item, content)
            else:
                if not settings.x_auth_token or not settings.x_ct0:
                    raise RuntimeError('missing_x_session_tokens')
                pub = _publish_x(settings.x_auth_token, settings.x_ct0, item, content)
            published_url = str(pub.get('published_url') or '').strip()
            if not published_url:
                raise RuntimeError('publisher_returned_no_url')
            item['published_url'] = published_url
            item['proof_url'] = published_url
            item['status'] = 'published'
            item['published_at'] = utc_now().isoformat()
            item['external_publish_id'] = pub.get('external_id')
            if pub.get('published_text'):
                item['published_text'] = pub.get('published_text')
            item.setdefault('notes', []).append(f'published:{platform}')
            results.append({'queue_id': item.get('queue_id'), 'platform': platform, 'status': 'published', 'published_url': published_url})
            published += 1
        except Exception as exc:
            item['status'] = 'publish_error'
            item.setdefault('notes', []).append(f'publish_failed:{platform}:{exc}')
            results.append({'queue_id': item.get('queue_id'), 'platform': platform, 'status': 'error', 'error': str(exc)})
            log.warning('publish_external_failed queue_id=%s platform=%s error=%s', item.get('queue_id'), platform, exc)

    queue['items'] = queue_items
    store.save('publish_queue', queue)
    result = {
        'generated_at': utc_now().isoformat(),
        'items': results,
        'summary': {'published': published, 'attempted': len(results), 'dry_run': dry_run},
    }
    store.save(STATE_KEY, result)
    log.info('publish_external published=%s attempted=%s dry_run=%s', published, len(results), dry_run)
    return result
