import sys

import pytest

import main
import redpacket_watch
from event_notify import maybe_notify_redpacket
from state import JsonStateStore
from utils.lock import SingleInstanceLock


def test_single_instance_lock_raises_readable_runtime_error_when_already_locked(tmp_path):
    path = tmp_path / 'bot.lock'
    first = SingleInstanceLock(path)
    second = SingleInstanceLock(path)
    first.acquire()

    with pytest.raises(RuntimeError, match='already running'):
        second.acquire()

    first.release()


def test_redpacket_watch_main_exits_cleanly_when_lock_is_held(tmp_path, monkeypatch):
    lock_path = tmp_path / 'agenthansa_redpacket.lock'
    lock = SingleInstanceLock(lock_path)
    lock.acquire()
    monkeypatch.setattr(sys, 'argv', ['redpacket_watch.py', '--dry-run'])
    monkeypatch.setattr(redpacket_watch, 'load_settings', lambda: type('S', (), {
        'log_dir': tmp_path / 'logs',
        'lock_file': tmp_path / 'agenthansa_bot.lock',
        'state_dir': tmp_path / 'state',
    })())

    assert redpacket_watch.main() == 0
    lock.release()


def test_main_exits_cleanly_when_lock_is_held(tmp_path, monkeypatch):
    lock_path = tmp_path / 'agenthansa_bot.lock'
    lock = SingleInstanceLock(lock_path)
    lock.acquire()
    monkeypatch.setattr(sys, 'argv', ['main.py', '--once', '--dry-run'])
    monkeypatch.setattr(main, 'load_settings', lambda _config=None: type('S', (), {
        'log_dir': tmp_path / 'logs',
        'lock_file': lock_path,
    })())

    assert main.main() == 0
    lock.release()


def test_maybe_notify_redpacket_deduplicates_sent_events(tmp_path, monkeypatch):
    settings = type('S', (), {
        'notify_telegram': True,
        'telegram_bot_token': '123:abc',
        'telegram_chat_id': '-1002477405938',
        'notify_prefix': 'K40',
    })()
    store = JsonStateStore(tmp_path)
    sent_messages = []
    monkeypatch.setattr('event_notify.send_telegram_message', lambda settings, message, client=None: sent_messages.append(message) or True)
    result = {
        'checked_at': '2026-04-17T02:00:00+00:00',
        'status': 'joined',
        'joined': True,
        'packet': {'id': 'rp1', 'title': 'Lucky Packet'},
        'solver': 'local_rules',
        'overview': {'next_packet_at': '2026-04-17T03:00:00+00:00'},
    }

    assert maybe_notify_redpacket(settings, store, result) is True
    assert maybe_notify_redpacket(settings, store, result) is False
    assert len(sent_messages) == 1
    assert 'Lucky Packet' in sent_messages[0]


def test_maybe_notify_redpacket_skips_test_packet_ids(tmp_path, monkeypatch):
    settings = type('S', (), {
        'notify_telegram': True,
        'telegram_bot_token': '123:abc',
        'telegram_chat_id': '-1002477405938',
        'notify_prefix': 'K40',
    })()
    store = JsonStateStore(tmp_path)
    sent_messages = []
    monkeypatch.setattr('event_notify.send_telegram_message', lambda settings, message, client=None: sent_messages.append(message) or True)
    result = {
        'checked_at': '2026-04-17T12:36:00+00:00',
        'status': 'joined',
        'joined': True,
        'packet': {'id': 'packet-vote', 'title': 'Upvote a forum post'},
        'solver': 'deepseek-local',
        'overview': {'next_packet_at': '2026-04-16T21:27:33.335175+00:00'},
    }

    assert maybe_notify_redpacket(settings, store, result) is False
    assert sent_messages == []
    assert store.load('event_notify_state', default={}) == {}
