from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


def _expand(path_str: str) -> Path:
    return Path(path_str).expanduser().resolve()


def _read_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _read_agent_json_fallback() -> str | None:
    path = Path.home() / '.config' / 'agenthansa' / 'agent.json'
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    value = obj.get('api_key')
    return value.strip() if isinstance(value, str) and value.strip() else None


@dataclass(slots=True)
class Settings:
    api_key: str
    base_url: str
    timezone: str
    poll_seconds: int
    state_dir: Path
    log_dir: Path
    data_dir: Path
    report_dir: Path
    lock_file: Path
    enable_checkin: bool
    enable_red_packet: bool
    use_redpacket_watcher: bool
    enable_official_watch: bool
    enable_notifications: bool
    enable_voting_suggestions: bool
    enable_forum_automation: bool
    enable_submission_autofix: bool
    enable_publish_pipeline: bool
    publish_queue_limit: int
    notify_telegram: bool
    telegram_bot_token: str
    telegram_chat_id: str
    notify_prefix: str
    official_watch_hours: int
    status_report_minutes: int
    leaderboard_minutes: int
    feed_minutes: int
    submissions_minutes: int
    red_packet_fallback_minutes: int
    snapshot_guard_minutes: int
    forum_xp_soft_cap: int
    forum_xp_hard_cap: int
    daily_comment_limit: int
    daily_post_limit: int
    max_http_retries: int
    http_timeout_seconds: int
    devto_api_key: str
    x_auth_token: str
    x_ct0: str
    config_file: Path | None


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def load_settings(config_path: str | None = None) -> Settings:
    env_path = Path.cwd() / '.env'
    env = _read_env_file(env_path)
    secret_env_path = Path.home() / '.secrets' / 'agenthansa.env'
    secret_env = _read_env_file(secret_env_path)

    yaml_data: dict[str, Any] = {}
    cfg_file: Path | None = None
    if config_path:
        cfg_file = _expand(config_path)
    else:
        default_yaml = Path.cwd() / 'config.yaml'
        if default_yaml.exists():
            cfg_file = default_yaml
    if cfg_file and cfg_file.exists() and yaml is not None:
        yaml_data = yaml.safe_load(cfg_file.read_text(encoding='utf-8')) or {}

    def pick(name: str, default: Any = None) -> Any:
        return os.getenv(name, env.get(name, secret_env.get(name, yaml_data.get(name, default))))

    api_key = pick('AGENTHANSA_API_KEY') or _read_agent_json_fallback()
    if not api_key:
        raise RuntimeError('Missing AGENTHANSA_API_KEY. Set it in .env, ~/.secrets/agenthansa.env, or ~/.config/agenthansa/agent.json')

    settings = Settings(
        api_key=api_key,
        base_url=str(pick('AGENTHANSA_BASE_URL', 'https://www.agenthansa.com/api')).rstrip('/'),
        timezone=str(pick('AGENTHANSA_BOT_TZ', 'UTC')),
        poll_seconds=int(pick('AGENTHANSA_BOT_POLL_SECONDS', 180)),
        state_dir=_expand(str(pick('AGENTHANSA_BOT_STATE_DIR', '~/.agenthansa_bot/state'))),
        log_dir=_expand(str(pick('AGENTHANSA_BOT_LOG_DIR', '~/.agenthansa_bot/logs'))),
        data_dir=_expand(str(pick('AGENTHANSA_BOT_DATA_DIR', '~/.agenthansa_bot/data'))),
        report_dir=_expand(str(pick('AGENTHANSA_BOT_REPORT_DIR', '~/.agenthansa_bot/reports'))),
        lock_file=_expand(str(pick('AGENTHANSA_BOT_LOCK_FILE', '~/.agenthansa_bot/agenthansa_bot.lock'))),
        enable_checkin=_truthy(pick('AGENTHANSA_ENABLE_CHECKIN', 1), True),
        enable_red_packet=_truthy(pick('AGENTHANSA_ENABLE_RED_PACKET', 1), True),
        use_redpacket_watcher=_truthy(pick('AGENTHANSA_USE_REDPACKET_WATCHER', 0), False),
        enable_official_watch=_truthy(pick('AGENTHANSA_ENABLE_OFFICIAL_WATCH', 1), True),
        enable_notifications=_truthy(pick('AGENTHANSA_ENABLE_NOTIFICATIONS', 1), True),
        enable_voting_suggestions=_truthy(pick('AGENTHANSA_ENABLE_VOTING_SUGGESTIONS', 1), True),
        enable_forum_automation=_truthy(pick('AGENTHANSA_ENABLE_FORUM_AUTOMATION', 0), False),
        enable_submission_autofix=_truthy(pick('AGENTHANSA_ENABLE_SUBMISSION_AUTOFIX', 0), False),
        enable_publish_pipeline=_truthy(pick('AGENTHANSA_ENABLE_PUBLISH_PIPELINE', 0), False),
        publish_queue_limit=int(pick('AGENTHANSA_PUBLISH_QUEUE_LIMIT', 8)),
        notify_telegram=_truthy(pick('AGENTHANSA_NOTIFY_TELEGRAM', 0), False),
        telegram_bot_token=str(pick('AGENTHANSA_TELEGRAM_BOT_TOKEN', '')),
        telegram_chat_id=str(pick('AGENTHANSA_TELEGRAM_CHAT_ID', '')),
        notify_prefix=str(pick('AGENTHANSA_NOTIFY_PREFIX', '')),
        official_watch_hours=int(pick('AGENTHANSA_OFFICIAL_WATCH_HOURS', 12)),
        status_report_minutes=int(pick('AGENTHANSA_STATUS_REPORT_MINUTES', 30)),
        leaderboard_minutes=int(pick('AGENTHANSA_LEADERBOARD_MINUTES', 15)),
        feed_minutes=int(pick('AGENTHANSA_FEED_MINUTES', 15)),
        submissions_minutes=int(pick('AGENTHANSA_SUBMISSIONS_MINUTES', 20)),
        red_packet_fallback_minutes=int(pick('AGENTHANSA_RED_PACKET_FALLBACK_MINUTES', 10)),
        snapshot_guard_minutes=int(pick('AGENTHANSA_SNAPSHOT_GUARD_MINUTES', 90)),
        forum_xp_soft_cap=int(pick('AGENTHANSA_FORUM_XP_SOFT_CAP', 140)),
        forum_xp_hard_cap=int(pick('AGENTHANSA_FORUM_XP_HARD_CAP', 200)),
        daily_comment_limit=int(pick('AGENTHANSA_DAILY_COMMENT_LIMIT', 2)),
        daily_post_limit=int(pick('AGENTHANSA_DAILY_POST_LIMIT', 1)),
        max_http_retries=int(pick('AGENTHANSA_MAX_HTTP_RETRIES', 4)),
        http_timeout_seconds=int(pick('AGENTHANSA_HTTP_TIMEOUT_SECONDS', 25)),
        devto_api_key=str(pick('AGENTHANSA_DEVTO_API_KEY', '')),
        x_auth_token=str(pick('AGENTHANSA_X_AUTH_TOKEN', '')),
        x_ct0=str(pick('AGENTHANSA_X_CT0', '')),
        config_file=cfg_file,
    )

    for directory in (settings.state_dir, settings.log_dir, settings.data_dir, settings.report_dir):
        directory.mkdir(parents=True, exist_ok=True)
    settings.lock_file.parent.mkdir(parents=True, exist_ok=True)
    return settings
