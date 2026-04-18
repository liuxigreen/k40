from __future__ import annotations

import argparse
import json
from typing import Any

from client import AgentHansaClient
from config import Settings, load_settings
from notification_watch import run as run_notification_watch
from official_watch import run as run_official_watch
from state import JsonStateStore
from tasks import (
    alliance_voting,
    checkin,
    daily_xp,
    decision_engine,
    feed,
    forum_curation,
    forum_strategy,
    leaderboard,
    my_submissions,
    publish_submit_bridge,
    publish_submission_execute,
    publish_external,
    publishing_queue,
    quests,
    redpacket,
    status_report,
)


class RecordingClient:
    def __init__(self, client: Any):
        self._client = client
        self.calls: list[dict[str, Any]] = []

    def get(self, path: str, **kwargs: Any) -> Any:
        self.calls.append({'method': 'GET', 'path': path, 'kwargs': kwargs})
        return self._client.get(path, **kwargs)

    def post(self, path: str, json: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        self.calls.append({'method': 'POST', 'path': path, 'json': json, 'kwargs': kwargs})
        return self._client.post(path, json=json, **kwargs)

    def patch(self, path: str, json: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        self.calls.append({'method': 'PATCH', 'path': path, 'json': json, 'kwargs': kwargs})
        return self._client.patch(path, json=json, **kwargs)

    def close(self) -> None:
        close = getattr(self._client, 'close', None)
        if callable(close):
            close()


def _task_result(name: str, fn) -> dict[str, Any]:
    try:
        result = fn()
        return {'ok': True, 'result': result}
    except Exception as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}


def run_safe_backtest(
    settings: Settings,
    store: JsonStateStore,
    *,
    client: Any | None = None,
    include_redpacket: bool = False,
) -> dict[str, Any]:
    owned_client = client is None
    base_client = client or AgentHansaClient(settings)
    wrapped_client = RecordingClient(base_client)
    tasks_out: dict[str, dict[str, Any]] = {}

    tasks_out['notifications'] = _task_result('notifications', lambda: run_notification_watch(wrapped_client, store, mark_read=False))
    if settings.enable_official_watch:
        tasks_out['official_watch'] = _task_result('official_watch', lambda: run_official_watch(settings, store))
    else:
        tasks_out['official_watch'] = {'ok': True, 'result': {'status': 'disabled'}}
    tasks_out['checkin'] = _task_result('checkin', lambda: checkin.run(wrapped_client, store, dry_run=True))
    tasks_out['feed'] = _task_result('feed', lambda: feed.run(wrapped_client, store))
    tasks_out['daily_xp'] = _task_result('daily_xp', lambda: daily_xp.run(wrapped_client, store))
    tasks_out['leaderboard'] = _task_result('leaderboard', lambda: leaderboard.run(wrapped_client, store))
    if include_redpacket or not getattr(settings, 'use_redpacket_watcher', False):
        tasks_out['redpacket'] = _task_result('redpacket', lambda: redpacket.run(wrapped_client, store, dry_run=True))
    else:
        tasks_out['redpacket'] = {'ok': True, 'result': {'status': 'dry_run_skipped_by_watcher_mode'}}
    tasks_out['quests'] = _task_result('quests', lambda: quests.run(wrapped_client, store))
    tasks_out['my_submissions'] = _task_result('my_submissions', lambda: my_submissions.run(wrapped_client, store))
    if settings.enable_voting_suggestions:
        tasks_out['alliance_voting'] = _task_result('alliance_voting', lambda: alliance_voting.run(wrapped_client, store))
    else:
        tasks_out['alliance_voting'] = {'ok': True, 'result': {'status': 'disabled'}}
    tasks_out['forum_strategy'] = _task_result('forum_strategy', lambda: forum_strategy.run(settings, wrapped_client, store))
    if settings.enable_forum_automation:
        tasks_out['forum_curation'] = _task_result('forum_curation', lambda: forum_curation.run(settings, wrapped_client, store, dry_run=True))
    else:
        tasks_out['forum_curation'] = {'ok': True, 'result': {'status': 'disabled', 'dry_run': True}}
    if settings.enable_publish_pipeline:
        tasks_out['publishing_queue'] = _task_result('publishing_queue', lambda: publishing_queue.run(settings, store))
        tasks_out['publish_external'] = _task_result('publish_external', lambda: publish_external.run(settings, store, dry_run=True))
        tasks_out['publish_submit_bridge'] = _task_result('publish_submit_bridge', lambda: publish_submit_bridge.run(settings, store))
        tasks_out['publish_submission_execute'] = _task_result('publish_submission_execute', lambda: publish_submission_execute.run(settings, wrapped_client, store, dry_run=True))
    else:
        tasks_out['publishing_queue'] = {'ok': True, 'result': {'status': 'disabled'}}
        tasks_out['publish_external'] = {'ok': True, 'result': {'status': 'disabled'}}
        tasks_out['publish_submit_bridge'] = {'ok': True, 'result': {'status': 'disabled'}}
        tasks_out['publish_submission_execute'] = {'ok': True, 'result': {'status': 'disabled'}}
    tasks_out['decision_engine'] = _task_result('decision_engine', lambda: decision_engine.run(settings, store))
    tasks_out['status_report'] = _task_result('status_report', lambda: status_report.run(settings, store))

    post_calls = [call for call in wrapped_client.calls if call['method'] == 'POST']
    patch_calls = [call for call in wrapped_client.calls if call['method'] == 'PATCH']
    errors = {name: item['error'] for name, item in tasks_out.items() if not item.get('ok')}
    result = {
        'tasks': tasks_out,
        'http_calls': wrapped_client.calls,
        'summary': {
            'task_count': len(tasks_out),
            'error_count': len(errors),
            'errors': errors,
            'get_call_count': len([call for call in wrapped_client.calls if call['method'] == 'GET']),
            'post_call_count': len(post_calls),
            'patch_call_count': len(patch_calls),
        },
    }

    store.save('safe_backtest', {
        'summary': result['summary'],
        'tasks': {name: item.get('result') if item.get('ok') else {'error': item.get('error')} for name, item in tasks_out.items()},
        'http_calls': wrapped_client.calls,
    })
    if owned_client:
        wrapped_client.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description='Lock-free safe backtest for AgentHansa tasks')
    parser.add_argument('--include-redpacket', action='store_true', help='Include redpacket task in dry-run mode even when watcher mode is enabled')
    args = parser.parse_args()

    settings = load_settings()
    store = JsonStateStore(settings.state_dir)
    result = run_safe_backtest(settings, store, include_redpacket=args.include_redpacket)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['summary']['error_count'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
