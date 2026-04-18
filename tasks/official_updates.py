from __future__ import annotations

from typing import Any

from config import Settings
from official_watch import run as run_official_watch
from notification_watch import run as run_notification_watch
from client import AgentHansaClient
from state import JsonStateStore


def run(settings: Settings, client: AgentHansaClient, store: JsonStateStore) -> dict[str, Any]:
    return {
        'official_watch': run_official_watch(settings, store),
        'notifications': run_notification_watch(client, store, mark_read=False),
    }
