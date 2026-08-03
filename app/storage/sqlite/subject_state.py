import json
import sqlite3

from app.domain.subjects import AgentState, QueueState, ViolationStartSource


class SqliteQueueStateRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, queue_id: str) -> QueueState | None:
        row = self._conn.execute(
            "SELECT * FROM queue_state WHERE queue_id = ?", (queue_id,)
        ).fetchone()
        return _queue_from_row(row) if row else None

    def put(self, state: QueueState) -> None:
        """Does not commit; wrap in storage.db.transaction()."""
        self._conn.execute(
            """
            INSERT INTO queue_state (queue_id, tickets_waiting, longest_wait_sec, sla_target_sec,
                                      agents_available, agents_on_call, volume_last_15m,
                                      volume_forecast_next_15m, last_event_ts)
            VALUES (:queue_id, :tickets_waiting, :longest_wait_sec, :sla_target_sec,
                    :agents_available, :agents_on_call, :volume_last_15m,
                    :volume_forecast_next_15m, :last_event_ts)
            ON CONFLICT(queue_id) DO UPDATE SET
                tickets_waiting = excluded.tickets_waiting,
                longest_wait_sec = excluded.longest_wait_sec,
                sla_target_sec = excluded.sla_target_sec,
                agents_available = excluded.agents_available,
                agents_on_call = excluded.agents_on_call,
                volume_last_15m = excluded.volume_last_15m,
                volume_forecast_next_15m = excluded.volume_forecast_next_15m,
                last_event_ts = excluded.last_event_ts
            """,
            {
                "queue_id": state.queue_id,
                "tickets_waiting": state.tickets_waiting,
                "longest_wait_sec": state.longest_wait_sec,
                "sla_target_sec": state.sla_target_sec,
                "agents_available": state.agents_available,
                "agents_on_call": state.agents_on_call,
                "volume_last_15m": state.volume_last_15m,
                "volume_forecast_next_15m": state.volume_forecast_next_15m,
                "last_event_ts": state.last_event_ts.isoformat(),
            },
        )

    def list(self) -> list[QueueState]:
        rows = self._conn.execute("SELECT * FROM queue_state").fetchall()
        return [_queue_from_row(r) for r in rows]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM queue_state").fetchone()
        return row["c"]


class SqliteAgentStateRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, agent_id: str) -> AgentState | None:
        row = self._conn.execute(
            "SELECT * FROM agent_state WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        return _agent_from_row(row) if row else None

    def put(self, state: AgentState) -> None:
        """Does not commit; wrap in storage.db.transaction()."""
        self._conn.execute(
            """
            INSERT INTO agent_state (agent_id, current_state, state_entered_at, queue_ids_json,
                                      violation_active, violation_started_at, violation_start_source,
                                      last_event_ts)
            VALUES (:agent_id, :current_state, :state_entered_at, :queue_ids_json,
                    :violation_active, :violation_started_at, :violation_start_source, :last_event_ts)
            ON CONFLICT(agent_id) DO UPDATE SET
                current_state = excluded.current_state,
                state_entered_at = excluded.state_entered_at,
                queue_ids_json = excluded.queue_ids_json,
                violation_active = excluded.violation_active,
                violation_started_at = excluded.violation_started_at,
                violation_start_source = excluded.violation_start_source,
                last_event_ts = excluded.last_event_ts
            """,
            {
                "agent_id": state.agent_id,
                "current_state": state.current_state,
                "state_entered_at": (
                    state.state_entered_at.isoformat() if state.state_entered_at else None
                ),
                "queue_ids_json": json.dumps(state.queue_ids),
                "violation_active": int(state.violation_active),
                "violation_started_at": (
                    state.violation_started_at.isoformat() if state.violation_started_at else None
                ),
                "violation_start_source": state.violation_start_source.value,
                "last_event_ts": state.last_event_ts.isoformat(),
            },
        )

    def list(self) -> list[AgentState]:
        rows = self._conn.execute("SELECT * FROM agent_state").fetchall()
        return [_agent_from_row(r) for r in rows]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM agent_state").fetchone()
        return row["c"]


def _queue_from_row(row: sqlite3.Row) -> QueueState:
    return QueueState(
        queue_id=row["queue_id"],
        tickets_waiting=row["tickets_waiting"],
        longest_wait_sec=row["longest_wait_sec"],
        sla_target_sec=row["sla_target_sec"],
        agents_available=row["agents_available"],
        agents_on_call=row["agents_on_call"],
        volume_last_15m=row["volume_last_15m"],
        volume_forecast_next_15m=row["volume_forecast_next_15m"],
        last_event_ts=row["last_event_ts"],
    )


def _agent_from_row(row: sqlite3.Row) -> AgentState:
    return AgentState(
        agent_id=row["agent_id"],
        current_state=row["current_state"],
        state_entered_at=row["state_entered_at"],
        queue_ids=json.loads(row["queue_ids_json"]),
        violation_active=bool(row["violation_active"]),
        violation_started_at=row["violation_started_at"],
        violation_start_source=ViolationStartSource(row["violation_start_source"]),
        last_event_ts=row["last_event_ts"],
    )
