import builtins
from types import SimpleNamespace

from redpacket_watch import compute_sleep_seconds, determine_sleep_seconds, run_watch_loop


class _StopLoop(Exception):
    pass


def test_compute_sleep_seconds_uses_wake_lead_when_far_away():
    assert compute_sleep_seconds(10375, wake_lead_seconds=60, minimum_sleep_seconds=5) == 10315


def test_compute_sleep_seconds_never_goes_below_minimum():
    assert compute_sleep_seconds(20, wake_lead_seconds=60, minimum_sleep_seconds=5) == 5


def test_compute_sleep_seconds_handles_missing_next_seconds():
    assert compute_sleep_seconds(None, wake_lead_seconds=60, minimum_sleep_seconds=7) == 7


def test_determine_sleep_seconds_after_join_uses_next_packet_schedule_not_window_poll():
    result = {
        'joined': True,
        'overview': {
            'active': [{'id': 'packet-1'}],
            'next_packet_seconds': 600,
        },
    }

    assert determine_sleep_seconds(result, wake_lead_seconds=60, window_poll_seconds=8, minimum_sleep_seconds=5) == 540


def test_determine_sleep_seconds_uses_window_poll_during_active_window_without_join():
    result = {
        'joined': False,
        'overview': {
            'active': [{'id': 'packet-1'}],
            'next_packet_seconds': 600,
        },
    }

    assert determine_sleep_seconds(result, wake_lead_seconds=60, window_poll_seconds=8, minimum_sleep_seconds=5) == 8


def test_determine_sleep_seconds_treats_already_joined_like_joined():
    result = {
        'status': 'already_joined',
        'joined': False,
        'overview': {
            'active': [{'id': 'packet-1'}],
            'next_packet_seconds': 600,
        },
    }

    assert determine_sleep_seconds(result, wake_lead_seconds=60, window_poll_seconds=8, minimum_sleep_seconds=5) == 540


def test_determine_sleep_seconds_handles_join_without_next_packet_hint():
    result = {
        'joined': True,
        'overview': {
            'active': [{'id': 'packet-1'}],
        },
    }

    assert determine_sleep_seconds(result, wake_lead_seconds=60, window_poll_seconds=8, minimum_sleep_seconds=5) == 5


def test_determine_sleep_seconds_uses_next_packet_schedule_when_idle():
    result = {
        'joined': False,
        'overview': {
            'active': [],
            'next_packet_seconds': 120,
        },
    }

    assert determine_sleep_seconds(result, wake_lead_seconds=60, window_poll_seconds=8, minimum_sleep_seconds=5) == 60


def test_run_watch_loop_continues_after_exception(monkeypatch):
    results = [RuntimeError('temporary join failure'), {'status': 'idle', 'joined': False, 'overview': {'active': [], 'next_packet_seconds': 120, 'next_packet_at': '2026-04-17T00:02:00+00:00'}}]
    sleep_calls = []
    printed = []
    settings = SimpleNamespace(log_dir='.', state_dir='.', lock_file='.')
    args = SimpleNamespace(dry_run=False, wake_lead_seconds=60, window_poll_seconds=8, idle_minimum_seconds=5, error_backoff_seconds=11)

    def fake_run_redpacket(client, store, dry_run=False):
        item = results.pop(0)
        if isinstance(item, Exception):
            raise item
        raise_after = item
        return raise_after

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise _StopLoop()

    monkeypatch.setattr('redpacket_watch.run_redpacket', fake_run_redpacket)
    monkeypatch.setattr('redpacket_watch.time.sleep', fake_sleep)
    monkeypatch.setattr(builtins, 'print', lambda message, flush=True: printed.append(message))

    try:
        run_watch_loop(client=object(), store=object(), log=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None), args=args)
    except _StopLoop:
        pass

    assert sleep_calls == [11, 60]
    assert len(printed) == 1


def test_run_watch_loop_logs_and_serializes_successful_result(monkeypatch):
    result = {'status': 'joined', 'joined': True, 'overview': {'active': [{'id': 'packet-1'}], 'next_packet_seconds': 300, 'next_packet_at': '2026-04-17T00:05:00+00:00'}}
    printed = []
    sleep_calls = []
    log_records = {'info': [], 'warning': []}
    args = SimpleNamespace(dry_run=False, wake_lead_seconds=60, window_poll_seconds=8, idle_minimum_seconds=5, error_backoff_seconds=11)

    monkeypatch.setattr('redpacket_watch.run_redpacket', lambda client, store, dry_run=False: result)

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise _StopLoop()

    monkeypatch.setattr('redpacket_watch.time.sleep', fake_sleep)
    monkeypatch.setattr(builtins, 'print', lambda message, flush=True: printed.append(message))

    log = SimpleNamespace(
        info=lambda *a, **k: log_records['info'].append(a),
        warning=lambda *a, **k: log_records['warning'].append(a),
    )

    try:
        run_watch_loop(client=object(), store=object(), log=log, args=args)
    except _StopLoop:
        pass

    assert printed and '"status": "joined"' in printed[0]
    assert sleep_calls == [240]
    assert any('next_redpacket status=%s next_packet_at=%s sleep_seconds=%s wake_lead_seconds=%s active=%s joined=%s' in call[0] for call in log_records['info'])
    assert log_records['warning'] == []
