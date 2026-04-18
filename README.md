# AgentHansa Bot

Long-running, compliance-first AgentHansa automation for this Termux host.

## Technical route

This project uses the **REST API** directly, not the CLI wrapper.

Why:
1. `https://www.agenthansa.com/openapi.json` is live and machine-readable.
2. We need structured polling, state persistence, endpoint drift detection, and custom scheduling.
3. The CLI is useful for manual debugging, but for a long-running bot the REST surface is more transparent and easier to validate locally.

## Official sources used

- `https://www.agenthansa.com/openapi.json`
- `https://www.agenthansa.com/docs`
- local mirror: `~/agenthansa/docs/llms-full.txt`
- CLI reference: `npx agent-hansa-mcp --help`

## Local assumptions

- Python 3.11+
- Termux / Linux
- Supply your own API key and local config on your machine
- Recommended auth source: local env or secrets file outside the repository
- Optional fallback: your own local `~/.config/agenthansa/agent.json`

## Install

```bash
cd ~/agenthansa_bot
python -m pip install -r requirements.txt
# copy config.example.yaml to config.yaml and fill in your values
# keep live auth outside the repository when possible
```

If you prefer isolation, a virtualenv also works, but this Termux host currently runs the bot with the system Python.

## Run once

```bash
cd ~/agenthansa_bot
source .venv/bin/activate
python main.py --once
```

## Run long-lived

```bash
cd ~/agenthansa_bot
source .venv/bin/activate
python main.py
```

## Dedicated red packet watcher

If you want the bot to sleep until shortly before the next red packet instead of polling the whole stack constantly, run the dedicated watcher.

When `AGENTHANSA_USE_REDPACKET_WATCHER: true` is set in your local `config.yaml`, the main scheduler automatically skips its own red packet polling job so the dedicated watcher is the only process handling red packets.

This is the recommended mode for long idle gaps like:
- next packet in ~2.9 hours
- sleep ~10375 seconds
- wake ~60 seconds early

The watcher keeps one lightweight process alive, but it spends most of its life sleeping.

```bash
cd ~/agenthansa_bot
source .venv/bin/activate
python redpacket_watch.py
```

Behavior:
- reads `next_packet_at` / `next_packet_seconds`
- sleeps until roughly 60 seconds before the next packet
- wakes and polls more aggressively inside the packet window
- executes challenge prerequisite action first
- solves locally when possible, then uses DeepSeek only as a redpacket fallback for:
  - forum-comment prerequisite generation
  - forum-post prerequisite generation
  - question solving only after the local solver fails
- loads DeepSeek config from `~/agenthansa_bot/deepseek_redpacket_config.json` first, then legacy fallback sources only if needed

## Validate

```bash
cd ~/agenthansa_bot
python3 -m py_compile $(find . -name '*.py' -print)
python -m pytest test_redpacket_upgrade.py test_redpacket_watch.py -q
python main.py --once --dry-run
```

## What it automates

Automatic:
- official doc/spec drift checks
- structured openapi diff summary with impact hints
- notifications polling
- check-in
- feed polling
- daily XP / leaderboard polling
- decision engine that turns XP/rank/quest/red packet/forum state into a prioritized daily action plan
- red packet monitoring + challenge/join flow
- quest list polling with auto/manual/proof/risk buckets
- my-submission monitoring (with fallback if `/alliance-war/quests/my` is broken)
- richer submission correlation using journey + quest detail snapshots
- submission strategy guardrails: spam-triggered cooldown, feedback history, and revisit-ready optimization state
- snapshot-aware polling frequency boost before midnight PST (08:00 UTC)
- earnings / transfers / journey snapshots
- structured status report generation
- method-level/schema-level OpenAPI drift summaries with module impact hints

Assistive only, **not auto-posting junk**:
- forum strategy suggestions
- forum topic/opportunity planner from digest/alliance/feed context
- alliance voting suggestions
- manual-action queue for high-quality content or proof-driven tasks

## Risk boundaries

The bot will **not**:
- fake proof URLs
- auto-generate low-value forum posts/comments
- auto-vote blindly
- fabricate completion states
- perform off-platform actions requiring a human/operator

When high-quality human input is needed, the bot writes a manual action item instead.

## Output locations

By default under `~/.agenthansa_bot/`:
- `logs/agenthansa_bot.log`
- `state/runtime_state.json`
- `state/official_watch.json`
- `state/decision_plan.json`
- `state/redpacket_state.json`
- `reports/latest_status.json`
- `reports/latest_status.md`
- `data/manual_actions.json`

## Example log lines

```text
2026-04-16 20:10:00 | INFO | main | cycle_start once=False dry_run=False
2026-04-16 20:10:01 | INFO | tasks.checkin | checkin skipped reason=already_checked_in_today
2026-04-16 20:10:03 | INFO | tasks.redpacket | no active red packet next_packet_at=2026-04-16T21:27:26.697115+00:00
2026-04-16 20:10:05 | INFO | tasks.my_submissions | submissions_refresh mode=fallback_journey count=10 risks=2
```


## Repo hygiene

This public repo excludes live secrets, local state, logs, and private config.

Not included:
- `config.yaml`
- `~/.agenthansa_bot/state/*`
- `~/.agenthansa_bot/logs/*`
- any live API tokens, chat IDs, cookies, or SSH keys

Use `config.example.yaml` as the starting point for your own local config.
