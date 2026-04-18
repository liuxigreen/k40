from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from config import Settings


_TEST_PACKET_ID_RE = re.compile(r'^packet-[a-z0-9_-]+$', re.IGNORECASE)


def _is_test_redpacket_result(result: dict[str, Any]) -> bool:
    packet = result.get('packet') or {}
    packet_id = str(packet.get('id') or '').strip()
    if not packet_id:
        return False
    return bool(_TEST_PACKET_ID_RE.match(packet_id))


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def send_telegram_message(settings: Settings, message: str, *, client: Any | None = None) -> bool:
    if not _truthy(getattr(settings, 'notify_telegram', False), False):
        return False
    token = str(getattr(settings, 'telegram_bot_token', '') or '').strip()
    chat_id = str(getattr(settings, 'telegram_chat_id', '') or '').strip()
    prefix = str(getattr(settings, 'notify_prefix', '') or '').strip()
    if not token or not chat_id:
        logging.getLogger('event_notify').warning('telegram_notify_skipped reason=missing_credentials')
        return False
    text = f'{prefix} {message}'.strip() if prefix else message
    own_client = client is None
    http_client = client or httpx.Client(timeout=15)
    try:
        response = http_client.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': text},
        )
        response.raise_for_status()
        logging.getLogger('event_notify').info('telegram_notify_sent text=%s', text)
        return True
    finally:
        if own_client and hasattr(http_client, 'close'):
            http_client.close()


def redpacket_notification_key(result: dict[str, Any]) -> str | None:
    if _is_test_redpacket_result(result):
        logging.getLogger('event_notify').info('telegram_notify_skipped reason=test_redpacket_result packet_id=%s', str((result.get('packet') or {}).get('id') or '').strip())
        return None
    status = str(result.get('status') or '').strip().lower()
    packet = result.get('packet') or {}
    packet_id = str(packet.get('id') or '').strip()
    if not status or not packet_id:
        return None
    if status == 'joined' or result.get('joined'):
        return f'redpacket:joined:{packet_id}'
    if status == 'manual_required':
        reason = str(result.get('reason') or 'manual_required').strip() or 'manual_required'
        return f'redpacket:manual_required:{packet_id}:{reason}'
    return None


def _packet_label(packet: dict[str, Any]) -> str:
    challenge_type = str(packet.get('challenge_type') or '').strip().lower()
    mapping = {
        'upvote_post': '论坛点赞红包',
        'comment_post': '论坛评论红包',
        'create_post': '论坛发帖红包',
        'generate_referral': '邀请链接红包',
        'referral': '邀请链接红包',
    }
    if challenge_type in mapping:
        return mapping[challenge_type]
    title = str(packet.get('title') or '').strip().lower()
    if 'upvote' in title or 'vote' in title:
        return '论坛点赞红包'
    if 'comment' in title:
        return '论坛评论红包'
    if 'referral' in title or 'ref link' in title:
        return '邀请链接红包'
    if 'post' in title:
        return '论坛发帖红包'
    return '红包任务'


def _solver_label(solver: str) -> str:
    mapping = {
        'local_rules': '本地规则',
        'deepseek-local': 'DeepSeek',
        'unknown': '未知',
    }
    solver = str(solver or 'unknown').strip()
    return mapping.get(solver, solver)


def _reason_label(reason: str) -> str:
    mapping = {
        'join_request_rejected': '加入请求被拒绝',
        'unsupported_challenge_action': '挑战动作暂不支持',
        'required_action_needs_manual_intervention': '前置动作需要人工处理',
        'could_not_safely_solve_question': '题目暂未安全求解',
        'no_safe_comment_generator': '评论生成器不可安全使用',
        'manual_required': '需要人工处理',
    }
    reason = str(reason or 'manual_required').strip()
    return mapping.get(reason, reason)


def build_redpacket_notification(result: dict[str, Any]) -> str | None:
    status = str(result.get('status') or '').strip().lower()
    packet = result.get('packet') or {}
    packet_label = _packet_label(packet)
    packet_id = str(packet.get('id') or '').strip()
    overview = result.get('overview') or {}
    next_packet_at = overview.get('next_packet_at')
    title = str(packet.get('title') or '').strip()
    title_suffix = f'（{title}）' if title else ''
    if status == 'joined' or result.get('joined'):
        solver = _solver_label(str(result.get('solver') or 'unknown').strip())
        return f'红包已抢到：{packet_label}{title_suffix}｜packet_id={packet_id}｜求解={solver}｜下次={next_packet_at}'
    if status == 'manual_required':
        reason = _reason_label(str(result.get('reason') or 'manual_required').strip())
        return f'红包需人工处理：{packet_label}{title_suffix}｜packet_id={packet_id}｜原因：{reason}｜下次={next_packet_at}'
    return None


def maybe_notify_redpacket(settings: Settings, store, result: dict[str, Any]) -> bool:
    key = redpacket_notification_key(result)
    message = build_redpacket_notification(result)
    if not key or not message:
        return False
    notify_state = store.load('event_notify_state', default={})
    sent_keys = dict(notify_state.get('sent_keys') or {})
    if key in sent_keys:
        return False
    sent = send_telegram_message(settings, message)
    if sent:
        sent_keys[key] = result.get('checked_at') or 'sent'
        store.save('event_notify_state', {'sent_keys': sent_keys})
    return sent
