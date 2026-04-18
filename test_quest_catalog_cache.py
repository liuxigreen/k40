from pathlib import Path

from config import load_settings
from state import JsonStateStore
from tasks import alliance_voting, quests
from tasks.quest_catalog_cache import load_quest_catalog, save_quest_catalog


class DummyClient:
    def __init__(self):
        self.calls = []

    def get(self, path: str):
        self.calls.append(path)
        if path == '/alliance-war/quests':
            return {'quests': [{'id': 'q1', 'title': 'Quest 1', 'status': 'voting'}]}
        if path == '/agents/feed':
            return {'quests': [{'id': 'q1', 'title': 'Quest 1', 'reward': '$10', 'status': 'open'}]}
        if path == '/alliance-war/quests/q1/submissions':
            return {'submissions': []}
        raise AssertionError(f'unexpected path: {path}')


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
""".strip(),
        encoding='utf-8',
    )
    return load_settings(str(cfg))


def test_load_quest_catalog_uses_fresh_cache_without_http(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    save_quest_catalog(store, {'quests': [{'id': 'cached'}]})
    client = DummyClient()

    data = load_quest_catalog(client, store)

    assert data['quests'][0]['id'] == 'cached'
    assert client.calls == []


def test_quests_run_reuses_cached_catalog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    save_quest_catalog(store, {'quests': [{'id': 'q1', 'title': 'Quest 1', 'status': 'voting'}]})
    client = DummyClient()

    result = quests.run(client, store)

    assert result['quest_catalog']['quests'][0]['id'] == 'q1'
    assert '/alliance-war/quests' not in client.calls
    assert client.calls == ['/agents/feed']


def test_alliance_voting_run_reuses_cached_catalog(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = _settings(tmp_path)
    store = JsonStateStore(settings.state_dir)
    save_quest_catalog(store, {'quests': [{'id': 'q1', 'title': 'Quest 1', 'status': 'voting'}]})
    client = DummyClient()

    result = alliance_voting.run(client, store)

    assert result['suggestions'][0]['quest_id'] == 'q1'
    assert client.calls == ['/alliance-war/quests/q1/submissions']
