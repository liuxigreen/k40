from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from config import Settings
from utils.timezone import minutes_until_pst_midnight


@dataclass
class Job:
    name: str
    interval_seconds: int
    fn: Callable[[], None]
    boost_interval_seconds: int | None = None
    boost_when_snapshot_guard: bool = False
    last_run_epoch: float = 0.0

    def effective_interval(self, snapshot_guard_active: bool) -> int:
        if snapshot_guard_active and self.boost_when_snapshot_guard and self.boost_interval_seconds:
            return self.boost_interval_seconds
        return self.interval_seconds

    def due(self, now_epoch: float, snapshot_guard_active: bool) -> bool:
        interval = self.effective_interval(snapshot_guard_active)
        return self.last_run_epoch == 0.0 or (now_epoch - self.last_run_epoch) >= interval

    def run(self, now_epoch: float) -> None:
        self.fn()
        self.last_run_epoch = now_epoch


class Scheduler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.log = logging.getLogger('scheduler')
        self.jobs: list[Job] = []

    def add_job(
        self,
        name: str,
        interval_seconds: int,
        fn: Callable[[], None],
        *,
        boost_interval_seconds: int | None = None,
        boost_when_snapshot_guard: bool = False,
    ) -> None:
        self.jobs.append(
            Job(
                name=name,
                interval_seconds=interval_seconds,
                fn=fn,
                boost_interval_seconds=boost_interval_seconds,
                boost_when_snapshot_guard=boost_when_snapshot_guard,
            )
        )

    def run_forever(self) -> None:
        self.log.info('scheduler_start jobs=%s poll_seconds=%s', len(self.jobs), self.settings.poll_seconds)
        while True:
            now_epoch = time.time()
            guard = self.snapshot_guard_active(self.settings.snapshot_guard_minutes)
            if guard:
                self.log.info('snapshot_guard_active minutes_until_snapshot=%s', minutes_until_pst_midnight())
            for job in self.jobs:
                if job.due(now_epoch, guard):
                    try:
                        job.run(now_epoch)
                    except Exception as exc:
                        self.log.exception('job_failed name=%s error=%s', job.name, exc)
            time.sleep(self.settings.poll_seconds)

    @staticmethod
    def snapshot_guard_active(buffer_minutes: int) -> bool:
        return minutes_until_pst_midnight() <= buffer_minutes
