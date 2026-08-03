from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/stats")
def stats(request: Request):
    """Ingest counters, visible without clicking (PLAN.md Phase 5 item 7)."""
    app_state = request.app.state.app_state
    counters = app_state.counters.snapshot()
    subjects_tracked = app_state.engine_deps.queue_repo.count() + app_state.engine_deps.agent_repo.count()
    return {**counters, "subjects_tracked": subjects_tracked}
