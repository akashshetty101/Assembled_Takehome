from app.ingest.normalize import AgentStateValue, normalize_agent_state, normalize_queue_ids


def test_normalize_queue_ids_none_becomes_empty_list():
    assert normalize_queue_ids(None) == []


def test_normalize_queue_ids_preserves_list():
    assert normalize_queue_ids(["billing", "vip"]) == ["billing", "vip"]


def test_normalize_queue_ids_empty_list_stays_empty():
    """a_05 lines 73/74: both must normalize to [], never None, either way."""
    assert normalize_queue_ids([]) == []


def test_normalize_agent_state_passes_known_states_through():
    for raw in ("available", "on_call", "on_break", "in_meeting", "offline"):
        assert normalize_agent_state(raw) == raw


def test_normalize_agent_state_falls_back_to_unknown():
    assert normalize_agent_state("doing_a_backflip") == AgentStateValue.UNKNOWN


def test_normalize_agent_state_passes_none_through():
    """previous_state is null on an agent's very first event -- distinct
    from an unrecognized string, so it must stay None, not become 'unknown'."""
    assert normalize_agent_state(None) is None
