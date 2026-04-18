from pathlib import Path

from config import load_settings
from event_notify import _is_test_redpacket_result, build_redpacket_notification, redpacket_notification_key, send_telegram_message
from state import JsonStateStore


class DummyResponse:
    def raise_for_status(self):
        return None


class DummyHttpClient:
    def __init__(self):
        self.calls = []

    def post(self, url: str, json: dict):
        self.calls.append((url, json))
        return DummyResponse()


def _settings(tmp_path: Path):
    cfg = tmp_path / 'config.yaml'
    cfg.write_text(
        """
AGENTHANSA_API_KEY: <your-agenthansa-api-key>
AGENTHANSA_BOT_STATE_DIR: ./state
AGENTHANSA_BOT_LOG_DIR: ./logs
AGENTHANSA_BOT_DATA_DIR: ./data
AGENTHANSA_BOT_REPORT_DIR: ./reports
AGENTHANSA_BOT_LOCK_FILE: ./bot.lock
AGENTHANSA_NOTIFY_TELEGRAM: true
AGENTHANSA_TELEGRAM_BOT_TOKEN: <your-bot-token>
AGENTHANSA_TELEGRAM_CHAT_ID: <your-chat-id>
AGENTHANSA_NOTIFY_PREFIX: K40
""".strip(),
        encoding='utf-8',
    )
    return load_settings(str(cfg))


def test_send_telegram_message_uses_prefix_and_chat_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    client = DummyHttpClient()

    sent = send_telegram_message(settings, '红包已抢到 packet_id=rp1', client=client)

    assert sent is True
    assert client.calls == [
        (
            'https://api.telegram.org/bot<your-bot-token>/sendMessage',
            {'chat_id': '<your-chat-id>', 'text': 'K40 红包已抢到 packet_id=rp1'},
        )
    ]


def test_redpacket_notification_key_and_message_for_joined_result():
    result = {
        'status': 'joined',
        'joined': True,
        'packet': {'id': 'rp1', 'title': 'Lucky Packet', 'challenge_type': 'upvote_post'},
        'solver': 'local_rules',
        'overview': {'next_packet_at': '2026-04-17T02:27:33+00:00'},
    }

    assert redpacket_notification_key(result) == 'redpacket:joined:rp1'
    text = build_redpacket_notification(result)
    assert '红包已抢到' in text
    assert '论坛点赞红包' in text
    assert '本地规则' in text


def test_redpacket_notification_key_for_manual_required_is_per_reason():
    result = {
        'status': 'manual_required',
        'reason': 'unsupported_challenge_action',
        'packet': {'id': 'rp2', 'title': 'Manual Packet', 'challenge_type': 'unknown'},
        'overview': {'next_packet_at': '2026-04-17T02:27:33+00:00'},
    }

    assert redpacket_notification_key(result) == 'redpacket:manual_required:rp2:unsupported_challenge_action'
    text = build_redpacket_notification(result)
    assert '需人工处理' in text
    assert '挑战动作暂不支持' in text


def test_redpacket_notification_key_is_none_for_idle_result():
    assert redpacket_notification_key({'status': 'idle', 'overview': {}}) is None
    assert build_redpacket_notification({'status': 'idle', 'overview': {}}) is None


def test_test_redpacket_result_is_filtered_from_notifications():
    result = {
        'status': 'joined',
        'joined': True,
        'packet': {'id': 'packet-vote', 'title': 'Upvote a forum post'},
        'solver': 'deepseek-local',
        'overview': {'next_packet_at': '2026-04-16T21:27:33.335175+00:00'},
    }

    assert _is_test_redpacket_result(result) is True
    assert redpacket_notification_key(result) is None


def test_non_test_uuid_redpacket_result_still_notifies():
    result = {
        'status': 'joined',
        'joined': True,
        'packet': {'id': '53c83079-8a8d-4452-a3ee-ed582d7dffde', 'title': 'Real Packet', 'challenge_type': 'referral'},
        'solver': 'deepseek-local',
        'overview': {'next_packet_at': None},
    }

    assert _is_test_redpacket_result(result) is False
    assert redpacket_notification_key(result) == 'redpacket:joined:53c83079-8a8d-4452-a3ee-ed582d7dffde'
    text = build_redpacket_notification(result)
    assert '邀请链接红包' in text
    assert 'DeepSeek' in text
