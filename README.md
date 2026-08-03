# Intraday Notification System

A rule-driven notification system for contact center operations. Team leads write rules against live queue and agent state; notifications route to different recipients (the rule author, individual agents, or channels).

## Quick Start

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest

# Seed example rules
python -m app.cli.seed

# Replay sample data through the engine
python -m app.cli.replay Sample_Data/events.jsonl --speed 0

# Start API server
uvicorn "app.api.app:create_app" --factory --reload
# Open http://127.0.0.1:8000/notifications
```

## How It Works

**Rules** define conditions (e.g., "SLA breached", "agent out of adherence"). Each rule has:
- **Conditions**: facts compared with operators (e.g., `tickets_waiting > 20`)
- **Duration**: how long a condition must be true before alerting (`for_duration_sec`)
- **Cooldown**: minimum time between repeated alerts (`cooldown_sec`)
- **Recipient**: who gets notified (rule author, individual agent, or channel)

**Episodes** track rule state:
- **CLEAR**: condition not active
- **PENDING**: condition true but waiting for duration to elapse
- **OPEN**: duration met, alert active

**Transitions**:
- CLEAR → PENDING → OPEN (when condition becomes true)
- OPEN → CLEAR (when condition becomes false)

## Key Design Decisions

| Aspect | Design |
|--------|--------|
| **Memory** | O(subjects), not O(events) — one state row per queue/agent |
| **Evaluation** | Both event-triggered and scheduled (for duration-based rules) |
| **Concurrency** | Single-threaded v1 with enforced unique index preventing double-evaluation |
| **Resolution** | Partial three-valued logic (Phase 9): if a driving fact goes missing, freeze the episode as stale instead of resolving |

## What's Not Included

- Forecast/volume-deviation rules (data unreliable in sample)
- Ack/snooze (cooldown gets 80% benefit with less surface)
- Rule-builder UI (clean schema ships; UI can layer on top later)
- Real Slack/email/push (out of scope; `InboxChannel` + `ConsoleChannel` included)
