from __future__ import annotations

import argparse
import logging
from contextlib import suppress

from client import AgentHansaClient
from config import load_settings
from logger import setup_logging
from official_watch import run as run_official_watch
from notification_watch import run as run_notification_watch
from scheduler import Scheduler
from state import JsonStateStore
from utils.lock import SingleInstanceLock
from tasks import checkin, feed, daily_xp, leaderboard, redpacket, quests, my_submissions, alliance_voting, forum_strategy, forum_curation, publishing_queue, publish_external, publish_submit_bridge, publish_submission_execute, status_report, decision_engine


def should_schedule_redpacket_job(settings) -> bool:
    return bool(settings.enable_red_packet and not getattr(settings, 'use_redpacket_watcher', False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Compliance-first AgentHansa automation bot')
    parser.add_argument('--once', action='store_true', help='Run one cycle and exit')
    parser.add_argument('--dry-run', action='store_true', help='Avoid mutating actions such as check-in or red packet join')
    parser.add_argument('--config', help='Optional config.yaml path')
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = load_settings(args.config)
    log = setup_logging(settings.log_dir)
    lock = SingleInstanceLock(settings.lock_file)
    try:
        lock.acquire()
    except RuntimeError as exc:
        log.warning('main_exit reason=%s', exc)
        return 0
    log.info('cycle_start once=%s dry_run=%s', args.once, args.dry_run)

    store = JsonStateStore(settings.state_dir)
    client = AgentHansaClient(settings)

    def one_cycle() -> None:
        if settings.enable_official_watch:
            run_official_watch(settings, store)
        if settings.enable_notifications:
            run_notification_watch(client, store, mark_read=False)
        if settings.enable_checkin:
            checkin.run(client, store, dry_run=args.dry_run)
        feed.run(client, store)
        daily_xp.run(client, store)
        leaderboard.run(client, store)
        if should_schedule_redpacket_job(settings):
            redpacket.run(client, store, dry_run=args.dry_run)
        quests.run(client, store)
        my_submissions.run(client, store)
        if settings.enable_voting_suggestions:
            alliance_voting.run(client, store)
        forum_strategy.run(settings, client, store)
        if settings.enable_forum_automation:
            forum_curation.run(settings, client, store, dry_run=args.dry_run)
        if settings.enable_publish_pipeline:
            publishing_queue.run(settings, store)
            publish_external.run(settings, store, dry_run=args.dry_run)
            publish_submit_bridge.run(settings, store)
            publish_submission_execute.run(settings, client, store, dry_run=args.dry_run)
        decision_engine.run(settings, store)
        status_report.run(settings, store)

    try:
        if args.once:
            one_cycle()
            return 0

        scheduler = Scheduler(settings)
        scheduler.add_job('official_watch', settings.official_watch_hours * 3600, lambda: run_official_watch(settings, store))
        scheduler.add_job('notifications', 15 * 60, lambda: run_notification_watch(client, store, mark_read=False), boost_interval_seconds=5 * 60, boost_when_snapshot_guard=True)
        scheduler.add_job('checkin', 6 * 3600, lambda: checkin.run(client, store, dry_run=args.dry_run))
        scheduler.add_job('feed', settings.feed_minutes * 60, lambda: feed.run(client, store), boost_interval_seconds=5 * 60, boost_when_snapshot_guard=True)
        scheduler.add_job('daily_xp', 15 * 60, lambda: daily_xp.run(client, store), boost_interval_seconds=5 * 60, boost_when_snapshot_guard=True)
        scheduler.add_job('leaderboard', settings.leaderboard_minutes * 60, lambda: leaderboard.run(client, store), boost_interval_seconds=5 * 60, boost_when_snapshot_guard=True)
        if should_schedule_redpacket_job(settings):
            scheduler.add_job('redpacket', settings.red_packet_fallback_minutes * 60, lambda: redpacket.run(client, store, dry_run=args.dry_run), boost_interval_seconds=2 * 60, boost_when_snapshot_guard=True)
        scheduler.add_job('quests', 20 * 60, lambda: quests.run(client, store), boost_interval_seconds=10 * 60, boost_when_snapshot_guard=True)
        scheduler.add_job('my_submissions', settings.submissions_minutes * 60, lambda: my_submissions.run(client, store), boost_interval_seconds=10 * 60, boost_when_snapshot_guard=True)
        scheduler.add_job('alliance_voting', 30 * 60, lambda: alliance_voting.run(client, store), boost_interval_seconds=10 * 60, boost_when_snapshot_guard=True)
        scheduler.add_job('forum_strategy', 30 * 60, lambda: forum_strategy.run(settings, client, store), boost_interval_seconds=10 * 60, boost_when_snapshot_guard=True)
        if settings.enable_forum_automation:
            scheduler.add_job('forum_curation', 15 * 60, lambda: forum_curation.run(settings, client, store, dry_run=args.dry_run), boost_interval_seconds=5 * 60, boost_when_snapshot_guard=True)
        if settings.enable_publish_pipeline:
            scheduler.add_job('publishing_queue', 20 * 60, lambda: publishing_queue.run(settings, store), boost_interval_seconds=5 * 60, boost_when_snapshot_guard=True)
            scheduler.add_job('publish_external', 20 * 60, lambda: publish_external.run(settings, store, dry_run=args.dry_run), boost_interval_seconds=5 * 60, boost_when_snapshot_guard=True)
            scheduler.add_job('publish_submit_bridge', 20 * 60, lambda: publish_submit_bridge.run(settings, store), boost_interval_seconds=5 * 60, boost_when_snapshot_guard=True)
            scheduler.add_job('publish_submission_execute', 20 * 60, lambda: publish_submission_execute.run(settings, client, store, dry_run=args.dry_run), boost_interval_seconds=5 * 60, boost_when_snapshot_guard=True)
        scheduler.add_job('decision_engine', 15 * 60, lambda: decision_engine.run(settings, store), boost_interval_seconds=5 * 60, boost_when_snapshot_guard=True)
        scheduler.add_job('status_report', settings.status_report_minutes * 60, lambda: status_report.run(settings, store), boost_interval_seconds=10 * 60, boost_when_snapshot_guard=True)
        scheduler.run_forever()
        return 0
    finally:
        with suppress(Exception):
            client.close()
        with suppress(Exception):
            lock.release()


if __name__ == '__main__':
    raise SystemExit(main())
