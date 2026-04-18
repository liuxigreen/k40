from __future__ import annotations

from datetime import datetime, timedelta, timezone

# AgentHansa docs/user spec define the daily snapshot boundary as midnight PST = 08:00 UTC.
# Use that fixed UTC boundary directly so this script works on Termux hosts without tzdata.
PST_SNAPSHOT_HOUR_UTC = 8


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_zone(dt: datetime, zone_name: str) -> datetime:
    # Kept only for compatibility; this project avoids host tzdata requirements.
    return dt


def _snapshot_day_start_utc(now: datetime) -> datetime:
    candidate = now.replace(hour=PST_SNAPSHOT_HOUR_UTC, minute=0, second=0, microsecond=0)
    if now < candidate:
        candidate -= timedelta(days=1)
    return candidate


def pst_date_key(now: datetime | None = None) -> str:
    now = now or utc_now()
    return _snapshot_day_start_utc(now).strftime('%Y-%m-%d')


def minutes_until_pst_midnight(now: datetime | None = None) -> int:
    now = now or utc_now()
    current_start = _snapshot_day_start_utc(now)
    next_start = current_start + timedelta(days=1)
    delta = next_start - now
    return max(0, int(delta.total_seconds() // 60))


def snapshot_time_label() -> str:
    return 'midnight PST (08:00 UTC)'
