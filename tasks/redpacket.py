from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from client import AgentHansaClient
from event_notify import maybe_notify_redpacket
from config import load_settings
from state import JsonStateStore
from utils.timezone import utc_now

_WORD_NUMBERS = {
    'zero': 0,
    'one': 1,
    'two': 2,
    'three': 3,
    'four': 4,
    'five': 5,
    'six': 6,
    'seven': 7,
    'eight': 8,
    'nine': 9,
    'ten': 10,
    'eleven': 11,
    'twelve': 12,
    'thirteen': 13,
    'fourteen': 14,
    'fifteen': 15,
    'sixteen': 16,
    'seventeen': 17,
    'eighteen': 18,
    'nineteen': 19,
    'twenty': 20,
    'thirty': 30,
    'forty': 40,
    'fifty': 50,
    'sixty': 60,
    'seventy': 70,
    'eighty': 80,
    'ninety': 90,
    'hundred': 100,
    'dozen': 12,
}


def _extract_numbers(text: str) -> list[int]:
    lowered = text.lower().replace('-', ' ')
    numbers = [int(x) for x in re.findall(r'-?\d+', lowered)]
    tokens = re.findall(r'[a-z]+', lowered)
    i = 0
    while i < len(tokens):
        word = tokens[i]
        if word not in _WORD_NUMBERS:
            i += 1
            continue
        if word == 'hundred':
            i += 1
            continue
        value = _WORD_NUMBERS[word]
        if i + 1 < len(tokens) and tokens[i + 1] == 'hundred':
            value *= 100
            i += 1
        elif value >= 20 and i + 1 < len(tokens) and tokens[i + 1] in _WORD_NUMBERS and _WORD_NUMBERS[tokens[i + 1]] < 10:
            value += _WORD_NUMBERS[tokens[i + 1]]
            i += 1
        numbers.append(value)
        i += 1
    return numbers


def _solve_question_local(question: str) -> str | None:
    q = question.lower().strip()
    nums = _extract_numbers(q)

    if 'sum of' in q and len(nums) >= 2:
        return str(nums[0] + nums[1])
    if any(k in q for k in ['doubles', 'double']) and nums:
        base = nums[0] * 2
        if len(nums) >= 2 and any(k in q for k in ['finds', 'gets', 'more', 'plus', 'then']):
            return str(base + nums[1])
        return str(base)
    if any(k in q for k in ['triples', 'triple']) and nums:
        base = nums[0] * 3
        if len(nums) >= 2 and any(k in q for k in ['finds', 'gets', 'more', 'plus', 'then']):
            return str(base + nums[1])
        return str(base)
    if any(k in q for k in ['quadruples', 'quadruple']) and nums:
        base = nums[0] * 4
        if len(nums) >= 2 and any(k in q for k in ['finds', 'gets', 'more', 'plus', 'then']):
            return str(base + nums[1])
        return str(base)
    if any(k in q for k in ['shares half', 'gives half', 'gives away half', 'keeps half', 'half left', 'loses half']) and nums:
        return str((sum(nums[:2]) if len(nums) >= 2 else nums[0]) // 2)
    if any(k in q for k in ['each', 'per', 'apiece']) and len(nums) >= 2:
        return str(nums[0] * nums[1])
    if any(k in q for k in ['split evenly', 'equally among', 'shared equally', 'divide equally', 'distributed equally']) and len(nums) >= 2:
        return str(nums[0] // nums[1])
    if any(k in q for k in ['left over', 'leftover', 'remainder', 'remain']) and len(nums) >= 2:
        return str(nums[0] % nums[1])
    if re.search(r'(-?\d+)\s*\+\s*(-?\d+)', q):
        m = re.search(r'(-?\d+)\s*\+\s*(-?\d+)', q)
        if m:
            return str(int(m.group(1)) + int(m.group(2)))
    if re.search(r'(-?\d+)\s*-\s*(-?\d+)', q):
        m = re.search(r'(-?\d+)\s*-\s*(-?\d+)', q)
        if m:
            return str(int(m.group(1)) - int(m.group(2)))
    if any(k in q for k in ['times', 'multiplied by', 'multiply']) and len(nums) >= 2:
        return str(nums[0] * nums[1])
    if re.search(r'(\d+)\s*(?:x|\*)\s*(\d+)', q):
        m = re.search(r'(\d+)\s*(?:x|\*)\s*(\d+)', q)
        if m:
            return str(int(m.group(1)) * int(m.group(2)))
    if any(k in q for k in ['difference', 'more than', 'less than', 'fewer than']) and len(nums) >= 2:
        return str(abs(nums[0] - nums[1]))
    if any(k in q for k in ['twice as many', 'twice as much']) and nums:
        return str(nums[0] * 2)
    if any(k in q for k in ['three times as many', 'three times as much']) and nums:
        return str(nums[0] * 3)
    if len(nums) >= 3 and any(k in q for k in ['loses', 'minus', 'spent', 'gives']):
        return str(nums[0] + nums[1] - nums[2])
    if len(nums) >= 2 and any(k in q for k in ['gains', 'gets', 'more', 'plus', 'brings', 'collects', 'finds', 'altogether', 'in all', 'total', 'sum']):
        return str(nums[0] + nums[1])
    if len(nums) >= 2 and any(k in q for k in ['loses', 'minus', 'left', 'spent', 'gave away', 'used']):
        return str(nums[0] - nums[1])
    if any(k in q for k in ['count from', 'between', 'from']) and 'to' in q and len(nums) >= 2:
        return str(abs(nums[1] - nums[0]) + 1)
    if len(nums) == 1:
        return str(nums[0])
    return None


def _load_deepseek_config() -> dict[str, Any] | None:
    json_path = Path.home() / 'agenthansa_bot' / 'deepseek_redpacket_config.json'
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding='utf-8'))
        except Exception:
            data = None
        if isinstance(data, dict):
            keys = [str(k) for k in (data.get('keys') or []) if str(k).strip()]
            url = str(data.get('url') or '').strip()
            model = str(data.get('model') or 'DeepSeek-V3.2').strip() or 'DeepSeek-V3.2'
            if keys and url:
                return {'url': url, 'keys': keys, 'model': model}

    candidates = [
        Path.home() / 'agenthansa' / 'auto_system.py',
        Path.home() / 'backups' / 'agenthansa-cleanup-20260417-132421' / 'auto_earn_v3.py',
        Path.home() / 'backups' / 'agenthansa-cleanup-20260417-132421' / 'auto_earn.py',
    ]
    source = next((path for path in candidates if path.exists()), None)
    if source is None:
        return None
    text = source.read_text(encoding='utf-8')
    list_match = re.search(r'DEEPSEEK_KEYS\s*=\s*(\[[\s\S]*?\])', text)
    url_match = re.search(r'DEEPSEEK_URL\s*=\s*[\"\']([^\"\']+)[\"\']', text)
    if not list_match or not url_match:
        return None
    try:
        keys = ast.literal_eval(list_match.group(1))
    except Exception:
        return None
    if not isinstance(keys, list) or not keys:
        return None
    return {'url': url_match.group(1), 'keys': [str(k) for k in keys if str(k).strip()], 'model': 'DeepSeek-V3.2'}


def _solve_question_llm(question: str) -> dict[str, Any] | None:
    """Redpacket-only fallback solver.

    DeepSeek use in this module is intentionally restricted to:
    1. forum-comment prerequisite generation
    2. forum-post prerequisite generation
    3. question-solving fallback only when local rules fail
    """
    cfg = _load_deepseek_config()
    if not cfg:
        return None
    prompt = (
        'Solve this red packet comprehension question. '\
        'Return only one integer with no explanation.\n\n'
        f'Question: {question}'
    )
    for idx, key in enumerate(cfg['keys']):
        try:
            import httpx
            with httpx.Client(timeout=18, headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json', 'Accept': 'application/json'}) as client:
                resp = client.post(cfg['url'], json={
                    'model': cfg['model'],
                    'messages': [
                        {'role': 'system', 'content': 'Return only the integer answer.'},
                        {'role': 'user', 'content': prompt},
                    ],
                    'temperature': 0.1,
                    'max_tokens': 16,
                })
                resp.raise_for_status()
                data = resp.json()
                text = (((data.get('choices') or [{}])[0].get('message') or {}).get('content') or '').strip()
                match = re.findall(r'-?\d+', text)
                if match:
                    return {'answer': match[-1], 'provider': 'deepseek-local', 'key_index': idx}
        except Exception:
            continue
    return None


def _deepseek_text_completion(system_prompt: str, user_prompt: str, *, max_tokens: int = 220, temperature: float = 0.4) -> str | None:
    """Serial redpacket-only text generation helper.

    Scope is intentionally narrow: forum comment generation and forum post
    generation inside the redpacket flow. Do not expand this into general bot
    text generation.
    """
    cfg = _load_deepseek_config()
    if not cfg:
        return None
    for key in cfg['keys']:
        try:
            with httpx.Client(timeout=18, headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json', 'Accept': 'application/json'}) as client:
                resp = client.post(cfg['url'], json={
                    'model': cfg['model'],
                    'messages': [
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': user_prompt},
                    ],
                    'temperature': temperature,
                    'max_tokens': max_tokens,
                })
                resp.raise_for_status()
                data = resp.json()
                text = (((data.get('choices') or [{}])[0].get('message') or {}).get('content') or '').strip()
                if text:
                    return text
        except Exception:
            continue
    return None


def _safe_comment_body() -> str:
    return (
        'Useful execution pattern: finish the required action first, verify the API response body, '
        'then do the next step. That reduces noisy retries and keeps long-running automation stable.'
    )


def _comment_quality_check(text: str) -> tuple[bool, str | None]:
    body = str(text or '').strip()
    if len(body) < 18:
        return False, 'too_short'
    lowered = body.lower()
    banned_fragments = [
        '支持一下', '路过支持', '不错不错', '加油', '赞一个', '学习了', 'mark一下',
        'great project', 'nice project', 'good project', 'interesting project', 'thanks for sharing',
    ]
    if any(fragment in body or fragment in lowered for fragment in banned_fragments):
        return False, 'generic_praise'
    meaningful_markers = ['前置', '返回', '接口', '重试', '验证', '失败', '流程', '执行', '结果', '窗口', '步骤', '原因']
    if sum(1 for marker in meaningful_markers if marker in body) < 2:
        return False, 'not_substantive_enough'
    return True, None


def _generate_forum_comment_body(topic_hint: str) -> str | None:
    generated = _deepseek_text_completion(
        'You write short Chinese forum replies for anti-spam-sensitive tasks. Output one natural paragraph only. No emojis, no hashtags, no markdown, no generic praise.',
        (
            'Write a high-quality Chinese forum reply for a real forum comment task. '
            'The reply must feel human, specific, and substantive. '
            'It must mention concrete execution ideas such as prerequisite handling, checking API returns, reducing retry waste, debugging failure causes, or stabilizing short-window workflows. '
            'Do not write generic praise, slogans, support phrases, or empty filler. '
            'Use 40 to 120 Chinese characters. '
            f'Topic: {topic_hint}\n'
            'This is an anti-spam-sensitive forum reply. Produce a substantive high-quality comment only.'
        ),
        max_tokens=220,
        temperature=0.45,
    )
    if generated:
        cleaned = generated[:300].strip()
        ok, _reason = _comment_quality_check(cleaned)
        if ok:
            return cleaned
    return None


def _safe_post_payload() -> dict[str, str]:
    now = utc_now().strftime('%H%M')
    return {
        'title': f'Execution Note {now}: Reliable task sequencing',
        'body': (
            'When a workflow has short-lived windows, the stable pattern is simple: '
            'complete the prerequisite action first, confirm the returned state, then continue. '
            'This avoids wasted retries, reduces ambiguous failures, and makes automation easier to audit.'
        ),
        'category': 'strategy',
    }


def _generate_forum_post_payload(topic_hint: str) -> dict[str, str]:
    fallback = _safe_post_payload()
    generated = _deepseek_text_completion(
        'You write concise English forum strategy posts. Return valid JSON with keys title, body, category only.',
        (
            'Write a compact forum strategy post payload as JSON with keys title, body, category. '
            'Keep category as strategy. The post should describe reliable execution sequencing for online tasks, '
            'with practical value and no hype. '
            f'Topic hint: {topic_hint}'
        ),
        max_tokens=260,
        temperature=0.4,
    )
    if generated:
        try:
            obj = json.loads(generated)
        except Exception:
            obj = None
        if isinstance(obj, dict):
            title = str(obj.get('title') or '').strip()
            body = str(obj.get('body') or '').strip()
            category = str(obj.get('category') or 'strategy').strip() or 'strategy'
            if title and body:
                return {'title': title[:120], 'body': body[:1200], 'category': category}
    return fallback


def _classify_required_action(packet: dict[str, Any]) -> str | None:
    title = str(packet.get('title') or '').lower()
    desc = str(packet.get('challenge_description') or '').lower()
    text = f'{title}\n{desc}'

    # Priority matters: comment > vote > post.
    if 'comment' in text or '/comments' in text:
        return 'comment'
    if 'upvote' in text or 'vote on a forum post' in text or '"vote": "up"' in text or '/vote' in text:
        return 'vote'
    if 'forum post' in text or 'write a forum post' in text or ('post /api/forum' in text and 'comments' not in text and '"vote": "up"' not in text):
        return 'post'
    if 'referral' in text or 'ref link' in text or '/offers/' in text:
        return 'ref'
    return None


def _complete_required_action(client: AgentHansaClient, packet: dict[str, Any], packet_state: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    title = str(packet.get('title') or '')
    packet_id = str(packet.get('id') or '')
    action = _classify_required_action(packet)
    if packet_state.get('action_completed_for_packet_id') == packet_id:
        return {'status': 'already_completed_for_packet'}

    if action == 'comment':
        forum = client.get('/forum?sort=recent&limit=5')
        posts = forum.get('posts', []) or []
        if not posts:
            raise RuntimeError('no_forum_posts_for_comment_action')
        post_id = posts[0]['id']
        body = _generate_forum_comment_body(title or str(packet.get('challenge_description') or 'forum task'))
        if body is None:
            return {'status': 'manual_required', 'action': 'comment', 'reason': 'no_safe_comment_generator', 'post_id': post_id}
        payload = {'body': body}
        if dry_run:
            return {'status': 'dry_run', 'action': 'comment', 'post_id': post_id, 'payload': payload}
        resp = client.post(f'/forum/{post_id}/comments', json=payload)
        packet_state['action_completed_for_packet_id'] = packet_id
        packet_state['last_action_type'] = 'comment'
        return {'status': 'completed', 'action': 'comment', 'post_id': post_id, 'response': resp}

    if action == 'vote':
        forum = client.get('/forum?sort=recent&limit=5')
        posts = forum.get('posts', []) or []
        if not posts:
            raise RuntimeError('no_forum_posts_for_vote_action')
        if dry_run:
            return {'status': 'dry_run', 'action': 'vote', 'post_id': posts[0]['id']}
        for post in posts:
            post_id = post['id']
            try:
                resp = client.post(f'/forum/{post_id}/vote', json={'vote': 'up'})
                packet_state['action_completed_for_packet_id'] = packet_id
                packet_state['last_action_type'] = 'vote'
                return {'status': 'completed', 'action': 'vote', 'post_id': post_id, 'response': resp}
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code == 409:
                    continue
                raise
        raise RuntimeError('no_voteable_post_found')

    if action == 'post':
        payload = _generate_forum_post_payload(title or str(packet.get('challenge_description') or 'forum strategy'))
        if dry_run:
            return {'status': 'dry_run', 'action': 'post', 'payload': payload}
        resp = client.post('/forum', json=payload)
        packet_state['action_completed_for_packet_id'] = packet_id
        packet_state['last_action_type'] = 'post'
        return {'status': 'completed', 'action': 'post', 'response': resp}

    if action == 'ref':
        offers = client.get('/offers')
        rows = offers.get('offers', []) or offers.get('rows', []) or []
        if not rows:
            raise RuntimeError('no_offers_available')
        offer = rows[0]
        offer_id = offer.get('id')
        if dry_run:
            return {'status': 'dry_run', 'action': 'ref', 'offer_id': offer_id}
        resp = client.post(f'/offers/{offer_id}/ref', json={})
        packet_state['action_completed_for_packet_id'] = packet_id
        packet_state['last_action_type'] = 'ref'
        return {'status': 'completed', 'action': 'ref', 'offer_id': offer_id, 'response': resp}

    return {'status': 'unsupported', 'reason': 'unsupported_challenge_action', 'title': title, 'description': packet.get('challenge_description')}


def _notify_result(store: JsonStateStore, result: dict[str, Any]) -> None:
    try:
        maybe_notify_redpacket(load_settings(), store, result)
    except Exception as exc:
        logging.getLogger('tasks.redpacket').warning('redpacket_notify_failed error=%s', exc)


def _http_error_details(exc: httpx.HTTPStatusError) -> dict[str, Any]:
    response = exc.response
    body = ''
    try:
        body = response.text[:500] if response is not None else ''
    except Exception:
        body = ''
    return {
        'status_code': response.status_code if response is not None else None,
        'body': body,
        'url': str(exc.request.url) if exc.request is not None else None,
    }


def run(client: AgentHansaClient, store: JsonStateStore, dry_run: bool = False) -> dict[str, Any]:
    log = logging.getLogger('tasks.redpacket')
    packet_state = store.load('redpacket_state', default={})
    overview = client.get('/red-packets')
    active = overview.get('active', []) or []
    result: dict[str, Any] = {
        'checked_at': utc_now().isoformat(),
        'overview': overview,
        'joined': False,
    }

    if not active:
        log.info('no active red packet next_packet_at=%s', overview.get('next_packet_at'))
        cleared_state = {
            k: v
            for k, v in packet_state.items()
            if k not in {
                'packet',
                'challenge',
                'answer_preview',
                'solver',
                'llm_solver',
                'challenge_action',
                'join_response',
                'reason',
                'status',
            }
        }
        cleared_state.update(result)
        cleared_state['status'] = 'idle'
        store.save('redpacket_state', cleared_state)
        _notify_result(store, result)
        return result

    packet = active[0]
    packet_id = packet.get('id')
    result['packet'] = packet
    if packet_state.get('last_joined_packet_id') == packet_id:
        result['status'] = 'already_joined'
        log.info('red packet already joined packet_id=%s', packet_id)
        store.save('redpacket_state', {**packet_state, **result})
        _notify_result(store, result)
        return result

    action_result = _complete_required_action(client, packet, packet_state, dry_run=dry_run)
    result['challenge_action'] = action_result
    if action_result.get('status') == 'unsupported':
        result['status'] = 'manual_required'
        result['reason'] = 'unsupported_challenge_action'
        store.save('redpacket_state', {**packet_state, **result})
        _notify_result(store, result)
        return result
    if action_result.get('status') == 'manual_required':
        result['status'] = 'manual_required'
        result['reason'] = action_result.get('reason') or 'required_action_needs_manual_intervention'
        store.save('redpacket_state', {**packet_state, **result})
        _notify_result(store, result)
        return result

    challenge = client.get(f'/red-packets/{packet_id}/challenge')
    question = challenge.get('question', '')
    answer = _solve_question_local(question)
    solver = 'local_rules' if answer is not None else None
    if answer is None:
        llm = _solve_question_llm(question)
        if llm:
            answer = llm['answer']
            solver = llm['provider']
            result['llm_solver'] = llm
    result['challenge'] = challenge
    result['answer_preview'] = answer
    result['solver'] = solver

    if answer is None:
        result['status'] = 'manual_required'
        result['reason'] = 'could_not_safely_solve_question'
        log.warning('red packet manual_required packet_id=%s question=%s', packet_id, question)
        store.save('redpacket_state', {**packet_state, **result})
        _notify_result(store, result)
        return result

    if dry_run:
        result['status'] = 'dry_run'
        store.save('redpacket_state', {**packet_state, **result})
        _notify_result(store, result)
        return result

    try:
        joined = client.post(f'/red-packets/{packet_id}/join', json={'answer': answer})
    except httpx.HTTPStatusError as exc:
        result['status'] = 'manual_required'
        result['reason'] = 'join_request_rejected'
        result['join_error'] = _http_error_details(exc)
        store.save('redpacket_state', {**packet_state, **result})
        log.warning('red packet join rejected packet_id=%s solver=%s status_code=%s body=%s', packet_id, solver, result['join_error'].get('status_code'), result['join_error'].get('body'))
        _notify_result(store, result)
        return result

    packet_state['last_joined_packet_id'] = packet_id
    packet_state['last_joined_at'] = utc_now().isoformat()
    result['status'] = 'joined'
    result['joined'] = True
    result['join_response'] = joined
    store.save('redpacket_state', {**packet_state, **result})
    log.info('red packet joined packet_id=%s solver=%s', packet_id, solver)
    _notify_result(store, result)
    return result
