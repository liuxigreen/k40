from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = Path.home() / '.agenthansa_bot' / 'state'
REPORT_DIR = Path.home() / '.agenthansa_bot' / 'reports'
LOG_DIR = Path.home() / '.agenthansa_bot' / 'logs'

SERVICE_DEFS = {
    'main': {
        'label': '主任务循环',
        'match': 'python main.py',
        'start': "nohup python main.py > ~/.agenthansa_bot/logs/main.stdout.log 2>&1 &",
        'stop': "pkill -f 'python main.py' || true",
    },
    'redpacket_watch': {
        'label': '红包监听',
        'match': 'python redpacket_watch.py',
        'start': "nohup python redpacket_watch.py --error-backoff-seconds 15 > ~/.agenthansa_bot/logs/redpacket_watch.stdout.log 2>&1 &",
        'stop': "pkill -f 'python redpacket_watch.py' || true",
    },
    'web_panel': {
        'label': '面板服务',
        'match': 'python web_panel.py',
        'start': "nohup python web_panel.py --host 127.0.0.1 --port 8765 > ~/.agenthansa_bot/logs/web_panel.out 2>&1 &",
        'stop': "pkill -f 'python web_panel.py' || true",
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')


def _run_shell(command: str, timeout: int = 20) -> dict:
    try:
        proc = subprocess.run(
            ['bash', '-lc', command],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
        return {
            'ok': proc.returncode == 0,
            'returncode': proc.returncode,
            'stdout': proc.stdout[-16000:],
            'stderr': proc.stderr[-16000:],
            'command': command,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            'ok': False,
            'returncode': -1,
            'stdout': (exc.stdout or '')[-16000:] if isinstance(exc.stdout, str) else '',
            'stderr': f'timeout after {timeout}s',
            'command': command,
        }


def _run(command: list[str], timeout: int = 20) -> dict:
    return _run_shell(' '.join(subprocess.list2cmdline([part]) for part in command), timeout=timeout)


def _tail(path: Path, lines: int = 80) -> str:
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return ''
    return '\n'.join(text.splitlines()[-lines:])


def _load_latest() -> dict:
    return _read_json(REPORT_DIR / 'latest_status.json', {})


def _load_decision_plan() -> dict:
    return _read_json(STATE_DIR / 'decision_plan.json', {})


def _load_manual_actions() -> dict:
    return _read_json(STATE_DIR / 'manual_actions.json', {})


def _load_publish_queue() -> dict:
    return _read_json(STATE_DIR / 'publish_queue.json', {})


def _load_safe_backtest() -> dict:
    return _read_json(STATE_DIR / 'safe_backtest.json', {})


def _load_runtime_state() -> dict:
    return _read_json(STATE_DIR / 'runtime_state.json', {})


def _load_redpacket_state() -> dict:
    return _read_json(STATE_DIR / 'redpacket_state.json', {})


def _load_notifications() -> dict:
    return _read_json(STATE_DIR / 'notifications.json', {})


def _slugify_queue_id(queue_id: str) -> str:
    return queue_id.replace('/', '__slash__')


def _unslugify_queue_id(slug: str) -> str:
    return slug.replace('__slash__', '/')


def _manual_action_id(item: dict, index: int) -> str:
    return f"manual-{index}-{str(item.get('type') or 'item').strip() or 'item'}"


def _process_snapshot(name: str) -> dict:
    service = SERVICE_DEFS[name]
    result = _run_shell(f"ps -ef | grep -F {json.dumps(service['match'])} | grep -v grep", timeout=10)
    lines = [line for line in result.get('stdout', '').splitlines() if line.strip()]
    pid = None
    if lines:
        parts = lines[0].split()
        if len(parts) > 1 and parts[1].isdigit():
            pid = int(parts[1])
    return {
        'name': name,
        'label': service['label'],
        'running': bool(lines),
        'pid': pid,
        'line_count': len(lines),
        'lines': lines[-3:],
        'checked_at': _now_iso(),
    }


def _services_payload() -> list[dict]:
    return [_process_snapshot(name) for name in SERVICE_DEFS]


def _status_chip_class(active: bool | None) -> str:
    if active is None:
        return 'neutral'
    return 'good' if active else 'bad'


def _recent_notes(notes: list[str] | None, limit: int = 3) -> list[str]:
    values = [str(note).strip() for note in (notes or []) if str(note).strip()]
    return values[-limit:]


def _load_dashboard_data() -> dict:
    latest = _load_latest()
    decision_plan = _load_decision_plan()
    manual_actions = _load_manual_actions()
    safe_backtest = _load_safe_backtest()
    runtime_state = _load_runtime_state()
    notifications = _load_notifications()
    redpacket_state = _load_redpacket_state()
    publish_queue = _load_publish_queue()

    services = _services_payload()
    by_name = {svc['name']: svc for svc in services}
    hermes_version = _run_shell('hermes --version', timeout=10)
    python_version = _run_shell('python --version', timeout=10)

    top_actions = []
    for item in (decision_plan.get('actions') or [])[:10]:
        payload = item.get('payload') or {}
        top_actions.append({
            'type': item.get('type'),
            'reason': item.get('reason'),
            'priority': item.get('priority'),
            'title': payload.get('title') or payload.get('quest_title') or payload.get('quest_id') or '',
            'payload': payload,
        })

    manual_items = []
    for index, item in enumerate((manual_actions.get('items') or latest.get('manual_actions') or [])[:20]):
        status = str(item.get('status') or 'pending')
        manual_items.append({
            'id': _manual_action_id(item, index),
            'index': index,
            'type': item.get('type'),
            'status': status,
            'reason': item.get('reason'),
            'requirements': item.get('requirements') or [],
            'topics': [x.get('title') for x in (item.get('topic_candidates') or [])[:4] if isinstance(x, dict)],
            'acked_at': item.get('acked_at'),
            'raw': item,
        })

    publish_items = []
    source_items = (publish_queue.get('items') or (latest.get('publish_queue') or {}).get('items') or [])[:30]
    for item in source_items:
        queue_id = str(item.get('queue_id') or item.get('quest_id') or '')
        publish_items.append({
            'id': _slugify_queue_id(queue_id) if queue_id else '',
            'queue_id': queue_id,
            'quest_id': item.get('quest_id'),
            'title': item.get('title') or item.get('quest_id') or '',
            'platform': item.get('platform'),
            'status': item.get('status'),
            'published_url': item.get('published_url'),
            'notes': item.get('notes') or [],
            'recent_notes': _recent_notes(item.get('notes') or []),
            'draft_path': item.get('draft_path'),
            'priority_score': item.get('priority_score'),
            'raw': item,
        })

    publish_summary = dict(publish_queue.get('summary') or {})
    publish_summary['paused'] = bool(publish_queue.get('paused', False))
    publish_summary['queued'] = len(source_items)

    risky_rows = ((latest.get('submissions') or {}).get('risky_rows') or [])[:20]
    red = latest.get('red_packet') or {}
    red_overview = redpacket_state.get('overview') or {}

    return {
        'updated_at': _now_iso(),
        'summary': {
            'generated_at': latest.get('generated_at'),
            'today_points': latest.get('today_points'),
            'alliance_rank': latest.get('alliance_rank'),
            'prize_eligible': latest.get('prize_eligible'),
            'minutes_until_snapshot': latest.get('minutes_until_snapshot'),
            'snapshot_guard_active': latest.get('snapshot_guard_active'),
            'top_action': ((latest.get('decision_plan') or {}).get('summary') or {}).get('highest_priority_type') or ((decision_plan.get('summary') or {}).get('highest_priority_type')),
            'risky_count': (latest.get('submissions') or {}).get('risky_count') or 0,
            'publish_queue': len(source_items),
            'publish_paused': bool(publish_queue.get('paused', False)),
            'manual_action_count': len(manual_items),
            'next_packet_at': red.get('next_packet_at') or red_overview.get('next_packet_at'),
            'redpacket_status': red.get('status') or redpacket_state.get('status') or 'unknown',
            'unread_notifications': latest.get('unread_notifications', len(notifications.get('items') or [])),
            'service_health': {
                'main': by_name.get('main', {}).get('running'),
                'redpacket_watch': by_name.get('redpacket_watch', {}).get('running'),
                'web_panel': by_name.get('web_panel', {}).get('running'),
            },
        },
        'top_actions': top_actions,
        'manual_items': manual_items,
        'publish_items': publish_items,
        'publish_summary': publish_summary,
        'risk_submissions': risky_rows,
        'safe_backtest': safe_backtest.get('summary') or {},
        'runtime_state': runtime_state,
        'services': services,
        'versions': {
            'hermes': hermes_version,
            'python': python_version,
        },
        'logs': {
            'main': _tail(LOG_DIR / 'agenthansa_bot.log', 100),
            'redpacket': _tail(LOG_DIR / 'redpacket_watch.stdout.log', 100),
            'panel': _tail(LOG_DIR / 'web_panel.out', 80),
        },
    }


def _load_dashboard_sections() -> dict:
    data = _load_dashboard_data()
    return {
        'updated_at': data.get('updated_at'),
        'overview': {
            'summary': data.get('summary') or {},
            'top_actions': data.get('top_actions') or [],
            'publish_items': data.get('publish_items') or [],
        },
        'tasks': {
            'manual_items': data.get('manual_items') or [],
            'risk_submissions': data.get('risk_submissions') or [],
        },
        'publish': {
            'publish_summary': data.get('publish_summary') or {},
            'publish_items': data.get('publish_items') or [],
        },
        'logs': data.get('logs') or {},
        'hermes': {
            'safe_backtest': data.get('safe_backtest') or {},
            'versions': data.get('versions') or {},
        },
        'runtime': {
            'services': data.get('services') or [],
            'runtime_state': data.get('runtime_state') or {},
        },
    }


def _resp(ok: bool, data=None, message: str = '', status: int = 200) -> tuple[dict, int]:
    return ({'ok': ok, 'message': message, 'updated_at': _now_iso(), 'data': data or {}}, status)


def _set_publish_paused(paused: bool) -> tuple[dict, int]:
    path = STATE_DIR / 'publish_queue.json'
    queue = _load_publish_queue()
    queue['paused'] = paused
    queue['paused_at'] = _now_iso() if paused else None
    _write_json(path, queue)
    return _resp(True, {'paused': paused, 'summary': queue.get('summary') or {}}, '已更新发布队列状态')


def _update_manual_action(action_id: str) -> tuple[dict, int]:
    data = _load_manual_actions()
    items = list(data.get('items') or [])
    for index, item in enumerate(items):
        if _manual_action_id(item, index) == action_id:
            item['status'] = 'acked'
            item['acked_at'] = _now_iso()
            items[index] = item
            data['items'] = items
            data['updated_at'] = _now_iso()
            _write_json(STATE_DIR / 'manual_actions.json', data)
            return _resp(True, {'id': action_id, 'status': 'acked'}, '人工任务已标记完成')
    return _resp(False, {}, '未找到人工任务', 404)


def _update_publish_item(queue_id_slug: str, action: str) -> tuple[dict, int]:
    queue_id = _unslugify_queue_id(queue_id_slug)
    data = _load_publish_queue()
    items = list(data.get('items') or [])
    for index, item in enumerate(items):
        if str(item.get('queue_id') or '') != queue_id:
            continue
        old_status = str(item.get('status') or '')
        notes = list(item.get('notes') or [])
        ts = _now_iso()
        if action == 'retry':
            item['status'] = 'publish_pending'
            notes.append(f'manual_retry:{ts}')
            message = '已重置为待发布，等待下轮处理'
        elif action == 'approve':
            item['status'] = 'publish_pending'
            item['manual_approved_at'] = ts
            notes.append(f'manual_approved:{ts}')
            message = '已人工批准，等待发布'
        elif action == 'reject':
            item['status'] = 'rejected_manual'
            item['manual_rejected_at'] = ts
            notes.append(f'manual_rejected:{ts}')
            message = '已人工拒绝该发布项'
        else:
            return _resp(False, {}, '未知操作', 400)
        item['notes'] = notes[-50:]
        item['updated_at'] = ts
        items[index] = item
        data['items'] = items
        data['updated_at'] = ts
        _write_json(STATE_DIR / 'publish_queue.json', data)
        return _resp(True, {'queue_id': queue_id, 'old_status': old_status, 'new_status': item['status']}, message)
    return _resp(False, {}, '未找到发布项', 404)


def _safe_backtest_run() -> tuple[dict, int]:
    result = _run_shell('python safe_backtest.py', timeout=180)
    return _resp(result['ok'], result, '安全回放完成' if result['ok'] else '安全回放失败', 200 if result['ok'] else 500)


def _service_control(name: str, action: str) -> tuple[dict, int]:
    if name not in SERVICE_DEFS:
        return _resp(False, {}, '未知服务', 404)
    service = SERVICE_DEFS[name]
    before = _process_snapshot(name)
    if action == 'status':
        return _resp(True, before, '服务状态')
    if action == 'start':
        result = _run_shell(service['start'], timeout=20)
    elif action == 'stop':
        result = _run_shell(service['stop'], timeout=20)
    elif action == 'restart':
        result = _run_shell(f"{service['stop']}; sleep 1; {service['start']}", timeout=30)
    else:
        return _resp(False, {}, '未知动作', 400)
    time.sleep(1)
    after = _process_snapshot(name)
    return _resp(result['ok'], {'before': before, 'after': after, 'result': result}, f'{service["label"]}{action}已执行', 200 if result['ok'] else 500)


def _schedule_self_restart(host: str, port: int) -> None:
    def worker() -> None:
        time.sleep(1)
        _run_shell("pkill -f 'python web_panel.py' || true", timeout=15)
        _run_shell(f"nohup python {BASE_DIR / 'web_panel.py'} --host {host} --port {port} > ~/.agenthansa_bot/logs/web_panel.out 2>&1 &", timeout=15)
    threading.Thread(target=worker, daemon=True).start()


INDEX_HTML = """<!doctype html>
<html lang='zh-CN'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'>
  <title>AgentHansa / Hermes 控制面板</title>
  <style>
    :root {
      --bg: #08101d;
      --card: #111b2f;
      --card2: #17233c;
      --line: #243758;
      --text: #edf3ff;
      --muted: #95a9d1;
      --blue: #4f8cff;
      --green: #2ecc71;
      --yellow: #f5b942;
      --red: #ff6262;
      --shadow: 0 10px 28px rgba(0,0,0,.28);
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Noto Sans SC', sans-serif;
      background: linear-gradient(180deg, #07101d 0%, #0c1627 100%);
      color: var(--text);
      font-size: 14px;
    }
    .wrap { max-width: 880px; margin: 0 auto; padding: 8px 8px 82px; }
    .hero { margin-bottom: 6px; }
    .title { font-size: 19px; font-weight: 800; margin: 2px 0; line-height: 1.15; }
    .sub { color: var(--muted); font-size: 12px; }
    .topbar {
      display: flex; gap: 6px; align-items: center; justify-content: space-between; margin-top: 6px;
      position: sticky; top: 0; z-index: 10; padding: 6px 0; backdrop-filter: blur(12px);
    }
    .chips, .inline { display: flex; gap: 5px; flex-wrap: wrap; }
    .chip {
      display: inline-flex; align-items: center; gap: 4px; font-size: 11px; line-height: 1; color: #dce7ff;
      background: #1a2946; border: 1px solid #2b426d; border-radius: 999px; padding: 4px 8px;
    }
    .chip.good { color: #8bf0b5; border-color: rgba(46,204,113,.35); background: rgba(46,204,113,.12); }
    .chip.warn { color: #ffd57d; border-color: rgba(245,185,66,.35); background: rgba(245,185,66,.12); }
    .chip.bad { color: #ffadad; border-color: rgba(255,98,98,.35); background: rgba(255,98,98,.12); }
    .btn {
      border: 0; border-radius: 12px; padding: 10px 10px; min-height: 40px; color: white; font-size: 13px; line-height: 1.2; font-weight: 750;
      background: linear-gradient(180deg, #4f8cff, #2563eb); box-shadow: var(--shadow);
    }
    .btn.secondary { background: linear-gradient(180deg, #37445d, #233046); }
    .btn.warn { background: linear-gradient(180deg, #efb63f, #d88d06); }
    .btn.danger { background: linear-gradient(180deg, #ff7b7b, #e34d4d); }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .card {
      background: rgba(17, 27, 47, .95); border: 1px solid var(--line); border-radius: 15px; padding: 10px; box-shadow: var(--shadow);
    }
    .card.full { grid-column: 1 / -1; }
    .label { color: var(--muted); font-size: 11px; margin-bottom: 3px; }
    .value { font-size: 21px; font-weight: 800; line-height: 1.05; }
    .small { color: var(--muted); font-size: 11px; }
    .section { display: none; margin-top: 8px; }
    .section.active { display: block; }
    .section-title { font-size: 15px; font-weight: 800; margin: 0 0 8px; }
    .action-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 6px; }
    .list { display: grid; gap: 6px; }
    .item { border: 1px solid var(--line); background: var(--card2); border-radius: 12px; padding: 9px; }
    .item h4 { margin: 0 0 4px; font-size: 13px; line-height: 1.25; }
    .item p { margin: 0; line-height: 1.35; font-size: 12px; color: #c9d7f7; }
    .item .meta { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 5px; }
    .toolbar { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
    .toolbar .btn { flex: 1 1 120px; }
    .status-line { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-bottom: 4px; }
    .note-list { margin-top: 6px; display: grid; gap: 5px; }
    .note { padding: 7px 9px; border-radius: 10px; background: rgba(9,17,31,.7); border: 1px solid #1f304c; font-size: 11px; color: #d8e4ff; line-height: 1.35; }
    .muted { color: var(--muted); }
    .section-loading { padding: 20px 10px; text-align: center; color: var(--muted); font-size: 12px; }
    .section-error { border-color: rgba(255,98,98,.4); }
    .pill-row { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 8px; }
    .link-btn { background: transparent; border: 0; color: #91c7ff; padding: 0; font: inherit; }
    .footer-nav {
      position: fixed; left: 0; right: 0; bottom: 0; z-index: 20; padding: 8px 8px calc(8px + env(safe-area-inset-bottom));
      background: rgba(8, 16, 29, .96); border-top: 1px solid #20304c; backdrop-filter: blur(18px);
    }
    .footer-tabs { max-width: 880px; margin: 0 auto; display: grid; grid-template-columns: repeat(6, minmax(0,1fr)); gap: 5px; }
    .tab {
      border: 1px solid #253756; border-radius: 12px; background: #132038; color: #cfe0ff; min-height: 42px; font-size: 11px; line-height: 1.1; font-weight: 700;
      padding: 6px 4px;
    }
    .tab.active { background: linear-gradient(180deg, #2259c9, #163c82); color: white; }
    pre {
      margin: 0; white-space: pre-wrap; word-break: break-word; font-size: 11px; line-height: 1.35; max-height: 280px; overflow: auto;
      padding: 8px; border-radius: 12px; background: #09111f; border: 1px solid #1f304c; color: #d8e4ff;
    }
    .result { margin-top: 6px; }
    .sticky-actions { position: sticky; bottom: 64px; z-index: 8; margin-top: 8px; }
    .empty { color: var(--muted); font-size: 12px; text-align: center; padding: 14px 8px; }
    a { color: #91c7ff; }
    @media (max-width: 720px) {
      .wrap { padding: 6px 6px 78px; }
      .grid, .action-grid { grid-template-columns: 1fr 1fr; }
      .footer-tabs { grid-template-columns: repeat(3, minmax(0,1fr)); }
      .card.full { grid-column: auto; }
      .topbar { align-items: flex-start; }
      .inline .btn { min-width: 72px; }
    }
    @media (max-width: 420px) {
      .title { font-size: 17px; }
      .value { font-size: 19px; }
      .btn { min-height: 38px; font-size: 12px; padding: 9px 8px; }
      .tab { min-height: 40px; font-size: 10px; }
      .item h4 { font-size: 12px; }
      .item p, .small, .label { font-size: 11px; }
    }
  </style>
</head>
<body>
  <div class='wrap'>
    <div class='hero'>
      <div class='title'>AgentHansa / Hermes 控制面板</div>
      <div class='sub'>手机优先 · 中文 · 可操作版</div>
    </div>
    <div class='topbar'>
      <div class='chips' id='statusChips'></div>
      <div class='inline'>
        <button class='btn secondary' onclick='refreshDashboard()'>刷新</button>
      </div>
    </div>
    <div id='section-overview' class='section active'></div>
    <div id='section-tasks' class='section'></div>
    <div id='section-publish' class='section'></div>
    <div id='section-logs' class='section'></div>
    <div id='section-hermes' class='section'></div>
    <div id='section-runtime' class='section'></div>
    <div id='resultBox' class='result'></div>
  </div>
  <div class='footer-nav'>
    <div class='footer-tabs'>
      <button class='tab active' data-target='overview' onclick='setTab("overview", this)'>总览</button>
      <button class='tab' data-target='tasks' onclick='setTab("tasks", this)'>任务中心</button>
      <button class='tab' data-target='publish' onclick='setTab("publish", this)'>发布队列</button>
      <button class='tab' data-target='logs' onclick='setTab("logs", this)'>日志诊断</button>
      <button class='tab' data-target='hermes' onclick='setTab("hermes", this)'>Hermes常用</button>
      <button class='tab' data-target='runtime' onclick='setTab("runtime", this)'>运行控制</button>
    </div>
  </div>
<script>
let dashboard = null;
function esc(s) {
  return String(s ?? '').replace(/[&<>\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}
async function api(url, options) {
  const res = await fetch(url, options || {});
  const text = await res.text();
  let data = {};
  try { data = JSON.parse(text); } catch { data = {ok:false, message:text}; }
  if (!res.ok) throw new Error(data.message || ('HTTP ' + res.status));
  return data;
}
function chip(text, cls='') { return `<span class="chip ${cls}">${esc(text)}</span>`; }
function renderStatusChips() {
  const s = dashboard.summary || {};
  const html = [
    chip('XP ' + (s.today_points ?? '-')),
    chip('排名 #' + (s.alliance_rank ?? '-')),
    chip((s.snapshot_guard_active ? '快照保护中' : '普通时段'), s.snapshot_guard_active ? 'warn' : 'good'),
    chip('高优先 ' + (s.top_action || '-'), 'bad'),
    chip('main ' + ((s.service_health||{}).main ? '运行中' : '异常'), ((s.service_health||{}).main ? 'good' : 'bad')),
    chip('红包 ' + (s.redpacket_status || '-'), (s.redpacket_status === 'idle' ? 'good' : 'warn')),
    chip('发布' + (s.publish_paused ? '已暂停' : '运行中'), s.publish_paused ? 'warn' : 'good')
  ].join('');
  document.getElementById('statusChips').innerHTML = html;
}
function showResult(data, isError=false) {
  const box = document.getElementById('resultBox');
  box.innerHTML = `<div class="card full"><div class="section-title">执行结果</div><pre>${esc(JSON.stringify(data, null, 2))}</pre></div>`;
  if (!isError) window.scrollTo({top: 0, behavior: 'smooth'});
}
function confirmAction(message) { return window.confirm(message); }
function serviceCard(service) {
  const statusCls = service.running ? 'good' : 'bad';
  const statusLabel = service.running ? '运行中' : '未运行';
  return `
    <div class="item">
      <h4>${esc(service.label)}</h4>
      <p>状态：${esc(statusLabel)}${service.pid ? ' · PID ' + esc(service.pid) : ''}</p>
      <div class="meta">${chip(statusLabel, statusCls)}</div>
      <div class="action-grid" style="margin-top:8px">
        <button class="btn secondary" onclick="serviceAction('${service.name}','status',false)">刷新状态</button>
        <button class="btn secondary" onclick="serviceAction('${service.name}','start',true)">启动</button>
        <button class="btn warn" onclick="serviceAction('${service.name}','restart',true)">重启</button>
        <button class="btn danger" onclick="serviceAction('${service.name}','stop',true)">停止</button>
      </div>
    </div>`;
}
function renderOverview() {
  const s = dashboard.summary || {};
  const topActions = (dashboard.top_actions || []).slice(0, 6).map(item => `
    <div class="item">
      <h4>${esc(item.type || 'unknown')} · 优先级 ${esc(item.priority ?? '-')}</h4>
      <p>${esc(item.reason || '')}</p>
      <p>${item.title ? '目标：' + esc(item.title) : ''}</p>
    </div>`).join('') || '<div class="empty">暂无高优先任务</div>';
  const publishErrors = (dashboard.publish_items || []).filter(x => String(x.status || '').includes('error')).slice(0, 4).map(item => `
    <div class="item">
      <h4>${esc(item.title || item.quest_id || '未命名')}</h4>
      <p>${esc(item.platform || '')} · ${esc(item.status || '')}</p>
      <div class="action-grid" style="margin-top:8px">
        <button class="btn warn" onclick="publishAction('${esc(item.id)}','retry', true)">重试发布</button>
        <button class="btn danger" onclick="publishAction('${esc(item.id)}','reject', true)">拒绝</button>
      </div>
    </div>`).join('') || '<div class="empty">暂无发布失败项</div>';
  document.getElementById('section-overview').innerHTML = `
    <div class="grid">
      <div class="card"><div class="label">当前 XP</div><div class="value">${esc(s.today_points ?? '-')}</div></div>
      <div class="card"><div class="label">联盟排名</div><div class="value">#${esc(s.alliance_rank ?? '-')}</div></div>
      <div class="card"><div class="label">风险提交</div><div class="value">${esc(s.risky_count ?? 0)}</div></div>
      <div class="card"><div class="label">待人工任务</div><div class="value">${esc(s.manual_action_count ?? 0)}</div></div>
      <div class="card"><div class="label">发布队列</div><div class="value">${esc(s.publish_queue ?? 0)}</div><div class="small">${s.publish_paused ? '当前已暂停' : '当前运行中'}</div></div>
      <div class="card"><div class="label">红包状态</div><div class="value" style="font-size:18px">${esc(s.redpacket_status || '-')}</div><div class="small">下次：${esc(s.next_packet_at || '-')}</div></div>
      <div class="card full"><div class="section-title">快捷操作</div>
        <div class="action-grid">
          <button class="btn" onclick="refreshDashboard()">立即刷新</button>
          <button class="btn secondary" onclick="runSafeBacktest(true)">安全回放</button>
          <button class="btn secondary" onclick="setTab('tasks')">看任务中心</button>
          <button class="btn secondary" onclick="setTab('publish')">看发布队列</button>
          <button class="btn secondary" onclick="setTab('logs')">看日志</button>
          <button class="btn warn" onclick="togglePublishQueue(${s.publish_paused ? 'false' : 'true'})">${s.publish_paused ? '恢复发布队列' : '暂停发布队列'}</button>
        </div>
      </div>
      <div class="card full"><div class="section-title">当前重点任务</div><div class="list">${topActions}</div></div>
      <div class="card full"><div class="section-title">需要马上处理的发布失败</div><div class="list">${publishErrors}</div></div>
    </div>`;
}
function renderTasks() {
  const manual = (dashboard.manual_items || []).map(item => `
    <div class="item">
      <h4>${esc(item.type || 'manual')}</h4>
      <p>原因：${esc(item.reason || '-')}</p>
      <p>要求：${esc((item.requirements || []).join('； ') || '无')}</p>
      <p>候选：${esc((item.topics || []).join(' ｜ ') || '无')}</p>
      <div class="action-grid" style="margin-top:8px">
        <button class="btn secondary" onclick="copyText(${JSON.stringify(JSON.stringify(item.raw, null, 2))})">复制详情</button>
        <button class="btn" onclick="ackManual('${esc(item.id)}')">标记已处理</button>
      </div>
    </div>`).join('') || '<div class="empty">暂无人工任务</div>';
  const risky = (dashboard.risk_submissions || []).map(row => `
    <div class="item">
      <h4>${esc(row.quest_title || row.quest_id || '风险提交')}</h4>
      <p>评级：${esc(row.ai_grade || '-')}</p>
      <p>${esc(row.ai_summary || '')}</p>
      ${row.proof_url ? `<p><a href="${esc(row.proof_url)}" target="_blank">打开 proof</a></p>` : ''}
    </div>`).join('') || '<div class="empty">暂无风险提交</div>';
  document.getElementById('section-tasks').innerHTML = `
    <div class="card"><div class="section-title">人工任务</div><div class="list">${manual}</div></div>
    <div class="card" style="margin-top:10px"><div class="section-title">风险提交</div><div class="list">${risky}</div></div>`;
}
function renderPublish() {
  const summary = dashboard.publish_summary || {};
  const items = (dashboard.publish_items || []).map(item => {
    const note = (item.notes || []).slice(-1)[0] || '';
    return `
      <div class="item">
        <h4>${esc(item.title || item.quest_id || '未命名发布')}</h4>
        <p>${esc(item.platform || '')} · ${esc(item.status || '')} · 优先级 ${esc(item.priority_score ?? '-')}</p>
        <p>${esc(note)}</p>
        <div class="meta">${item.published_url ? `<a href="${esc(item.published_url)}" target="_blank">打开链接</a>` : chip('暂无 proof', 'warn')}</div>
        <div class="action-grid" style="margin-top:8px">
          <button class="btn secondary" onclick="copyText(${JSON.stringify(JSON.stringify(item.raw, null, 2))})">复制条目</button>
          <button class="btn warn" onclick="publishAction('${esc(item.id)}','retry', true)">重试</button>
          <button class="btn" onclick="publishAction('${esc(item.id)}','approve', true)">批准</button>
          <button class="btn danger" onclick="publishAction('${esc(item.id)}','reject', true)">拒绝</button>
        </div>
      </div>`;
  }).join('') || '<div class="empty">发布队列为空</div>';
  document.getElementById('section-publish').innerHTML = `
    <div class="card">
      <div class="section-title">队列总览</div>
      <div class="chips">
        ${chip('排队 ' + (summary.queued ?? 0))}
        ${chip('需发布 ' + (summary.publish_required ?? 0))}
        ${chip(summary.paused ? '已暂停' : '运行中', summary.paused ? 'warn' : 'good')}
      </div>
      <div class="action-grid" style="margin-top:10px">
        <button class="btn warn" onclick="togglePublishQueue(true)">暂停队列</button>
        <button class="btn" onclick="togglePublishQueue(false)">恢复队列</button>
      </div>
    </div>
    <div class="card" style="margin-top:10px"><div class="section-title">队列明细</div><div class="list">${items}</div></div>`;
}
function renderLogs() {
  document.getElementById('section-logs').innerHTML = `
    <div class="card"><div class="section-title">主日志</div><pre>${esc((dashboard.logs || {}).main || '暂无')}</pre></div>
    <div class="card" style="margin-top:10px"><div class="section-title">红包日志</div><pre>${esc((dashboard.logs || {}).redpacket || '暂无')}</pre></div>
    <div class="card" style="margin-top:10px"><div class="section-title">面板日志</div><pre>${esc((dashboard.logs || {}).panel || '暂无')}</pre></div>`;
}
function renderHermes() {
  const backtest = dashboard.safe_backtest || {};
  const versions = dashboard.versions || {};
  document.getElementById('section-hermes').innerHTML = `
    <div class="card">
      <div class="section-title">Hermes 常用</div>
      <div class="chips">
        ${chip(String((versions.hermes || {}).stdout || '未知').trim() || '未知')}
        ${chip(String((versions.python || {}).stdout || '未知').trim() || '未知')}
        ${chip('回放错误数 ' + (backtest.error_count ?? '-'), (backtest.error_count || 0) > 0 ? 'bad' : 'good')}
        ${chip('GET ' + (backtest.get_call_count ?? '-'))}
        ${chip('POST ' + (backtest.post_call_count ?? '-'), (backtest.post_call_count || 0) > 0 ? 'warn' : '')}
      </div>
      <div class="action-grid" style="margin-top:10px">
        <button class="btn" onclick="runSafeBacktest(true)">执行安全回放</button>
        <button class="btn secondary" onclick="copyText(${JSON.stringify(JSON.stringify(backtest, null, 2))})">复制回放摘要</button>
      </div>
    </div>`;
}
function renderRuntime() {
  const services = (dashboard.services || []).map(serviceCard).join('');
  document.getElementById('section-runtime').innerHTML = `
    <div class="card"><div class="section-title">运行控制</div><div class="list">${services}</div></div>`;
}
function renderAll() {
  renderStatusChips();
  renderOverview();
  renderTasks();
  renderPublish();
  renderLogs();
  renderHermes();
  renderRuntime();
}
async function refreshDashboard() {
  try {
    const res = await api('/api/dashboard');
    dashboard = res.data || {};
    renderAll();
  } catch (e) {
    showResult({ok:false, message:String(e)}, true);
  }
}
function setTab(name, el) {
  document.querySelectorAll('.section').forEach(x => x.classList.remove('active'));
  const target = document.getElementById('section-' + name);
  if (target) target.classList.add('active');
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  const btn = el || document.querySelector(`.tab[data-target="${name}"]`);
  if (btn) btn.classList.add('active');
  window.scrollTo({top: 0, behavior: 'smooth'});
}
async function runSafeBacktest(confirmNeeded) {
  if (confirmNeeded && !confirmAction('执行安全回放？这会运行只读检查并占用一点时间。')) return;
  try {
    const res = await api('/api/safe_backtest/run', {method: 'POST'});
    showResult(res, !res.ok);
    await refreshDashboard();
  } catch (e) {
    showResult({ok:false, message:String(e)}, true);
  }
}
async function ackManual(id) {
  try {
    const res = await api('/api/manual_actions/' + encodeURIComponent(id) + '/ack', {method: 'POST'});
    showResult(res, !res.ok);
    await refreshDashboard();
  } catch (e) {
    showResult({ok:false, message:String(e)}, true);
  }
}
async function publishAction(id, action, confirmNeeded) {
  const msgMap = {retry:'重试该发布项？', approve:'批准该发布项？可能会恢复到待发布。', reject:'拒绝该发布项？'};
  if (confirmNeeded && !confirmAction(msgMap[action] || '确认执行？')) return;
  try {
    const res = await api('/api/publish_queue/' + encodeURIComponent(id) + '/' + action, {method: 'POST'});
    showResult(res, !res.ok);
    await refreshDashboard();
  } catch (e) {
    showResult({ok:false, message:String(e)}, true);
  }
}
async function togglePublishQueue(paused) {
  if (!confirmAction(paused ? '暂停发布队列？' : '恢复发布队列？')) return;
  try {
    const res = await api(paused ? '/api/publish_queue/pause' : '/api/publish_queue/resume', {method: 'POST'});
    showResult(res, !res.ok);
    await refreshDashboard();
  } catch (e) {
    showResult({ok:false, message:String(e)}, true);
  }
}
async function serviceAction(name, action, confirmNeeded) {
  const risk = {restart:'重启', stop:'停止', start:'启动', status:'刷新状态'};
  if (confirmNeeded && !confirmAction(`${risk[action] || action} ${name} ？`)) return;
  try {
    const res = await api('/api/processes/' + encodeURIComponent(name) + '/' + action, {method: action === 'status' ? 'GET' : 'POST'});
    showResult(res, !res.ok);
    if (name === 'web_panel' && action === 'restart' && res.ok) {
      setTimeout(() => location.reload(), 2500);
      return;
    }
    await refreshDashboard();
  } catch (e) {
    showResult({ok:false, message:String(e)}, true);
  }
}
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    showResult({ok:true, message:'已复制到剪贴板'});
  } catch {
    showResult({ok:false, message:'复制失败'} , true);
  }
}
refreshDashboard();
setInterval(refreshDashboard, 30000);
</script>
</body>
</html>
"""


class PanelHandler(BaseHTTPRequestHandler):
    server_version = 'AgentHansaPanel/2.0'

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: str, status: int = 200) -> None:
        data = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == '/':
            self._html(INDEX_HTML)
            return
        if path == '/api/dashboard':
            payload, status = _resp(True, _load_dashboard_data(), 'dashboard')
            self._json(payload, status)
            return
        if path == '/api/summary':
            payload, status = _resp(True, _load_dashboard_data().get('summary') or {}, 'summary')
            self._json(payload, status)
            return
        if path == '/api/decision_plan':
            payload, status = _resp(True, _load_decision_plan(), 'decision_plan')
            self._json(payload, status)
            return
        if path == '/api/manual_actions':
            payload, status = _resp(True, _load_manual_actions(), 'manual_actions')
            self._json(payload, status)
            return
        if path == '/api/publish_queue':
            payload, status = _resp(True, _load_publish_queue(), 'publish_queue')
            self._json(payload, status)
            return
        if path == '/api/processes':
            data = {'services': _services_payload(), 'versions': {'hermes': _run_shell('hermes --version', 10), 'python': _run_shell('python --version', 10)}}
            payload, status = _resp(True, data, 'processes')
            self._json(payload, status)
            return
        if path.startswith('/api/processes/') and path.endswith('/status'):
            name = path.split('/')[3]
            payload, status = _service_control(name, 'status')
            self._json(payload, status)
            return
        if path == '/api/redpacket/status':
            data = {'latest': _load_latest().get('red_packet') or {}, 'state': _load_redpacket_state()}
            payload, status = _resp(True, data, 'redpacket_status')
            self._json(payload, status)
            return
        if path == '/api/risk_submissions':
            data = {'items': ((_load_latest().get('submissions') or {}).get('risky_rows') or [])}
            payload, status = _resp(True, data, 'risk_submissions')
            self._json(payload, status)
            return
        if path == '/api/logs/main':
            lines = int((query.get('lines') or ['160'])[0])
            payload, status = _resp(True, {'content': _tail(LOG_DIR / 'agenthansa_bot.log', lines), 'lines': lines}, 'main_log')
            self._json(payload, status)
            return
        if path == '/api/logs/redpacket':
            lines = int((query.get('lines') or ['160'])[0])
            payload, status = _resp(True, {'content': _tail(LOG_DIR / 'redpacket_watch.stdout.log', lines), 'lines': lines}, 'redpacket_log')
            self._json(payload, status)
            return
        self._json({'ok': False, 'message': 'not_found', 'data': {}}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == '/api/safe_backtest/run' or path == '/api/action/safe_backtest':
            payload, status = _safe_backtest_run()
            self._json(payload, status)
            return
        if path == '/api/publish_queue/pause':
            payload, status = _set_publish_paused(True)
            self._json(payload, status)
            return
        if path == '/api/publish_queue/resume':
            payload, status = _set_publish_paused(False)
            self._json(payload, status)
            return
        if path.startswith('/api/manual_actions/') and path.endswith('/ack'):
            action_id = path.split('/')[3]
            payload, status = _update_manual_action(action_id)
            self._json(payload, status)
            return
        if path.startswith('/api/publish_queue/'):
            parts = path.split('/')
            if len(parts) >= 5:
                item_id = parts[3]
                action = parts[4]
                payload, status = _update_publish_item(item_id, action)
                self._json(payload, status)
                return
        if path.startswith('/api/processes/'):
            parts = path.split('/')
            if len(parts) >= 5:
                name = parts[3]
                action = parts[4]
                if name == 'web_panel' and action == 'restart':
                    payload, status = _resp(True, {'scheduled': True}, '面板正在重启')
                    self._json(payload, status)
                    host, port = self.server.server_address
                    _schedule_self_restart(host, port)
                    return
                payload, status = _service_control(name, action)
                self._json(payload, status)
                return
        self._json({'ok': False, 'message': 'not_found', 'data': {}}, HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description='Mobile-friendly AgentHansa/Hermes web panel')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), PanelHandler)
    print(f'Panel running on http://{args.host}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
