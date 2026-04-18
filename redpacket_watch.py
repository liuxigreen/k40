from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any

from client import AgentHansaClient
from config import load_settings
from logger import setup_logging
from state import JsonStateStore
from utils.lock import SingleInstanceLock
from tasks.redpacket import run as run_redpacket


def compute_sleep_seconds(next_packet_seconds: int | None, wake_lead_seconds: int = 60, minimum_sleep_seconds: int = 5) -> int:
    if next_packet_seconds is None:
        return minimum_sleep_seconds
    return max(minimum_sleep_seconds, int(next_packet_seconds) - int(wake_lead_seconds))


def determine_sleep_seconds(
    result: dict[str, Any],
    wake_lead_seconds: int = 60,
    window_poll_seconds: int = 8,
    minimum_sleep_seconds: int = 5,
) -> int:
    overview = result.get('overview') or {}
    treated_as_joined = bool(result.get('joined')) or result.get('status') == 'already_joined'
    if treated_as_joined:
        return compute_sleep_seconds(
            overview.get('next_packet_seconds'),
            wake_lead_seconds=wake_lead_seconds,
            minimum_sleep_seconds=minimum_sleep_seconds,
        )
    if overview.get('active'):
        return int(window_poll_seconds)
    return compute_sleep_seconds(
        overview.get('next_packet_seconds'),
        wake_lead_seconds=wake_lead_seconds,
        minimum_sleep_seconds=minimum_sleep_seconds,
    )


def run_watch_loop(client: AgentHansaClient, store: JsonStateStore, log: logging.Logger, args: Any) -> None:
    while True:
        try:
            result = run_redpacket(client, store, dry_run=args.dry_run)
        except Exception as exc:
            log.warning('redpacket_watch_cycle_failed error=%s backoff_seconds=%s', exc, args.error_backoff_seconds)
            time.sleep(args.error_backoff_seconds)
            continue
        print(json.dumps(result, ensure_ascii=False), flush=True)
        overview = result.get('overview') or {}
        sleep_for = determine_sleep_seconds(
            result,
            wake_lead_seconds=args.wake_lead_seconds,
            window_poll_seconds=args.window_poll_seconds,
            minimum_sleep_seconds=args.idle_minimum_seconds,
        )
        log.info(
            'next_redpacket status=%s next_packet_at=%s sleep_seconds=%s wake_lead_seconds=%s active=%s joined=%s',
            result.get('status'),
            overview.get('next_packet_at'),
            sleep_for,
            args.wake_lead_seconds,
            bool(overview.get('active')),
            bool(result.get('joined')),
        )
        time.sleep(sleep_for)


def main() -> int:
    parser = argparse.ArgumentParser(description='Dedicated AgentHansa red packet watcher')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--wake-lead-seconds', type=int, default=60)
    parser.add_argument('--window-poll-seconds', type=int, default=8)
    parser.add_argument('--idle-minimum-seconds', type=int, default=5)
    parser.add_argument('--error-backoff-seconds', type=int, default=15)
    args = parser.parse_args()

    settings = load_settings()
    log = setup_logging(settings.log_dir)
    lock = SingleInstanceLock(settings.lock_file.with_name('agenthansa_redpacket.lock'))
    try:
        lock.acquire()
    except RuntimeError as exc:
        logging.getLogger('redpacket_watch').warning('redpacket_watch_exit reason=%s', exc)
        return 0
    store = JsonStateStore(settings.state_dir)
    client = AgentHansaClient(settings)

    log = logging.getLogger('redpacket_watch')
    log.info('redpacket_watch_start dry_run=%s', args.dry_run)

    try:
        run_watch_loop(client, store, log, args)
    finally:
        client.close()
        lock.release()


if __name__ == '__main__':
    raise SystemExit(main())
