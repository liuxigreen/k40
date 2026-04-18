from config import load_settings
from scheduler import Job
from main import should_schedule_redpacket_job


def test_job_uses_boost_interval_inside_snapshot_guard():
    job = Job(
        name='leaderboard',
        interval_seconds=900,
        boost_interval_seconds=300,
        boost_when_snapshot_guard=True,
        fn=lambda: None,
    )
    assert job.effective_interval(True) == 300
    assert job.effective_interval(False) == 900


def test_job_due_respects_boost_interval():
    job = Job(
        name='leaderboard',
        interval_seconds=900,
        boost_interval_seconds=300,
        boost_when_snapshot_guard=True,
        fn=lambda: None,
        last_run_epoch=100,
    )
    assert job.due(450, True) is True
    assert job.due(450, False) is False


def test_should_schedule_redpacket_job_disabled_when_dedicated_watcher_enabled(tmp_path, monkeypatch):
    cfg = tmp_path / 'config.yaml'
    cfg.write_text(
        """
AGENTHANSA_API_KEY: <your-agenthansa-api-key>
AGENTHANSA_BOT_STATE_DIR: ./state
AGENTHANSA_BOT_LOG_DIR: ./logs
AGENTHANSA_BOT_DATA_DIR: ./data
AGENTHANSA_BOT_REPORT_DIR: ./reports
AGENTHANSA_BOT_LOCK_FILE: ./bot.lock
AGENTHANSA_ENABLE_RED_PACKET: true
AGENTHANSA_USE_REDPACKET_WATCHER: true
""".strip(),
        encoding='utf-8',
    )
    monkeypatch.chdir(tmp_path)
    settings = load_settings(str(cfg))

    assert should_schedule_redpacket_job(settings) is False


def test_should_schedule_redpacket_job_enabled_without_dedicated_watcher(tmp_path, monkeypatch):
    cfg = tmp_path / 'config.yaml'
    cfg.write_text(
        """
AGENTHANSA_API_KEY: <your-agenthansa-api-key>
AGENTHANSA_BOT_STATE_DIR: ./state
AGENTHANSA_BOT_LOG_DIR: ./logs
AGENTHANSA_BOT_DATA_DIR: ./data
AGENTHANSA_BOT_REPORT_DIR: ./reports
AGENTHANSA_BOT_LOCK_FILE: ./bot.lock
AGENTHANSA_ENABLE_RED_PACKET: true
AGENTHANSA_USE_REDPACKET_WATCHER: false
""".strip(),
        encoding='utf-8',
    )
    monkeypatch.chdir(tmp_path)
    settings = load_settings(str(cfg))

    assert should_schedule_redpacket_job(settings) is True
