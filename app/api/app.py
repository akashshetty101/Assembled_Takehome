import sqlite3
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from app.clock import Clock, SystemClock
from app.config import Settings
from app.domain.facts import FactRegistry
from app.engine.engine import EngineDeps
from app.evaluation.registry import build_registry
from app.ingest.counters import Counters
from app.routing.channels import Channel
from app.routing.console_channel import ConsoleChannel
from app.routing.inbox_channel import InboxChannel
from app.scheduling.scan import ScanScheduler
from app.storage.db import connect, migrate
from app.storage.sqlite.counters import SqliteCountersRepository
from app.storage.sqlite.episodes import SqliteEpisodesRepository
from app.storage.sqlite.events import SqliteEventsRepository
from app.storage.sqlite.notifications import SqliteNotificationsRepository
from app.storage.sqlite.rules import SqliteRulesRepository
from app.storage.sqlite.subject_state import SqliteAgentStateRepository, SqliteQueueStateRepository


@dataclass
class AppState:
    conn: sqlite3.Connection
    settings: Settings
    clock: Clock
    registry: FactRegistry
    engine_deps: EngineDeps
    scheduler: ScanScheduler
    counters: Counters


def build_app_state(
    settings: Settings | None = None,
    conn: sqlite3.Connection | None = None,
    clock: Clock | None = None,
    channels: list[Channel] | None = None,
) -> AppState:
    """Explicit DI, no globals -- tests construct an app against a temp DB
    by passing their own settings/conn/clock."""
    settings = settings or Settings()
    conn = conn or connect(settings)
    migrate(conn, settings)
    clock = clock or SystemClock()
    registry = build_registry()

    rules_repo = SqliteRulesRepository(conn)
    episodes_repo = SqliteEpisodesRepository(conn)
    queue_repo = SqliteQueueStateRepository(conn)
    agent_repo = SqliteAgentStateRepository(conn)
    notifications_repo = SqliteNotificationsRepository(conn)
    counters = Counters(SqliteCountersRepository(conn))

    engine_deps = EngineDeps(
        conn=conn, rules_repo=rules_repo, episodes_repo=episodes_repo,
        queue_repo=queue_repo, agent_repo=agent_repo, registry=registry,
        channels=channels if channels is not None else [ConsoleChannel(), InboxChannel()],
        notifications_repo=notifications_repo, counters=counters,
    )
    scheduler = ScanScheduler(episodes_repo, rules_repo, queue_repo, agent_repo)

    return AppState(
        conn=conn, settings=settings, clock=clock, registry=registry,
        engine_deps=engine_deps, scheduler=scheduler, counters=counters,
    )


def create_app(state: AppState | None = None) -> FastAPI:
    state = state or build_app_state()
    app = FastAPI(title="Intraday Notification System")
    app.state.app_state = state
    app.state.templates = Jinja2Templates(directory="app/api/templates")

    from app.api.routes_notifications import router as notifications_router
    from app.api.routes_rules import router as rules_router
    from app.api.routes_stats import router as stats_router

    app.include_router(rules_router)
    app.include_router(notifications_router)
    app.include_router(stats_router)
    return app
